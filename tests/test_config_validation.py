import importlib.util
import io
import os
import tempfile
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

    def test_load_config_rejects_iccmax_values_that_overflow_the_field(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n[ICCMAX.AC]\nCORE: 300\nGPU: 100\n'
        )

        with mock.patch.object(throttled, 'warning'):
            config = throttled.load_config()

        self.assertFalse(config.has_option('ICCMAX.AC', 'CORE'))
        self.assertEqual(config.getfloat('ICCMAX.AC', 'GPU'), 100)

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


if __name__ == '__main__':
    unittest.main()
