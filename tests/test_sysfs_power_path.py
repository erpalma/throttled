import configparser
import importlib.util
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
    return module


def make_config(pattern):
    config = configparser.ConfigParser()
    config.add_section('GENERAL')
    config.set('GENERAL', 'Sysfs_Power_Path', pattern)
    return config


class SysfsPowerPathTests(unittest.TestCase):
    def test_single_adapter(self):
        throttled = load_throttled()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'AC0' / 'online'
            path.parent.mkdir()
            path.write_text('1\n')

            self.assertFalse(throttled.is_on_battery(make_config(f'{directory}/AC*/online')))
            path.write_text('0\n')
            self.assertTrue(throttled.is_on_battery(make_config(f'{directory}/AC*/online')))

    def test_multiple_adapters_ac_if_any_is_online(self):
        throttled = load_throttled()
        with tempfile.TemporaryDirectory() as directory:
            for name, value in (('AC0', '0\n'), ('AC1', '1\n')):
                path = Path(directory) / name / 'online'
                path.parent.mkdir()
                path.write_text(value)

            self.assertFalse(throttled.is_on_battery(make_config(f'{directory}/AC*/online')))

    def test_all_adapters_off_is_battery(self):
        throttled = load_throttled()
        with tempfile.TemporaryDirectory() as directory:
            for name in ('AC0', 'AC1'):
                path = Path(directory) / name / 'online'
                path.parent.mkdir()
                path.write_text('0\n')

            self.assertTrue(throttled.is_on_battery(make_config(f'{directory}/AC*/online')))

    def test_no_match_uses_upower_fallback(self):
        throttled = load_throttled()
        with mock.patch.object(throttled, 'get_upower_on_battery', return_value=False) as upower:
            self.assertFalse(throttled.is_on_battery(make_config('/missing/AC*/online')))
        upower.assert_called_once_with()

    def test_malformed_match_uses_upower_fallback(self):
        throttled = load_throttled()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'AC0' / 'online'
            path.parent.mkdir()
            path.write_text('unknown\n')
            with mock.patch.object(throttled, 'get_upower_on_battery', return_value=True) as upower:
                self.assertTrue(throttled.is_on_battery(make_config(f'{directory}/AC*/online')))
            upower.assert_called_once_with()

    def test_online_adapter_wins_over_another_read_error(self):
        throttled = load_throttled()
        paths = ['/sys/class/power_supply/AC0/online', '/sys/class/power_supply/AC1/online']

        def open_path(path):
            if path == paths[0]:
                return mock.mock_open(read_data='1\n').return_value
            raise OSError('adapter disappeared')

        with mock.patch.object(throttled.glob, 'glob', return_value=paths):
            with mock.patch('builtins.open', side_effect=open_path):
                with mock.patch.object(throttled, 'warning') as warning:
                    with mock.patch.object(throttled, 'get_upower_on_battery') as upower:
                        self.assertFalse(throttled.is_on_battery(make_config('/sys/class/power_supply/AC*/online')))
        warning.assert_called_once()
        upower.assert_not_called()

    def test_hot_unplugged_adapter_does_not_become_battery(self):
        throttled = load_throttled()
        paths = ['/sys/class/power_supply/AC0/online', '/sys/class/power_supply/AC1/online']
        opened = {paths[0]: mock.mock_open(read_data='0\n').return_value}

        def open_path(path):
            if path == paths[1]:
                raise FileNotFoundError(path)
            return opened[path]

        with mock.patch.object(throttled.glob, 'glob', return_value=list(reversed(paths))):
            with mock.patch('builtins.open', side_effect=open_path):
                with mock.patch.object(throttled, 'get_upower_on_battery', return_value=False) as upower:
                    self.assertFalse(throttled.is_on_battery(make_config('/sys/class/power_supply/AC*/online')))
        upower.assert_called_once_with()

    def test_sorted_path_order_is_stable(self):
        throttled = load_throttled()
        paths = ['/sys/class/power_supply/AC0/online', '/sys/class/power_supply/AC1/online']
        opened = mock.mock_open(read_data='0\n')
        with mock.patch.object(throttled.glob, 'glob', return_value=list(reversed(paths))):
            with mock.patch('builtins.open', opened):
                self.assertTrue(throttled.is_on_battery(make_config('/sys/class/power_supply/AC*/online')))
        self.assertEqual([call.args[0] for call in opened.call_args_list], paths)


if __name__ == '__main__':
    unittest.main()
