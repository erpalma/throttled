import importlib.util
import io
import errno
import struct
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
    module.args = SimpleNamespace(log=None, debug=False, config='/tmp/throttled.conf', force=True, monitor=None)
    module.log_history.clear()
    module.power['source'] = 'AC'
    module.power['method'] = 'dbus'
    return module


class HWPPerCpuTests(unittest.TestCase):
    def test_set_hwp_rewrites_each_cpu_independently(self):
        throttled = load_throttled()
        current = {
            0: 0xAAAABBBB01020304,
            2: 0x1111222233445566,
        }

        def readmsr(msr, *args, cpu=None, **kwargs):
            self.assertEqual(msr, 'IA32_HWP_REQUEST')
            return current[cpu]

        with mock.patch.object(throttled, '_ensure_msr_module', return_value=['/dev/cpu/0/msr', '/dev/cpu/2/msr']):
            with mock.patch.object(throttled, 'readmsr', side_effect=readmsr):
                with mock.patch.object(throttled, 'writemsr') as writemsr:
                    throttled.set_hwp(True)

        expected = [
            mock.call(
                'IA32_HWP_REQUEST',
                (current[0] & 0xFFFFFFFF00FFFFFF) | (throttled.HWP_PERFORMANCE_VALUE << 24),
                cpu=0,
            ),
            mock.call(
                'IA32_HWP_REQUEST',
                (current[2] & 0xFFFFFFFF00FFFFFF) | (throttled.HWP_PERFORMANCE_VALUE << 24),
                cpu=2,
            ),
        ]
        self.assertEqual(writemsr.call_args_list, expected)

    def test_writemsr_cpu_targets_one_msr_device(self):
        throttled = load_throttled()

        with mock.patch.object(throttled, '_ensure_msr_module', return_value='/dev/cpu/2/msr') as ensure:
            with mock.patch.object(throttled.os, 'open', return_value=11) as open_:
                with mock.patch.object(throttled.os, 'lseek') as lseek:
                    with mock.patch.object(throttled.os, 'write') as write:
                        with mock.patch.object(throttled.os, 'close') as close:
                            throttled.writemsr('IA32_HWP_REQUEST', 0x1234, cpu=2)

        ensure.assert_called_once_with(2)
        open_.assert_called_once_with('/dev/cpu/2/msr', throttled.os.O_WRONLY)
        lseek.assert_called_once_with(11, throttled.MSR_DICT['IA32_HWP_REQUEST'], throttled.os.SEEK_SET)
        write.assert_called_once_with(11, struct.pack('Q', 0x1234))
        close.assert_called_once_with(11)

    def test_writemsr_without_cpu_preserves_broadcast(self):
        throttled = load_throttled()

        with mock.patch.object(
            throttled, '_ensure_msr_module', return_value=['/dev/cpu/0/msr', '/dev/cpu/2/msr']
        ) as ensure:
            with mock.patch.object(throttled.os, 'open', side_effect=[10, 20]) as open_:
                with mock.patch.object(throttled.os, 'lseek'):
                    with mock.patch.object(throttled.os, 'write'):
                        with mock.patch.object(throttled.os, 'close'):
                            throttled.writemsr('IA32_HWP_REQUEST', 0x1234)

        ensure.assert_called_once_with()
        self.assertEqual(
            open_.call_args_list,
            [
                mock.call('/dev/cpu/0/msr', throttled.os.O_WRONLY),
                mock.call('/dev/cpu/2/msr', throttled.os.O_WRONLY),
            ],
        )

    def test_writemsr_rejects_invalid_cpu_arguments(self):
        throttled = load_throttled()
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exit_context:
                throttled.writemsr('IA32_HWP_REQUEST', 0, cpu=-1)

        self.assertEqual(exit_context.exception.code, 1)
        self.assertIn('Wrong writemsr cpu param', stderr.getvalue())

    def test_readmsr_fails_safe_when_cpu_disappears(self):
        throttled = load_throttled()
        stderr = io.StringIO()

        with mock.patch.object(throttled, '_ensure_msr_module', return_value='/dev/cpu/2/msr'):
            with mock.patch.object(throttled.os, 'open', side_effect=FileNotFoundError(errno.ENOENT, 'CPU offline')):
                with redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as exit_context:
                        throttled.readmsr('IA32_HWP_REQUEST', cpu=2)

        self.assertEqual(exit_context.exception.code, 1)
        self.assertIn('CPU 2 went offline while reading MSR IA32_HWP_REQUEST (774); aborting.', stderr.getvalue())

    def test_hwp_probe_writes_back_only_cpu0(self):
        throttled = load_throttled()

        def readmsr(msr, *args, cpu=None, **kwargs):
            self.assertEqual((msr, cpu), ('IA32_HWP_REQUEST', 0))
            return 0x1234

        with mock.patch.object(throttled, 'get_undervolt', return_value={'CORE': 0}):
            with mock.patch.object(throttled, 'get_icc_max', return_value={'CORE': 0}):
                with mock.patch.object(throttled, 'readmsr', side_effect=readmsr):
                    with mock.patch.object(throttled, 'writemsr') as writemsr:
                        throttled.test_msr_rw_capabilities()

        writemsr.assert_called_once_with('IA32_HWP_REQUEST', 0x1234, cpu=0)


if __name__ == '__main__':
    unittest.main()
