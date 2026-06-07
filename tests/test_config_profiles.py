import importlib.util
import os
import tempfile
import unittest
from collections import defaultdict
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


def write_config(contents):
    config_file = tempfile.NamedTemporaryFile('w', delete=False)
    with config_file:
        config_file.write(contents)
    return config_file.name


class StopAfterWait:
    def __init__(self):
        self.timeout = None
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.timeout = timeout
        self.stopped = True


class ConfigProfileTests(unittest.TestCase):
    def test_load_config_accepts_single_power_profile(self):
        throttled = load_throttled()
        config_path = write_config(
            '[GENERAL]\n'
            'Enabled: True\n'
            '\n'
            '[AC]\n'
            'Update_Rate_s: 5\n'
            'Trip_Temp_C: 95\n'
        )
        self.addCleanup(os.unlink, config_path)
        throttled.args.config = config_path

        config = throttled.load_config()

        self.assertIn('AC', config)
        self.assertNotIn('BATTERY', config)

    def test_calc_reg_values_skips_missing_power_profile(self):
        throttled = load_throttled()
        config = throttled.configparser.ConfigParser()
        config.add_section('AC')
        config.set('AC', 'Update_Rate_s', '5')
        config.set('AC', 'Trip_Temp_C', '90')

        with mock.patch.object(throttled, 'get_critical_temp', return_value=100):
            regs = throttled.calc_reg_values(
                {'feature_programmable_temperature_target': 1, 'feature_programmable_tdp_limit': 0},
                config,
            )

        self.assertEqual(regs['AC']['MSR_TEMPERATURE_TARGET'], 10 << 24)
        self.assertNotIn('BATTERY', regs)

    def test_power_thread_uses_configured_update_rate_when_current_profile_is_missing(self):
        throttled = load_throttled()
        throttled.power['source'] = 'BATTERY'
        config = throttled.configparser.ConfigParser()
        config.add_section('GENERAL')
        config.set('GENERAL', 'Autoreload', 'False')
        config.add_section('AC')
        config.set('AC', 'Update_Rate_s', '5')
        state = {'config': config, 'regs': defaultdict(dict)}
        exit_event = StopAfterWait()

        with mock.patch.object(throttled, 'read_mchbar_base', return_value=0):
            with mock.patch.object(throttled, 'MMIO'):
                throttled.power_thread(state, exit_event, None)

        self.assertEqual(exit_event.timeout, 5.0)


if __name__ == '__main__':
    unittest.main()
