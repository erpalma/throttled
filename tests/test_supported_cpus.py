import importlib.util
import unittest
from pathlib import Path


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
            (6, 198, 2),   # #382/#393 Intel Core Ultra 9 275HX / Ultra 7 255HX
        }

        missing = reported_cpu_ids - set(throttled.supported_cpus)

        self.assertEqual(missing, set())


if __name__ == '__main__':
    unittest.main()
