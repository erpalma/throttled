import importlib.util
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


if __name__ == '__main__':
    unittest.main()
