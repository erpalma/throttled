import importlib.util
import io
import struct
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


def make_config(autoreload=False, update_rate='0.001', trip_temp=None):
    config = __import__('configparser').ConfigParser()
    config.add_section('GENERAL')
    config.set('GENERAL', 'Autoreload', 'True' if autoreload else 'False')
    for section in ('AC', 'BATTERY'):
        config.add_section(section)
        config.set(section, 'Update_Rate_s', update_rate)
        if trip_temp is not None:
            config.set(section, 'Trip_Temp_C', str(trip_temp))
    return config


class StopAfterWait:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.stopped = True


class PR394FixTests(unittest.TestCase):
    def test_log_history_only_remembers_oneshot_messages(self):
        throttled = load_throttled()

        with mock.patch('sys.stdout', new=io.StringIO()):
            throttled.log('status 1')
            throttled.log('status 2')
            throttled.log('dedupe me', oneshot=True)
            throttled.log('dedupe me', oneshot=True)

        self.assertEqual(throttled.log_history, {'dedupe me'})

    def test_msr_devices_can_be_missing_before_modprobe(self):
        throttled = load_throttled()

        with mock.patch.object(throttled, 'get_msr_list', side_effect=[[], ['/dev/cpu/0/msr']]):
            with mock.patch.object(throttled.os.path, 'exists', return_value=True):
                with mock.patch.object(throttled.subprocess, 'check_call') as check_call:
                    self.assertEqual(throttled._ensure_msr_module(), ['/dev/cpu/0/msr'])

        check_call.assert_called_once_with(('modprobe', 'msr'))

    def test_readmsr_cpu_argument_is_cpu_number_not_list_index(self):
        throttled = load_throttled()
        throttled.cpu_count = lambda: 2

        with mock.patch.object(throttled, '_ensure_msr_module', return_value=['/dev/cpu/0/msr', '/dev/cpu/2/msr']):
            with mock.patch.object(throttled.os, 'open', side_effect=[10, 20]):
                with mock.patch.object(throttled.os, 'read', side_effect=[struct.pack('Q', 11), struct.pack('Q', 22)]):
                    with mock.patch.object(throttled.os, 'lseek'):
                        with mock.patch.object(throttled.os, 'close'):
                            self.assertEqual(throttled.readmsr('MSR_PLATFORM_INFO', cpu=2), 22)

    def test_thermal_status_uses_values_read_from_available_msr_devices(self):
        throttled = load_throttled()

        with mock.patch.object(throttled, 'readmsr', return_value=[0, 1]):
            with mock.patch.object(throttled, 'writemsr'):
                status = throttled.get_reset_thermal_status()

        self.assertEqual(len(status), 2)

    def test_trip_temperature_is_clamped_after_critical_temperature_is_known(self):
        throttled = load_throttled()
        throttled.TRIP_TEMP_RANGE = [40, 97]

        with mock.patch.object(throttled, 'get_critical_temp', return_value=95):
            with mock.patch.object(throttled, 'get_power_unit', return_value=1):
                with mock.patch.object(throttled, 'log'):
                    regs = throttled.calc_reg_values(
                        {'feature_programmable_temperature_target': 1, 'feature_programmable_tdp_limit': 0},
                        make_config(trip_temp=94),
                    )

        self.assertEqual(regs['AC']['MSR_TEMPERATURE_TARGET'], 3 << 24)
        self.assertEqual(regs['BATTERY']['MSR_TEMPERATURE_TARGET'], 3 << 24)

    def test_power_unit_is_not_read_when_package_power_limits_are_disabled(self):
        throttled = load_throttled()

        with mock.patch.object(throttled, 'get_power_unit', side_effect=AssertionError('unused')):
            with mock.patch.object(throttled, 'log'):
                with mock.patch.object(throttled, 'warning'):
                    regs = throttled.calc_reg_values(
                        {'feature_programmable_temperature_target': 0, 'feature_programmable_tdp_limit': 0},
                        make_config(),
                    )

        self.assertEqual(dict(regs), {})

    def test_mchbar_reader_rejects_invalid_setpci_values_before_guessing(self):
        throttled = load_throttled()

        with mock.patch.object(throttled, '_read_mchbar_dword', side_effect=[0xFFFFFFFF, 0xFED10001]) as read:
            self.assertEqual(throttled.read_mchbar_base((6, 142, 9)), 0xFED10001)

        self.assertEqual([call.args[0] for call in read.call_args_list], ['ecam', None])

    def test_power_thread_autoreload_updates_shared_state_for_dbus_callbacks(self):
        throttled = load_throttled()
        old_config = make_config(autoreload=True)
        new_config = make_config(autoreload=True)
        state = {'config': old_config, 'regs': {'AC': {}, 'BATTERY': {}}}
        new_regs = {'AC': {}, 'BATTERY': {}}

        with mock.patch.object(throttled, 'read_mchbar_base', return_value=0):
            with mock.patch.object(throttled, 'MMIO'):
                with mock.patch.object(throttled, 'get_config_write_time', side_effect=[1, 2]):
                    with mock.patch.object(throttled, 'reload_config', return_value=(new_config, new_regs)):
                        throttled.power_thread(state, StopAfterWait(), None)

        self.assertIs(state['config'], new_config)
        self.assertIs(state['regs'], new_regs)

    def test_sleep_callback_reads_current_config_from_state(self):
        throttled = load_throttled()
        state = {'config': 'updated-config'}
        calls = []

        with mock.patch.object(throttled, 'undervolt', lambda config: calls.append(('undervolt', config))):
            with mock.patch.object(throttled, 'set_icc_max', lambda config: calls.append(('iccmax', config))):
                throttled.handle_sleep_prepare(False, state)

        self.assertEqual(calls, [('undervolt', 'updated-config'), ('iccmax', 'updated-config')])


if __name__ == '__main__':
    unittest.main()
