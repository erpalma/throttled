import configparser
import importlib.util
import io
import unittest
from pathlib import Path
from threading import Thread
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


class DaemonRobustnessTests(unittest.TestCase):
    def test_source_install_service_restarts_after_daemon_failure(self):
        service = (ROOT / 'systemd' / 'throttled.service').read_text()

        self.assertIn('Restart=on-failure', service)
        self.assertIn('RestartSec=5', service)

    def test_fatal_in_a_worker_thread_kills_the_whole_process(self):
        throttled = load_throttled()

        def run_fatal():
            try:
                throttled.fatal('boom')
            except SystemExit:
                # reached only because os._exit is mocked out below
                pass

        with mock.patch.object(throttled.os, '_exit') as os_exit:
            with mock.patch('sys.stderr', new=io.StringIO()):
                thread = Thread(target=run_fatal)
                thread.start()
                thread.join()

        os_exit.assert_called_once_with(1)

    def test_power_thread_crash_exits_the_process_for_systemd_to_restart(self):
        throttled = load_throttled()

        with mock.patch.object(throttled, '_power_thread', side_effect=RuntimeError('boom')):
            with mock.patch.object(throttled.os, '_exit') as os_exit:
                with mock.patch.object(throttled, 'warning'):
                    throttled.power_thread({}, None)

        os_exit.assert_called_once_with(1)

    def test_dbus_fatal_status_escapes_main(self):
        throttled = load_throttled()
        config = configparser.ConfigParser()
        config.read_dict({'GENERAL': {'Enabled': 'True'}, 'AC': {'Update_Rate_s': '5'}})
        parsed_args = SimpleNamespace(log=None, debug=False, config='/tmp/throttled.conf', monitor=None, force=True)
        parser = mock.Mock()
        parser.parse_args.return_value = parsed_args

        with (
            mock.patch.object(throttled, 'build_arg_parser', return_value=parser),
            mock.patch.object(throttled, 'load_config', return_value=config),
            mock.patch.object(throttled, 'set_msr_allow_writes'),
            mock.patch.object(throttled, 'test_msr_rw_capabilities'),
            mock.patch.object(throttled, 'is_on_battery', return_value=False),
            mock.patch.object(throttled, 'get_cpu_platform_info', return_value={}),
            mock.patch.object(throttled, 'calc_reg_values', return_value={}),
            mock.patch.object(throttled, 'undervolt'),
            mock.patch.object(throttled, 'set_icc_max'),
            mock.patch.object(throttled, 'set_hwp'),
            mock.patch.object(throttled, 'log'),
            mock.patch.object(throttled, 'Thread'),
            mock.patch.object(throttled, 'run_dbus_loop', new=mock.Mock(return_value=object())),
            mock.patch.object(throttled.asyncio, 'run', side_effect=SystemExit(7)),
        ):
            with self.assertRaises(SystemExit) as exit_context:
                throttled.main()

        self.assertEqual(exit_context.exception.code, 7)


if __name__ == '__main__':
    unittest.main()
