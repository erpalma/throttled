import asyncio
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


class Variant:
    def __init__(self, value):
        self.value = value


class DBusHelperTests(unittest.TestCase):
    def test_ac_signal_callback_updates_power_source(self):
        throttled = load_throttled()
        throttled.power['source'] = 'BATTERY'
        throttled.power['method'] = 'polling'

        throttled.handle_ac_properties_changed('org.freedesktop.UPower', {'OnBattery': Variant(False)}, [])

        self.assertEqual(throttled.power, {'source': 'AC', 'method': 'dbus'})

    def test_resume_callback_reapplies_settings_only_after_wake(self):
        throttled = load_throttled()
        config = throttled.configparser.ConfigParser()
        config.add_section('GENERAL')
        config.set('GENERAL', 'Enabled', 'True')
        calls = []

        with mock.patch.object(throttled, 'undervolt', lambda config: calls.append(('undervolt', config))):
            with mock.patch.object(throttled, 'set_icc_max', lambda config: calls.append(('iccmax', config))):
                throttled.handle_sleep_prepare(True, config)
                self.assertEqual(calls, [])

                throttled.handle_sleep_prepare(False, config)
                self.assertEqual(calls, [('undervolt', config), ('iccmax', config)])

    def test_dbus_resume_signal_enabled_when_undervolt_or_iccmax_configured(self):
        throttled = load_throttled()
        config = throttled.configparser.ConfigParser()
        config.add_section('GENERAL')
        config.set('GENERAL', 'Enabled', 'True')
        config.add_section('UNDERVOLT')
        config.set('UNDERVOLT', 'CORE', '-50')

        self.assertIs(throttled.should_listen_for_resume(config), True)

    def test_dbus_resume_signal_disabled_without_undervolt_or_iccmax(self):
        throttled = load_throttled()
        config = throttled.configparser.ConfigParser()

        self.assertIs(throttled.should_listen_for_resume(config), False)


class DBusLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_disconnect_reaches_systemd(self):
        throttled = load_throttled()
        bus = mock.Mock()
        bus.wait_for_disconnect = mock.AsyncMock(side_effect=ConnectionError('bus lost'))
        context = {'bus': bus}

        with mock.patch.object(throttled, 'setup_dbus_signal_handlers', return_value=context):
            with self.assertRaisesRegex(ConnectionError, 'bus lost'):
                await asyncio.wait_for(throttled.run_dbus_loop(object()), timeout=1)

        bus.wait_for_disconnect.assert_awaited_once_with()
        bus.disconnect.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
