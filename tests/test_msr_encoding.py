import configparser
import importlib.util
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
        with self.assertRaises(AssertionError):
            throttled.calc_icc_max_msr('CORE', 256)


if __name__ == '__main__':
    unittest.main()
