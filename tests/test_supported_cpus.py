import importlib.util
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_throttled():
    spec = importlib.util.spec_from_file_location('throttled_under_test', ROOT / 'throttled.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SupportedCPUTests(unittest.TestCase):
    def test_open_issue_reported_cpu_ids_are_supported(self):
        throttled = load_throttled()
        reported_cpu_ids = {
            (6, 37, 5),    # #279 Intel Core i7 L640
            (6, 181, 0),   # #385 Intel Core Ultra 7 265U
            (6, 190, 0),   # #362/#392 Intel N100 / i3-N305
            (6, 197, 2),   # #429 Intel Core Ultra 7 255H
            (6, 198, 2),   # #382/#393 Intel Core Ultra 9 275HX / Ultra 7 255HX
            (6, 204, 2),   # #401 Intel Core Ultra X7 358H
        }

        missing = reported_cpu_ids - set(throttled.supported_cpus)

        self.assertEqual(missing, set())

    def test_check_cpu_accepts_documented_client_cpu_ids(self):
        throttled = load_throttled()
        # F-M-S signatures from Intel's microcode-20260925 release notes.
        documented_cpu_ids = {
            (6, 165, 3): 'CometLake-S',
            (6, 166, 1): 'CometLake-U',
            (6, 191, 2): 'RaptorLake-HX/S',
            (6, 191, 5): 'RaptorLake-S',
            (6, 197, 2): 'ArrowLake-H',
            (6, 204, 3): 'PantherLake',
        }

        for cpuid, architecture in documented_cpu_ids.items():
            with self.subTest(cpuid=cpuid):
                self.assertEqual(throttled.supported_cpus.get(cpuid), architecture)
                family, model, stepping = cpuid
                cpuinfo = (
                    'processor: 0\n'
                    'vendor_id: GenuineIntel\n'
                    f'cpu family: {family}\n'
                    f'model: {model}\n'
                    f'stepping: {stepping}\n'
                )
                with (
                    mock.patch('builtins.open', mock.mock_open(read_data=cpuinfo)) as cpuinfo_file,
                    mock.patch.object(throttled, 'log') as log,
                ):
                    self.assertEqual(throttled.check_cpu(), cpuid)

                cpuinfo_file.assert_called_once_with('/proc/cpuinfo')
                log.assert_called_once_with(f'[I] Detected CPU architecture: Intel {architecture}')

    def test_check_cpu_rejects_unlisted_client_stepping(self):
        throttled = load_throttled()
        cpuinfo = (
            'processor: 0\n'
            'vendor_id: GenuineIntel\n'
            'cpu family: 6\n'
            'model: 197\n'
            'stepping: 3\n'
        )
        with (
            mock.patch('builtins.open', mock.mock_open(read_data=cpuinfo)),
            mock.patch.object(throttled, 'fatal', side_effect=SystemExit(1)) as fatal,
            mock.patch.object(throttled, 'log') as log,
        ):
            with self.assertRaises(SystemExit) as exit_context:
                throttled.check_cpu()

        self.assertEqual(exit_context.exception.code, 1)
        fatal.assert_called_once()
        self.assertIn('Your CPU model is not supported.', fatal.call_args.args[0])
        log.assert_not_called()


if __name__ == '__main__':
    unittest.main()
