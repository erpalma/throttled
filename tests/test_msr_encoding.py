import configparser
import importlib.util
import io
import subprocess
import sys
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


def make_config(sections):
    config = configparser.ConfigParser()
    for section, options in sections.items():
        config.add_section(section)
        for option, value in options.items():
            config.set(section, option, str(value))
    return config


class MsrEncodingTests(unittest.TestCase):
    def test_undervolt_decode_handles_the_sign_boundary(self):
        throttled = load_throttled()

        for offset_mv in (0, -100, -125, -999, -1000):
            msr = throttled.calc_undervolt_msr('CORE', offset_mv)
            self.assertEqual(throttled.calc_undervolt_mv(msr & 0xFFFFFFFF), offset_mv)

    def test_undervolt_encoder_rejects_values_outside_signed_eleven_bits(self):
        throttled = load_throttled()

        with self.assertRaisesRegex(ValueError, 'between -1000 and 0 mV'):
            throttled.calc_undervolt_msr('CORE', -1001)
        with self.assertRaisesRegex(ValueError, 'between -1000 and 0 mV'):
            throttled.calc_undervolt_msr('CORE', 1)
        for invalid in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, 'between -1000 and 0 mV'):
                    throttled.calc_undervolt_msr('CORE', invalid)

    def test_trip_offset_is_clamped_to_the_six_bit_msr_field(self):
        throttled = load_throttled()
        config = make_config(
            {
                'AC': {'Update_Rate_s': '5', 'Trip_Temp_C': '40'},
                'BATTERY': {'Update_Rate_s': '30', 'Trip_Temp_C': '40'},
            }
        )

        with mock.patch.object(throttled, 'get_critical_temp', return_value=105):
            with mock.patch.object(throttled, 'log'):
                regs = throttled.calc_reg_values(
                    {'feature_programmable_temperature_target': 1, 'feature_programmable_tdp_limit': 0},
                    config,
                )

        self.assertEqual(regs['AC']['MSR_TEMPERATURE_TARGET'], 63 << 24)
        self.assertEqual(regs['BATTERY']['MSR_TEMPERATURE_TARGET'], 63 << 24)

    def test_icc_max_encoder_rejects_values_that_overflow_ten_bits(self):
        throttled = load_throttled()

        self.assertEqual(throttled.calc_icc_max_msr('CORE', 0x3FF / 4) & 0x3FF, 0x3FF)
        with self.assertRaises(ValueError):
            throttled.calc_icc_max_msr('CORE', 256)

    def test_package_power_limit_encoder_rejects_field_spill(self):
        throttled = load_throttled()

        value = throttled._encode_pkg_power_limit(0x7FFF, 0x7F, 0x7FFF, 0x7F)
        self.assertEqual(
            value,
            0x7FFF | (1 << 15) | (1 << 16) | (0x7F << 17) | (0x7FFF << 32) | (1 << 47) | (0x7F << 49),
        )
        with self.assertRaisesRegex(ValueError, 'PL1'):
            throttled._encode_pkg_power_limit(0x8000, 0, 1, 0)
        with self.assertRaisesRegex(ValueError, 'PL2'):
            throttled._encode_pkg_power_limit(1, 0, 0x8000, 0)

    def test_calculated_package_power_limit_rejects_unencodable_wattage(self):
        throttled = load_throttled()
        config = make_config(
            {
                'GENERAL': {'Enabled': 'True'},
                'AC': {
                    'Update_Rate_s': '5',
                    'PL1_Tdp_W': str(0x8000),
                    'PL1_Duration_s': '1',
                    'PL2_Tdp_W': '20',
                    'PL2_Duration_s': '1',
                },
            }
        )
        platform_info = {
            'feature_programmable_temperature_target': 0,
            'feature_programmable_tdp_limit': 0,
        }

        stderr = io.StringIO()
        with mock.patch.object(throttled, 'get_power_unit', return_value=1):
            with mock.patch.object(
                throttled,
                'get_cur_pkg_power_limits',
                return_value={'PL1': 0, 'TW1': 0, 'PL2': 0, 'TW2': 0},
            ):
                with mock.patch.object(throttled, 'calc_time_window_vars', return_value=(0, 0)):
                    with mock.patch.object(throttled, 'warning'):
                        with redirect_stderr(stderr):
                            with self.assertRaises(SystemExit):
                                throttled.calc_reg_values(platform_info, config)

        self.assertIn('PL1', stderr.getvalue())

    def test_icc_max_encoder_rejects_malformed_planes_and_currents(self):
        throttled = load_throttled()

        self.assertEqual(throttled.calc_icc_max_msr('CORE', 100) & 0x3FF, 400)
        with self.assertRaisesRegex(ValueError, 'plane'):
            throttled.calc_icc_max_msr('UNCORE', 100)
        with self.assertRaisesRegex(ValueError, '10-bit'):
            throttled.calc_icc_max_msr('CORE', 0.1)
        for invalid in (0, -1, 255.76, float('nan'), float('inf'), 'abc'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    throttled.calc_icc_max_msr('CORE', invalid)

    def test_icc_max_validation_survives_python_optimize(self):
        code = (
            'import throttled\n'
            'try:\n'
            '    throttled.calc_icc_max_msr("CORE", 256)\n'
            'except ValueError:\n'
            '    raise SystemExit(0)\n'
            'raise SystemExit(1)\n'
        )
        result = subprocess.run([sys.executable, '-O', '-c', code], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
