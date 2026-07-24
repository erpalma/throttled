import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from throttled_version import __version__


ROOT = Path(__file__).resolve().parents[1]
STAGE_SCRIPT = ROOT / 'scripts' / 'stage-package.sh'
BUILD_PACKAGES_SCRIPT = ROOT / 'scripts' / 'build-packages.sh'
INSTALL_SCRIPT = ROOT / 'install.sh'


class PackagingTests(unittest.TestCase):
    def test_stage_script_rejects_source_repository(self):
        result = subprocess.run([str(STAGE_SCRIPT), str(ROOT)], cwd=ROOT, text=True, capture_output=True)

        self.assertEqual(result.returncode, 2)
        self.assertIn('Refusing', result.stderr)

    def test_stage_script_creates_native_filesystem_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([str(STAGE_SCRIPT), tmp], cwd=ROOT, text=True, capture_output=True)

            self.assertEqual(result.returncode, 0, result.stderr)
            stage = Path(tmp)
            expected = (
                'etc/throttled.conf',
                'usr/bin/throttled',
                'usr/lib/systemd/system/throttled.service',
                'usr/lib/throttled/mmio.py',
                'usr/lib/throttled/throttled.py',
                'usr/lib/throttled/throttled_version.py',
                'usr/share/doc/throttled/copyright',
            )
            for relative_path in expected:
                self.assertTrue((stage / relative_path).is_file(), relative_path)

            service = (stage / 'usr/lib/systemd/system/throttled.service').read_text()
            wrapper = (stage / 'usr/bin/throttled').read_text()
            self.assertIn('ExecStart=/usr/bin/throttled --config /etc/throttled.conf', service)
            self.assertNotIn('/opt/throttled', service)
            self.assertIn('/usr/lib/throttled/throttled.py', wrapper)

    def test_stage_script_can_render_native_openrc_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [str(STAGE_SCRIPT), '--with-openrc', tmp],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            service = Path(tmp, 'etc/init.d/throttled')
            self.assertTrue(service.is_file())
            self.assertTrue(os.access(service, os.X_OK))
            self.assertIn('command="/usr/bin/throttled"', service.read_text())
            self.assertNotIn('/opt/throttled', service.read_text())

    def test_source_services_use_the_installed_console_script(self):
        systemd = (ROOT / 'systemd/throttled.service').read_text()
        openrc = (ROOT / 'openrc/throttled').read_text()
        runit = (ROOT / 'runit/throttled/run').read_text()

        self.assertIn('/opt/throttled/venv/bin/throttled', systemd)
        self.assertIn('/opt/throttled/venv/bin/throttled', openrc)
        self.assertIn('/opt/throttled/venv/bin/throttled', runit)
        self.assertNotIn('/opt/throttled/throttled.py', systemd + openrc + runit)

    def test_rpm_install_preserves_distribution_service_policy(self):
        postinstall = (ROOT / 'packaging/scripts/rpm/postinstall.sh').read_text()

        self.assertIn('systemctl daemon-reload', postinstall)
        self.assertIn('systemctl is-active --quiet throttled.service', postinstall)
        self.assertIn('systemctl try-restart throttled.service', postinstall)
        self.assertNotIn('systemctl enable', postinstall)

    def test_build_packages_help_and_validation(self):
        help_result = subprocess.run([str(BUILD_PACKAGES_SCRIPT), '--help'], cwd=ROOT, text=True, capture_output=True)
        invalid_result = subprocess.run(
            [str(BUILD_PACKAGES_SCRIPT), '--packager', 'pkg'], cwd=ROOT, text=True, capture_output=True
        )

        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn('Build native throttled packages with nFPM.', help_result.stdout)
        self.assertIn('deb, rpm, or apk', help_result.stdout)
        self.assertEqual(invalid_result.returncode, 2)
        self.assertIn('Unsupported packager: pkg', invalid_result.stderr)

    def test_source_installer_documents_non_interactive_options(self):
        result = subprocess.run([str(INSTALL_SCRIPT), '--help'], cwd=ROOT, text=True, capture_output=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--init NAME', result.stdout)
        self.assertIn('--no-start', result.stdout)

    def test_packaging_scripts_are_executable(self):
        scripts = [
            STAGE_SCRIPT,
            BUILD_PACKAGES_SCRIPT,
            ROOT / 'packaging/throttled',
            *sorted((ROOT / 'packaging/scripts').glob('*/*.sh')),
        ]

        for script in scripts:
            self.assertTrue(os.access(script, os.X_OK), str(script))

    def test_pyproject_and_native_builders_share_release_version(self):
        pyproject = (ROOT / 'pyproject.toml').read_text()
        manifest = (ROOT / 'MANIFEST.in').read_text()
        requirements = [
            line
            for line in (ROOT / 'requirements.txt').read_text().splitlines()
            if line and not line.startswith('#')
        ]
        build_deb = (ROOT / 'scripts/build-deb.sh').read_text()
        build_packages = BUILD_PACKAGES_SCRIPT.read_text()

        self.assertTrue(__version__)
        self.assertIn('version = { attr = "throttled_version.__version__" }', pyproject)
        self.assertIn('include nfpm.yaml', manifest)
        self.assertIn('include docs/static-power-limits.md', manifest)
        self.assertIn('include docs/supported-hardware.md', manifest)
        self.assertIn('recursive-include packaging *', manifest)
        for requirement in requirements:
            self.assertIn(f'"{requirement}"', pyproject)
        self.assertIn('from throttled_version import __version__', build_deb)
        self.assertIn('from throttled_version import __version__', build_packages)

    def test_native_package_artifacts_are_ignored_by_git(self):
        artifacts = (
            f'throttled_{__version__}_all.deb',
            f'throttled-{__version__}-1.noarch.rpm',
            f'throttled_{__version__}-1_noarch.apk',
        )
        for artifact in artifacts:
            result = subprocess.run(
                ['git', 'check-ignore', artifact],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, artifact)


if __name__ == '__main__':
    unittest.main()
