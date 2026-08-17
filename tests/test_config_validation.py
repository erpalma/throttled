import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
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


class StopAfterWait:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.stopped = True


class ConfigValidationTests(unittest.TestCase):
    def write_config(self, text):
        tmp = tempfile.NamedTemporaryFile('w', suffix='.conf', delete=False)
        tmp.write(text)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_load_config_survives_a_partial_undervolt_section(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n[UNDERVOLT]\nCORE: -50\n'
        )

        with mock.patch.object(throttled, 'warning'):
            config = throttled.load_config()

        self.assertEqual(config.getfloat('UNDERVOLT', 'CORE'), -50)
        self.assertEqual(config.getfloat('UNDERVOLT', 'CACHE', fallback=0.0), 0.0)

    def test_load_config_removes_unencodable_undervolt_values(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: True\n'
            '[AC]\nUpdate_Rate_s: 5\n'
            '[UNDERVOLT]\nCORE: -1001\nCACHE: -1000\nGPU: nan\nUNCORE: -inf\nANALOGIO: 1\n'
        )

        with mock.patch.object(throttled, 'warning'), mock.patch.object(throttled, 'log'):
            config = throttled.load_config()

        self.assertFalse(config.has_option('UNDERVOLT', 'CORE'))
        self.assertEqual(config.getfloat('UNDERVOLT', 'CACHE'), -1000)
        self.assertFalse(config.has_option('UNDERVOLT', 'GPU'))
        self.assertFalse(config.has_option('UNDERVOLT', 'UNCORE'))
        self.assertEqual(config.getfloat('UNDERVOLT', 'ANALOGIO'), 0)

    def test_load_config_rejects_iccmax_values_that_overflow_the_field(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n'
            '[ICCMAX.AC]\nCORE: 300\nGPU: 100\nCACHE: nan\n[ICCMAX.BATTERY]\nCORE: 0.1\n'
        )

        with mock.patch.object(throttled, 'warning'):
            config = throttled.load_config()

        self.assertFalse(config.has_option('ICCMAX.AC', 'CORE'))
        self.assertEqual(config.getfloat('ICCMAX.AC', 'GPU'), 100)
        self.assertFalse(config.has_option('ICCMAX.AC', 'CACHE'))
        self.assertFalse(config.has_option('ICCMAX.BATTERY', 'CORE'))

    def test_mchbar_probe_keeps_setpci_stderr_out_of_the_journal(self):
        throttled = load_throttled()
        failure = throttled.CalledProcessError(
            1, ['setpci'], output=b'', stderr=b'setpci: Cannot map ecam region: Operation not permitted.\n'
        )

        with mock.patch.object(throttled, 'check_output', side_effect=failure) as check:
            with mock.patch.object(throttled, 'log') as log:
                self.assertIsNone(throttled._read_mchbar_dword('ecam'))

        self.assertEqual(check.call_args.kwargs['stderr'], throttled.PIPE)
        log.assert_not_called()

    def test_mchbar_probe_surfaces_setpci_stderr_in_debug_mode(self):
        throttled = load_throttled()
        throttled.args.debug = True
        failure = throttled.CalledProcessError(
            1, ['setpci'], output=b'', stderr=b'setpci: Cannot map ecam region: Operation not permitted.\n'
        )

        with mock.patch.object(throttled, 'check_output', side_effect=failure):
            with mock.patch.object(throttled, 'log') as log:
                self.assertIsNone(throttled._read_mchbar_dword('ecam'))

        self.assertIn('Cannot map ecam region', log.call_args.args[0])

    def test_check_kernel_warns_when_lockdown_is_active(self):
        throttled = load_throttled()

        def open_kernel_file(path, *args, **kwargs):
            if path == '/sys/kernel/security/lockdown':
                return io.StringIO('none [integrity] confidentiality\n')
            if path == '/boot/config-test':
                return io.StringIO('CONFIG_DEVMEM=y\nCONFIG_X86_MSR=m\n')
            raise FileNotFoundError(path)

        with mock.patch.object(throttled.os, 'geteuid', return_value=0):
            with mock.patch.object(throttled, 'uname', return_value=('', '', 'test')):
                with mock.patch('builtins.open', side_effect=open_kernel_file):
                    with mock.patch.object(throttled, 'warning') as warning:
                        throttled.check_kernel()

        warning.assert_called_once_with('Kernel lockdown is active: MSR and /dev/mem writes will be blocked.')

    def test_monitor_handles_rapl_counter_wraparound(self):
        throttled = load_throttled()
        energy_values = {
            'MSR_INTEL_PKG_ENERGY_STATUS': iter((2**32 - 10, 5)),
            'MSR_PP1_ENERGY_STATUS': iter((100, 110)),
            'MSR_DRAM_ENERGY_STATUS': iter((100, 110)),
        }

        def readmsr(msr, *args, **kwargs):
            if msr == 'MSR_RAPL_POWER_UNIT':
                return 0
            if msr in energy_values:
                return next(energy_values[msr])
            return 0

        with mock.patch.object(throttled, 'readmsr', side_effect=readmsr):
            with mock.patch.object(throttled, 'time', side_effect=(0, 0, 0, 1, 1, 1)):
                with mock.patch.object(throttled, 'get_undervolt', return_value=dict.fromkeys(throttled.VOLTAGE_PLANES, 0)):
                    with mock.patch.object(throttled, 'get_icc_max', return_value=dict.fromkeys(throttled.CURRENT_PLANES, 0)):
                        with mock.patch.object(throttled, 'log') as log:
                            throttled.monitor(StopAfterWait(), 1)

        output = next(call.args[0] for call in log.call_args_list if 'Package:' in call.args[0])
        self.assertIn('Package: 15.0 W', output)
        self.assertIn('Total: 35.0 W', output)

    def test_load_config_disables_malformed_power_limits(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: True\n'
            '[AC]\nUpdate_Rate_s: 5\nPL1_Tdp_W: inf\nPL2_Duration_s: nan\nPL2_Tdp_W: abc\nPL1_Duration_s: 1\n'
        )

        with mock.patch.object(throttled, 'warning'):
            config = throttled.load_config()

        self.assertFalse(config.has_option('AC', 'PL1_Tdp_W'))
        self.assertFalse(config.has_option('AC', 'PL2_Duration_s'))
        self.assertFalse(config.has_option('AC', 'PL2_Tdp_W'))
        self.assertEqual(config.getfloat('AC', 'PL1_Duration_s'), 1)
        self.assertEqual(config.getfloat('AC', 'Update_Rate_s'), 5)

    def test_load_config_still_dies_on_a_nonfinite_update_rate(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config('[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: nan\n')
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                throttled.load_config()

        self.assertIn('must be a finite number', stderr.getvalue())

    def test_monitor_skips_only_the_undervolt_read_when_undervolt_is_unsupported(self):
        throttled = load_throttled()
        throttled.UNSUPPORTED_FEATURES.append('UNDERVOLT')

        with mock.patch.object(throttled, 'readmsr', return_value=0):
            with mock.patch.object(throttled, 'get_undervolt') as get_undervolt:
                with mock.patch.object(
                    throttled, 'get_icc_max', return_value=dict.fromkeys(throttled.CURRENT_PLANES, 0)
                ) as get_icc_max:
                    with mock.patch.object(throttled, 'log') as log:
                        throttled.monitor(StopAfterWait(), 1)

        get_undervolt.assert_not_called()
        get_icc_max.assert_called_once_with(convert=True)
        messages = [call.args[0] for call in log.call_args_list]
        self.assertIn('[D] Undervolt offsets: unsupported', messages)
        self.assertIn('[D] IccMax: CORE: 0.00 A | GPU: 0.00 A | CACHE: 0.00 A', messages)

    def test_monitor_skips_only_the_iccmax_read_when_iccmax_is_unsupported(self):
        throttled = load_throttled()
        throttled.UNSUPPORTED_FEATURES.append('ICCMAX')

        with mock.patch.object(throttled, 'readmsr', return_value=0):
            with mock.patch.object(
                throttled, 'get_undervolt', return_value=dict.fromkeys(throttled.VOLTAGE_PLANES, -50)
            ) as get_undervolt:
                with mock.patch.object(throttled, 'get_icc_max') as get_icc_max:
                    with mock.patch.object(throttled, 'log') as log:
                        throttled.monitor(StopAfterWait(), 1)

        get_undervolt.assert_called_once_with(convert=True)
        get_icc_max.assert_not_called()
        messages = [call.args[0] for call in log.call_args_list]
        self.assertIn('[D] IccMax: unsupported', messages)
        self.assertTrue(any('CORE: -50.00 mV' in message for message in messages))

    def test_monitor_skips_the_oc_mailbox_when_both_probes_fail(self):
        throttled = load_throttled()
        throttled.UNSUPPORTED_FEATURES.extend(('UNDERVOLT', 'ICCMAX'))

        with mock.patch.object(throttled, 'readmsr', return_value=0):
            with mock.patch.object(throttled, 'get_undervolt') as get_undervolt:
                with mock.patch.object(throttled, 'get_icc_max') as get_icc_max:
                    with mock.patch.object(throttled, 'log') as log:
                        throttled.monitor(StopAfterWait(), 1)

        get_undervolt.assert_not_called()
        get_icc_max.assert_not_called()
        messages = [call.args[0] for call in log.call_args_list]
        self.assertIn('[D] Undervolt offsets: unsupported', messages)
        self.assertIn('[D] IccMax: unsupported', messages)

    def test_monitor_keeps_supported_undervolt_output(self):
        throttled = load_throttled()
        undervolt = dict.fromkeys(throttled.VOLTAGE_PLANES, -50)

        with mock.patch.object(throttled, 'readmsr', return_value=0):
            with mock.patch.object(throttled, 'get_undervolt', return_value=undervolt):
                with mock.patch.object(
                    throttled, 'get_icc_max', return_value=dict.fromkeys(throttled.CURRENT_PLANES, 0)
                ):
                    with mock.patch.object(throttled, 'log') as log:
                        throttled.monitor(StopAfterWait(), 1)

        self.assertTrue(any('CORE: -50.00 mV' in call.args[0] for call in log.call_args_list))

    def test_probe_marks_only_undervolt_when_its_mailbox_command_fails(self):
        throttled = load_throttled()

        with mock.patch.object(throttled, 'get_undervolt', side_effect=OSError):
            with mock.patch.object(
                throttled, 'get_icc_max', return_value=dict.fromkeys(throttled.CURRENT_PLANES, 0)
            ):
                with mock.patch.object(throttled, 'readmsr', return_value=0):
                    with mock.patch.object(throttled, 'writemsr'):
                        with mock.patch.object(throttled, 'warning'):
                            with mock.patch.object(throttled, 'log'):
                                throttled.test_msr_rw_capabilities()

        self.assertIn('UNDERVOLT', throttled.UNSUPPORTED_FEATURES)
        self.assertNotIn('ICCMAX', throttled.UNSUPPORTED_FEATURES)

    def test_probe_marks_only_iccmax_when_its_mailbox_command_fails(self):
        throttled = load_throttled()

        with mock.patch.object(
            throttled, 'get_undervolt', return_value=dict.fromkeys(throttled.VOLTAGE_PLANES, 0)
        ):
            with mock.patch.object(throttled, 'get_icc_max', side_effect=OSError):
                with mock.patch.object(throttled, 'readmsr', return_value=0):
                    with mock.patch.object(throttled, 'writemsr'):
                        with mock.patch.object(throttled, 'warning'):
                            with mock.patch.object(throttled, 'log'):
                                throttled.test_msr_rw_capabilities()

        self.assertNotIn('UNDERVOLT', throttled.UNSUPPORTED_FEATURES)
        self.assertIn('ICCMAX', throttled.UNSUPPORTED_FEATURES)

    def test_set_icc_max_skips_the_mailbox_when_iccmax_is_unsupported(self):
        throttled = load_throttled()
        throttled.UNSUPPORTED_FEATURES.append('ICCMAX')
        config = throttled.configparser.ConfigParser()
        config.add_section('ICCMAX.AC')
        config.set('ICCMAX.AC', 'CORE', '100')

        with mock.patch.object(throttled, 'writemsr') as writemsr:
            throttled.set_icc_max(config, source='AC')

        writemsr.assert_not_called()


if __name__ == '__main__':
    unittest.main()
