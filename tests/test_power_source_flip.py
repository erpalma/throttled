import configparser
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_throttled():
    spec = importlib.util.spec_from_file_location('throttled_under_test', ROOT / 'throttled.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.args = SimpleNamespace(log=None, debug=False, config='/tmp/throttled.conf', monitor=None)
    module.log_history.clear()
    module.power['source'] = 'AC'
    module.power['method'] = 'dbus'
    return module


def make_config(sections):
    config = configparser.ConfigParser()
    for section, options in sections.items():
        config.add_section(section)
        for option, value in options.items():
            config.set(section, option, str(value))
    return config


class StopAfterWait:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.stopped = True


class PowerSourceFlipTests(unittest.TestCase):
    def test_resume_listener_checks_the_planes_each_key_actually_supports(self):
        throttled = load_throttled()

        self.assertFalse(throttled.should_listen_for_resume(make_config({'ICCMAX.AC': {'UNCORE': 200}})))
        self.assertTrue(throttled.should_listen_for_resume(make_config({'ICCMAX.AC': {'CORE': 200}})))
        self.assertTrue(throttled.should_listen_for_resume(make_config({'UNDERVOLT.AC': {'ANALOGIO': -50}})))

    def test_set_icc_max_honors_an_explicit_power_source(self):
        throttled = load_throttled()
        throttled.power['source'] = 'BATTERY'
        config = make_config({'ICCMAX.AC': {'CORE': 100}})

        with mock.patch.object(throttled, 'writemsr') as writemsr:
            with mock.patch.object(throttled, 'warning'):
                throttled.set_icc_max(config, source='AC')

        writemsr.assert_called_once_with('MSR_OC_MAILBOX', throttled.calc_icc_max_msr('CORE', 100))

    def test_power_flip_reapplies_undervolt_and_iccmax(self):
        throttled = load_throttled()
        throttled.power['source'] = 'BATTERY'
        throttled.power['method'] = 'polling'
        config = make_config(
            {
                'GENERAL': {'Autoreload': 'False'},
                'AC': {'Update_Rate_s': '5'},
                'BATTERY': {'Update_Rate_s': '30'},
            }
        )
        state = {'config': config, 'regs': {'AC': {}, 'BATTERY': {}}}
        calls = []

        with mock.patch.object(throttled, 'read_mchbar_base', return_value=0):
            with mock.patch.object(throttled, 'MMIO'):
                with mock.patch.object(throttled, 'is_on_battery', return_value=False):
                    with mock.patch.object(throttled, 'undervolt', lambda config, source=None: calls.append(('undervolt', source))):
                        with mock.patch.object(throttled, 'set_icc_max', lambda config, source=None: calls.append(('iccmax', source))):
                            with mock.patch.object(throttled, 'log'):
                                throttled.power_thread(state, StopAfterWait(), None)

        self.assertEqual(calls, [('undervolt', 'AC'), ('iccmax', 'AC')])
        self.assertEqual(throttled.power['source'], 'AC')


if __name__ == '__main__':
    unittest.main()
