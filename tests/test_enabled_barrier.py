import configparser
import importlib.util
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


def make_config(enabled=True, autoreload=False):
    config = configparser.ConfigParser()
    config.add_section('GENERAL')
    config.set('GENERAL', 'Enabled', str(enabled))
    config.set('GENERAL', 'Autoreload', str(autoreload))
    config.add_section('AC')
    config.set('AC', 'Update_Rate_s', '0.001')
    return config


class StopAfterWait:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.stopped = True


class EnabledBarrierTests(unittest.TestCase):
    def test_initially_disabled_config_exits_before_hardware_checks(self):
        throttled = load_throttled()
        config = make_config(enabled=False)
        parsed_args = SimpleNamespace(log=None, debug=False, config='/tmp/throttled.conf', monitor=None, force=False)
        parser = mock.Mock()
        parser.parse_args.return_value = parsed_args

        with mock.patch.object(throttled, 'build_arg_parser', return_value=parser):
            with mock.patch.object(throttled, 'load_config', return_value=config):
                with mock.patch.object(throttled, 'check_kernel') as check_kernel:
                    with mock.patch.object(throttled, 'check_cpu') as check_cpu:
                        with mock.patch.object(throttled, 'set_msr_allow_writes') as allow_writes:
                            with mock.patch.object(throttled, 'test_msr_rw_capabilities') as test_msr:
                                throttled.main()

        check_kernel.assert_not_called()
        check_cpu.assert_not_called()
        allow_writes.assert_not_called()
        test_msr.assert_not_called()

    def test_disabled_config_blocks_resume_writes(self):
        throttled = load_throttled()
        config = make_config(enabled=False)

        with mock.patch.object(throttled, 'undervolt') as undervolt:
            with mock.patch.object(throttled, 'set_icc_max') as set_icc_max:
                throttled.handle_sleep_prepare(False, config)

        undervolt.assert_not_called()
        set_icc_max.assert_not_called()
        self.assertFalse(throttled.should_listen_for_resume(config))

    def test_disabled_reload_does_not_calculate_or_apply_hardware_settings(self):
        throttled = load_throttled()
        config = make_config(enabled=False, autoreload=True)

        with mock.patch.object(throttled, 'load_config', return_value=config):
            with mock.patch.object(throttled, 'calc_reg_values') as calc_reg_values:
                with mock.patch.object(throttled, 'undervolt') as undervolt:
                    with mock.patch.object(throttled, 'set_icc_max') as set_icc_max:
                        with mock.patch.object(throttled, 'set_hwp') as set_hwp:
                            reloaded_config, regs = throttled.reload_config()

        self.assertIs(reloaded_config, config)
        self.assertEqual(dict(regs), {})
        calc_reg_values.assert_not_called()
        undervolt.assert_not_called()
        set_icc_max.assert_not_called()
        set_hwp.assert_not_called()

    def test_enabled_to_disabled_reload_is_an_immediate_write_barrier(self):
        throttled = load_throttled()
        throttled.args.debug = True
        old_config = make_config(enabled=True, autoreload=True)
        new_config = make_config(enabled=False, autoreload=True)
        state = {'config': old_config, 'regs': {'AC': {'MSR_PKG_POWER_LIMIT': 0x1234}}}

        with mock.patch.object(throttled, 'read_mchbar_base', return_value=0):
            with mock.patch.object(throttled, 'MMIO', side_effect=throttled.MMIOError):
                with mock.patch.object(throttled, 'warning'):
                    with mock.patch.object(throttled, 'get_config_write_time', side_effect=(1, 2)):
                        with mock.patch.object(
                            throttled, 'reload_config', return_value=(new_config, defaultdict(dict))
                        ):
                            with mock.patch.object(throttled, 'writemsr') as writemsr:
                                with mock.patch.object(throttled, 'undervolt') as undervolt:
                                    with mock.patch.object(
                                        throttled, 'get_reset_thermal_status'
                                    ) as thermal_status:
                                        throttled._power_thread(state, StopAfterWait(), (6, 158, 13))

        writemsr.assert_not_called()
        undervolt.assert_not_called()
        thermal_status.assert_not_called()


if __name__ == '__main__':
    unittest.main()
