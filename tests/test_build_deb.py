import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from throttled_version import __version__


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'build-deb.sh'


class BuildDebTests(unittest.TestCase):
    def test_help_mentions_usage_and_options(self):
        result = subprocess.run([str(SCRIPT), '--help'], cwd=ROOT, text=True, capture_output=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Usage:', result.stdout)
        self.assertIn('--output-dir', result.stdout)
        self.assertIn('--version', result.stdout)
        self.assertIn('<project-version>+git.<short-sha>', result.stdout)

    def test_default_version_uses_release_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([str(SCRIPT), '--output-dir', tmp], cwd=ROOT, text=True, capture_output=True)

            self.assertEqual(result.returncode, 0, result.stderr)
            deb = Path(result.stdout.strip())
            self.assertEqual(deb.parent, Path(tmp))
            self.assertTrue(deb.name.startswith(f'throttled_{__version__}+git.'), result.stdout)
            self.assertTrue(deb.name.endswith('_all.deb'), result.stdout)
            self.assertTrue(deb.exists(), result.stdout)

    def test_builds_deb_with_expected_metadata_and_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_version = f'{__version__}+test'
            result = subprocess.run(
                [
                    str(SCRIPT),
                    '--output-dir',
                    tmp,
                    '--version',
                    test_version,
                    '--maintainer',
                    'Tester <test@example.com>',
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            deb = Path(tmp) / f'throttled_{test_version}_all.deb'
            self.assertTrue(deb.exists(), result.stdout)

            info = subprocess.run(['dpkg-deb', '--info', str(deb)], text=True, capture_output=True, check=True)
            contents = subprocess.run(['dpkg-deb', '--contents', str(deb)], text=True, capture_output=True, check=True)
            control_tar = subprocess.run(['dpkg-deb', '--ctrl-tarfile', str(deb)], capture_output=True, check=True)
            data_tar = subprocess.run(['dpkg-deb', '--fsys-tarfile', str(deb)], capture_output=True, check=True)

            self.assertIn('Package: throttled', info.stdout)
            self.assertIn(f'Version: {test_version}', info.stdout)
            self.assertIn('Architecture: all', info.stdout)
            self.assertIn('Maintainer: Tester <test@example.com>', info.stdout)
            self.assertIn('python3 (>= 3.10), python3-dbus-fast, pciutils, kmod, upower, systemd', info.stdout)
            self.assertIn('./usr/bin/throttled', contents.stdout)
            self.assertIn('./usr/lib/throttled/throttled.py', contents.stdout)
            self.assertIn('./usr/lib/throttled/mmio.py', contents.stdout)
            self.assertIn('./usr/lib/throttled/throttled_version.py', contents.stdout)
            self.assertIn('./etc/throttled.conf', contents.stdout)
            self.assertIn('./usr/lib/systemd/system/throttled.service', contents.stdout)
            self.assertNotIn('drwxrwx', contents.stdout)
            self.assertNotIn('-rw-rw', contents.stdout)

            with tarfile.open(fileobj=io.BytesIO(control_tar.stdout), mode='r:*') as archive:
                postinst = archive.extractfile('./postinst').read().decode()
                prerm = archive.extractfile('./prerm').read().decode()
                postrm = archive.extractfile('./postrm').read().decode()
            with tarfile.open(fileobj=io.BytesIO(data_tar.stdout), mode='r:*') as archive:
                service = archive.extractfile('./usr/lib/systemd/system/throttled.service').read().decode()
                wrapper = archive.extractfile('./usr/bin/throttled').read().decode()

            self.assertIn('systemctl daemon-reload', postinst)
            self.assertIn('systemctl enable throttled.service', postinst)
            self.assertIn('systemctl restart throttled.service', postinst)
            self.assertIn('systemctl stop throttled.service', prerm)
            self.assertIn('systemctl disable throttled.service', prerm)
            self.assertIn('systemctl daemon-reload', postrm)
            self.assertIn('Restart=on-failure', service)
            self.assertIn('RestartSec=5', service)
            self.assertIn('ExecStart=/usr/bin/throttled --config /etc/throttled.conf', service)
            self.assertIn('/usr/bin/python3 /usr/lib/throttled/throttled.py', wrapper)

    def test_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_debian_package_artifacts_are_ignored_by_git(self):
        result = subprocess.run(
            ['git', 'check-ignore', f'throttled_{__version__}+test_all.deb'],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
