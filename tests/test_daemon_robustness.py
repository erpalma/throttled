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


class StopAfterWait:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.stopped = True


class DaemonRobustnessTests(unittest.TestCase):
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
                    throttled.power_thread({}, None, None)

        os_exit.assert_called_once_with(1)


if __name__ == '__main__':
    unittest.main()
