import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'build-deb.sh'


class BuildDebTests(unittest.TestCase):
    def test_help_mentions_usage_and_options(self):
        result = subprocess.run([str(SCRIPT), '--help'], cwd=ROOT, text=True, capture_output=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Usage:', result.stdout)
        self.assertIn('--output-dir', result.stdout)
        self.assertIn('--version', result.stdout)
        self.assertIn('0.12+git.<short-sha>', result.stdout)

    def test_default_version_uses_release_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([str(SCRIPT), '--output-dir', tmp], cwd=ROOT, text=True, capture_output=True)

            self.assertEqual(result.returncode, 0, result.stderr)
            deb = Path(result.stdout.strip())
            self.assertEqual(deb.parent, Path(tmp))
            self.assertTrue(deb.name.startswith('throttled_0.12+git.'), result.stdout)
            self.assertTrue(deb.name.endswith('_all.deb'), result.stdout)
            self.assertTrue(deb.exists(), result.stdout)

    def test_builds_deb_with_expected_metadata_and_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [str(SCRIPT), '--output-dir', tmp, '--version', '0.12+test', '--maintainer', 'Tester <test@example.com>'],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            deb = Path(tmp) / 'throttled_0.12+test_all.deb'
            self.assertTrue(deb.exists(), result.stdout)

            info = subprocess.run(['dpkg-deb', '--info', str(deb)], text=True, capture_output=True, check=True)
            contents = subprocess.run(['dpkg-deb', '--contents', str(deb)], text=True, capture_output=True, check=True)
            control_tar = subprocess.run(['dpkg-deb', '--ctrl-tarfile', str(deb)], capture_output=True, check=True)

            self.assertIn('Package: throttled', info.stdout)
            self.assertIn('Version: 0.12+test', info.stdout)
            self.assertIn('Architecture: all', info.stdout)
            self.assertIn('Maintainer: Tester <test@example.com>', info.stdout)
            self.assertIn('python3 (>= 3.9), python3-dbus-next, pciutils, kmod, upower, systemd', info.stdout)
            self.assertIn('./usr/lib/throttled/throttled.py', contents.stdout)
            self.assertIn('./usr/lib/throttled/mmio.py', contents.stdout)
            self.assertIn('./etc/throttled.conf', contents.stdout)
            self.assertIn('./lib/systemd/system/throttled.service', contents.stdout)
            self.assertNotIn('drwxrwx', contents.stdout)
            self.assertNotIn('-rw-rw', contents.stdout)

            with tarfile.open(fileobj=io.BytesIO(control_tar.stdout), mode='r:*') as archive:
                postinst = archive.extractfile('./postinst').read().decode()
                prerm = archive.extractfile('./prerm').read().decode()
                postrm = archive.extractfile('./postrm').read().decode()

            self.assertIn('systemctl daemon-reload', postinst)
            self.assertIn('systemctl enable throttled.service', postinst)
            self.assertIn('systemctl restart throttled.service', postinst)
            self.assertIn('systemctl stop throttled.service', prerm)
            self.assertIn('systemctl disable throttled.service', prerm)
            self.assertIn('systemctl daemon-reload', postrm)

    def test_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_debian_package_artifacts_are_ignored_by_git(self):
        result = subprocess.run(
            ['git', 'check-ignore', 'throttled_0.12+test_all.deb'],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
