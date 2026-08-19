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
    config.set('GENERAL', 'Enabled', 'True')
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

        def ensure_msr_module(cpu=None):
            if cpu is None:
                return ['/dev/cpu/0/msr', '/dev/cpu/2/msr']
            return f'/dev/cpu/{cpu:d}/msr'

        with mock.patch.object(throttled, '_ensure_msr_module', side_effect=ensure_msr_module):
            with mock.patch.object(throttled.os, 'open', return_value=20) as open_:
                with mock.patch.object(throttled.os, 'read', return_value=struct.pack('Q', 22)):
                    with mock.patch.object(throttled.os, 'lseek'):
                        with mock.patch.object(throttled.os, 'close'):
                            self.assertEqual(throttled.readmsr('MSR_PLATFORM_INFO', cpu=2), 22)

        open_.assert_called_once_with('/dev/cpu/2/msr', throttled.os.O_RDONLY)

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

    def test_mchbar_allowlist_is_keyed_by_pci_device_and_covers_each_sourced_group(self):
        throttled = load_throttled()
        expected = {
            0x5914: throttled.MCHBAR_ADDRESS_MASK_39_15,  # T480/T480s/X1C6
            0x3EC4: throttled.MCHBAR_ADDRESS_MASK_39_15,  # P53
            0x9A14: throttled.MCHBAR_ADDRESS_MASK_39_17,  # Tiger Lake
            0x4601: throttled.MCHBAR_ADDRESS_MASK_42_17,  # Alder Lake
            0xA706: throttled.MCHBAR_ADDRESS_MASK_42_17,  # Raptor Lake
            0x7D21: throttled.MCHBAR_ADDRESS_MASK_42_17,  # Meteor Lake
            0x7D06: throttled.MCHBAR_ADDRESS_MASK_42_17,  # Arrow Lake
        }

        for device_id, layout in expected.items():
            with self.subTest(device_id=device_id):
                self.assertEqual(throttled.MCHBAR_ADDRESS_MASKS_BY_PCI_DEVICE[device_id], layout)

        self.assertTrue(all(isinstance(device_id, int) for device_id in throttled.MCHBAR_ADDRESS_MASKS_BY_PCI_DEVICE))

    def test_host_bridge_identity_is_read_strictly_from_sysfs(self):
        throttled = load_throttled()
        self.assertEqual(throttled.PCI_HOST_BRIDGE_SYSFS_PATH, '/sys/bus/pci/devices/0000:00:00.0')
        contents = {
            f'{throttled.PCI_HOST_BRIDGE_SYSFS_PATH}/vendor': '0x8086\n',
            f'{throttled.PCI_HOST_BRIDGE_SYSFS_PATH}/device': '0x5914\n',
        }

        def sysfs_open(path, encoding):
            self.assertEqual(encoding, 'ascii')
            return mock.mock_open(read_data=contents[path])()

        with mock.patch('builtins.open', side_effect=sysfs_open):
            self.assertEqual(throttled._read_host_bridge_identity(), (0x8086, 0x5914))

    def test_host_bridge_parse_errors_fail_before_any_bar_probe(self):
        throttled = load_throttled()

        for values in (('8086\n', '0x5914\n'), ('0x8086\n', '0x591\n'), ('0x8086\n', 'not-hex\n')):
            with self.subTest(values=values):
                handles = tuple(mock.mock_open(read_data=value)() for value in values)
                with mock.patch('builtins.open', side_effect=handles):
                    with mock.patch.object(throttled, '_read_mchbar_dword') as read:
                        with mock.patch.object(throttled, 'warning'):
                            self.assertIsNone(throttled.read_mchbar_base())
                read.assert_not_called()

        # only the strict regex rejects these: int() would accept both and the
        # second would land on an allowlisted device id
        for raw in ('0X5914\n', '0x05914\n'):
            with self.subTest(raw=raw):
                handles = (mock.mock_open(read_data='0x8086\n')(), mock.mock_open(read_data=raw)())
                with mock.patch('builtins.open', side_effect=handles):
                    self.assertIsNone(throttled._read_host_bridge_identity())

        with mock.patch('builtins.open', side_effect=PermissionError('denied')):
            with mock.patch.object(throttled, '_read_mchbar_dword') as read:
                with mock.patch.object(throttled, 'warning'):
                    self.assertIsNone(throttled.read_mchbar_base())
        read.assert_not_called()

    def test_mchbar_reader_combines_modern_dwords_above_four_gib(self):
        throttled = load_throttled()
        values = {'48.l': b'fedc0001\n', '4c.l': b'00000001\n'}

        def setpci(cmd, stderr):
            self.assertIs(stderr, throttled.PIPE)
            return values[cmd[-1]]

        with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=(0x8086, 0xA706)):
            with mock.patch.object(throttled, 'check_output', side_effect=setpci) as check:
                self.assertEqual(throttled.read_mchbar_base(), 0x1FEDC0000)

        self.assertEqual([call.args[0][-1] for call in check.call_args_list], ['48.l', '4c.l'])

    def test_mchbar_reader_accepts_the_p53_coffee_lake_host_bridge(self):
        throttled = load_throttled()
        values = {'48.l': b'fed10001\n', '4c.l': b'00000000\n'}

        with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=(0x8086, 0x3EC4)):
            with mock.patch.object(throttled, 'check_output', side_effect=lambda cmd, stderr: values[cmd[-1]]):
                self.assertEqual(throttled.read_mchbar_base(), 0xFED10000)

    def test_mchbar_reader_accepts_a_low_nonzero_32k_bar(self):
        throttled = load_throttled()

        with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=(0x8086, 0x3EC4)):
            with mock.patch.object(throttled, '_read_mchbar_dword', side_effect=(0x8001, 0)):
                self.assertEqual(throttled.read_mchbar_base(), 0x8000)

    def test_mchbar_reader_validates_the_tiger_lake_128k_layout(self):
        throttled = load_throttled()

        with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=(0x8086, 0x9A14)):
            with mock.patch.object(throttled, '_read_mchbar_dword', side_effect=(0x20001, 0)):
                self.assertEqual(throttled.read_mchbar_base(), 0x20000)

        with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=(0x8086, 0x9A14)):
            with mock.patch.object(throttled, '_read_mchbar_dword', side_effect=(0x30001, 0) * 2):
                with mock.patch.object(throttled, 'warning'):
                    self.assertIsNone(throttled.read_mchbar_base())

    def test_mchbar_reader_rejects_disabled_zero_and_reserved_bit_bars(self):
        throttled = load_throttled()
        invalid_values = (
            (0xA706, 0xFEDC0000, 0),
            (0xA706, 0x00000001, 0),
            (0xA706, 0xFED1F001, 0),
            (0xA706, 0xFEDC0001, 0x400),
            (0x3EC4, 0xFED14001, 0),
        )

        for device_id, low, high in invalid_values:
            with self.subTest(device_id=device_id, low=low, high=high):
                reads = (low, high, low, high)
                with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=(0x8086, device_id)):
                    with mock.patch.object(throttled, '_read_mchbar_dword', side_effect=reads):
                        with mock.patch.object(throttled, 'warning') as warning:
                            self.assertIsNone(throttled.read_mchbar_base())

                warning.assert_called_once()
                self.assertIn('disabling only MMIO', warning.call_args.args[0])

    def test_mchbar_reader_accepts_a_42_bit_base_above_the_39_bit_range(self):
        throttled = load_throttled()

        with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=(0x8086, 0xA706)):
            with mock.patch.object(throttled, '_read_mchbar_dword', side_effect=(0x00000001, 0x80)):
                self.assertEqual(throttled.read_mchbar_base(), 1 << 39)

    def test_mchbar_reader_rejects_wrong_vendor_and_unknown_device_without_probing(self):
        throttled = load_throttled()

        for identity in ((0x1234, 0x5914), (0x8086, 0xFFFF)):
            with self.subTest(identity=identity):
                with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=identity):
                    with mock.patch.object(throttled, '_read_mchbar_dword') as read:
                        with mock.patch.object(throttled, 'warning') as warning:
                            self.assertIsNone(throttled.read_mchbar_base())

                read.assert_not_called()
                warning.assert_called_once()

    def test_power_thread_gates_mmio_on_the_pci_identity(self):
        throttled = load_throttled()
        config = make_config()
        state = {'config': config, 'regs': {'AC': {'MSR_PKG_POWER_LIMIT': 0x1234}, 'BATTERY': {}}}
        mapping = mock.Mock()

        with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=(0x8086, 0x5914)):
            with mock.patch.object(throttled, '_read_mchbar_dword', side_effect=(0xFED10001, 0)):
                with mock.patch.object(throttled, 'MMIO', return_value=mapping) as mmio:
                    with mock.patch.object(throttled, 'writemsr'):
                        throttled._power_thread(state, StopAfterWait())

        mmio.assert_called_once_with(0xFED159A0, 8)
        mapping.write64.assert_called_once_with(0, 0x1234)

        with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=(0x8086, 0xFFFF)):
            with mock.patch.object(throttled, '_read_mchbar_dword') as read:
                with mock.patch.object(throttled, 'MMIO') as mmio:
                    with mock.patch.object(throttled, 'writemsr') as writemsr:
                        with mock.patch.object(throttled, 'warning'):
                            throttled._power_thread(state, StopAfterWait())

        read.assert_not_called()
        mmio.assert_not_called()
        writemsr.assert_called_once_with('MSR_PKG_POWER_LIMIT', 0x1234)

    def test_mchbar_reader_retries_a_complete_pair_after_either_ecam_read_fails(self):
        throttled = load_throttled()
        values = {'48.l': b'fedc0001\n', '4c.l': b'00000001\n'}

        for failed_register in values:
            with self.subTest(failed_register=failed_register):

                def setpci(cmd, stderr):
                    self.assertIs(stderr, throttled.PIPE)
                    if '-A' in cmd and cmd[-1] == failed_register:
                        raise throttled.CalledProcessError(1, cmd, stderr=b'blocked')
                    return values[cmd[-1]]

                with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=(0x8086, 0xA706)):
                    with mock.patch.object(throttled, 'check_output', side_effect=setpci) as check:
                        self.assertEqual(throttled.read_mchbar_base(), 0x1FEDC0000)

                commands = [call.args[0] for call in check.call_args_list]
                self.assertEqual([command[-1] for command in commands], ['48.l', '4c.l', '48.l', '4c.l'])
                self.assertEqual(['-A' in command for command in commands], [True, True, False, False])
                for command in commands:
                    self.assertEqual(command[command.index('-s') + 1], '0000:00:00.0')

    def test_mchbar_reader_rejects_invalid_setpci_output_and_execution_errors(self):
        throttled = load_throttled()

        for output in (b'1\n', b'0xfedc0001\n', b'not-a-hex-dword\n', b'100000000\n'):
            with self.subTest(output=output):
                with mock.patch.object(throttled, 'check_output', return_value=output):
                    self.assertIsNone(throttled._read_mchbar_dword('ecam'))

        errors = (
            PermissionError('denied'),
            throttled.CalledProcessError(1, ('setpci',), stderr=b'blocked'),
        )
        for error in errors:
            with self.subTest(error=error):
                with mock.patch.object(throttled, 'check_output', side_effect=error):
                    self.assertIsNone(throttled._read_mchbar_dword('ecam'))

    def test_invalid_mchbar_never_opens_dev_mem_and_keeps_msr_writes(self):
        throttled = load_throttled()
        config = make_config()
        state = {'config': config, 'regs': {'AC': {'MSR_PKG_POWER_LIMIT': 0x1234}, 'BATTERY': {}}}
        values = {'48.l': b'fed1f001\n', '4c.l': b'00000000\n'}

        with mock.patch.object(throttled, '_read_host_bridge_identity', return_value=(0x8086, 0xA706)):
            with mock.patch.object(throttled, 'check_output', side_effect=lambda cmd, stderr: values[cmd[-1]]) as check:
                with mock.patch.object(throttled, 'MMIO') as mmio:
                    with mock.patch.object(throttled, 'writemsr') as writemsr:
                        with mock.patch.object(throttled, 'warning'):
                            throttled._power_thread(state, StopAfterWait())

        self.assertEqual(check.call_count, 4)
        mmio.assert_not_called()
        writemsr.assert_called_once_with('MSR_PKG_POWER_LIMIT', 0x1234)

    def test_mchbar_mapping_uses_the_clean_base_and_documented_offset(self):
        throttled = load_throttled()
        config = make_config()
        state = {'config': config, 'regs': {'AC': {'MSR_PKG_POWER_LIMIT': 0x1234}, 'BATTERY': {}}}
        mapping = mock.Mock()

        with mock.patch.object(throttled, 'read_mchbar_base', return_value=0xFED10000):
            with mock.patch.object(throttled, 'MMIO', return_value=mapping) as mmio:
                with mock.patch.object(throttled, 'writemsr'):
                    throttled._power_thread(state, StopAfterWait())

        mmio.assert_called_once_with(0xFED159A0, 8)
        mapping.write64.assert_called_once_with(0, 0x1234)

    def test_mchbar_write_error_disables_only_the_mmio_path(self):
        throttled = load_throttled()
        config = make_config()
        state = {'config': config, 'regs': {'AC': {'MSR_PKG_POWER_LIMIT': 0x1234}, 'BATTERY': {}}}
        mapping = mock.Mock()
        mapping.write64.side_effect = throttled.MMIOError('blocked')

        with mock.patch.object(throttled, 'read_mchbar_base', return_value=0xFED10000):
            with mock.patch.object(throttled, 'MMIO', return_value=mapping):
                with mock.patch.object(throttled, 'writemsr') as writemsr:
                    with mock.patch.object(throttled, 'warning') as warning:
                        throttled._power_thread(state, StopAfterWait())

        writemsr.assert_called_once_with('MSR_PKG_POWER_LIMIT', 0x1234)
        mapping.write64.assert_called_once_with(0, 0x1234)
        self.assertIn('disabling only MMIO writes', warning.call_args.args[0])

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
                        throttled.power_thread(state, StopAfterWait())

        self.assertIs(state['config'], new_config)
        self.assertIs(state['regs'], new_regs)

    def test_sleep_callback_reads_current_config_from_state(self):
        throttled = load_throttled()
        updated_config = make_config()
        state = {'config': updated_config}
        calls = []

        with mock.patch.object(throttled, 'undervolt', lambda config: calls.append(('undervolt', config))):
            with mock.patch.object(throttled, 'set_icc_max', lambda config: calls.append(('iccmax', config))):
                throttled.handle_sleep_prepare(False, state)

        self.assertEqual(calls, [('undervolt', updated_config), ('iccmax', updated_config)])


if __name__ == '__main__':
    unittest.main()
