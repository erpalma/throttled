import configparser
import importlib.util
import io
import os
import tempfile
import unittest
from collections import defaultdict
from contextlib import redirect_stderr
from errno import EPERM
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
    config.read_dict(sections)
    return config


class StopAfterWait:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.stopped = True


class FakeSysfs:
    """A throwaway sysfs tree shaped like a Tiger Lake laptop."""

    def __init__(self, root):
        self.root = Path(root)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'{value}\n')
        return path

    def read(self, relative):
        return (self.root / relative).read_text().strip()

    def add_zone(self, name, label, constraints, enabled=1):
        self.write(f'powercap/{name}/name', label)
        self.write(f'powercap/{name}/enabled', enabled)
        for index, (constraint, power_uw, window_us) in enumerate(constraints):
            self.write(f'powercap/{name}/constraint_{index}_name', constraint)
            self.write(f'powercap/{name}/constraint_{index}_power_limit_uw', power_uw)
            self.write(f'powercap/{name}/constraint_{index}_time_window_us', window_us)

    def patch(self, throttled):
        return mock.patch.multiple(
            throttled,
            LOCKDOWN_PATH=str(self.root / 'lockdown'),
            POWERCAP_ZONE_GLOB=str(self.root / 'powercap' / 'intel-rapl*:*'),
            TCC_OFFSET_GLOB=str(self.root / 'pci' / '*' / 'tcc_offset_degree_celsius'),
            THERMAL_COOLING_GLOB=str(self.root / 'thermal' / 'cooling_device*'),
            EPP_GLOB=str(self.root / 'cpu' / 'cpu[0-9]*' / 'cpufreq' / 'energy_performance_preference'),
        )


class SysfsBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sysfs = FakeSysfs(self.tmp.name)
        self.throttled = load_throttled()
        patcher = self.sysfs.patch(self.throttled)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_tiger_lake_zones(self):
        self.sysfs.add_zone(
            'intel-rapl:0', 'package-0', [('long_term', 28000000, 27983872), ('short_term', 44000000, 2440)]
        )
        self.sysfs.add_zone('intel-rapl:0:0', 'core', [('long_term', 0, 976)])
        self.sysfs.add_zone('intel-rapl:1', 'psys', [('long_term', 0, 27983872)])
        self.sysfs.add_zone(
            'intel-rapl-mmio:0',
            'package-0',
            [('long_term', 15000000, 27983872), ('short_term', 44000000, 2440), ('peak_power', 71000000, 0)],
        )

    def test_auto_backend_follows_kernel_lockdown(self):
        throttled = self.throttled
        config = make_config({'GENERAL': {'Backend': 'auto'}})
        for lockdown, expected in (
            ('none [integrity] confidentiality', 'sysfs'),
            ('none integrity [confidentiality]', 'sysfs'),
            ('[none] integrity confidentiality', 'msr'),
        ):
            with self.subTest(lockdown=lockdown):
                self.sysfs.write('lockdown', lockdown)
                self.assertEqual(throttled.select_backend(config), expected)

        os.remove(self.sysfs.root / 'lockdown')
        self.assertEqual(throttled.select_backend(config), 'msr')

    def test_backend_can_be_forced_without_lockdown(self):
        self.sysfs.write('lockdown', '[none] integrity confidentiality')
        self.assertEqual(self.throttled.select_backend(make_config({'GENERAL': {'Backend': 'sysfs'}})), 'sysfs')

    def test_load_config_rejects_an_unknown_backend(self):
        config_path = self.sysfs.write('throttled.conf', '[GENERAL]\nBackend: magic\n[AC]\nUpdate_Rate_s: 5\n')
        self.throttled.args.config = str(config_path)

        with redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit):
                self.throttled.load_config()

        self.assertIn('"Backend" parameter in [GENERAL] must be one of: auto, msr, sysfs', stderr.getvalue())

    def test_load_config_normalises_the_backend_value(self):
        config_path = self.sysfs.write('throttled.conf', '[GENERAL]\nBackend: SysFS \n[AC]\nUpdate_Rate_s: 5\n')
        self.throttled.args.config = str(config_path)

        self.assertEqual(self.throttled.load_config().get('GENERAL', 'Backend'), 'sysfs')

    def test_discovers_only_package_zones_and_their_pl1_pl2_constraints(self):
        self.add_tiger_lake_zones()

        zones = self.throttled.discover_powercap_zones()

        self.assertEqual(
            [os.path.basename(zone['path']) for zone in zones], ['intel-rapl-mmio:0', 'intel-rapl:0']
        )
        for zone in zones:
            self.assertEqual(set(zone['constraints']), {('PL1', 'TW1'), ('PL2', 'TW2')})
        self.assertTrue(zones[0]['constraints'][('PL2', 'TW2')].endswith('constraint_1_'))

    def test_power_limits_are_written_to_every_package_zone(self):
        self.add_tiger_lake_zones()
        self.sysfs.write('powercap/intel-rapl-mmio:0/enabled', 0)
        self.throttled.SYSFS_TARGETS['powercap'] = self.throttled.discover_powercap_zones()

        self.throttled.apply_sysfs_power_limits({'PL1': 29000000, 'TW1': 28000000, 'PL2': 44000000})

        for zone in ('intel-rapl:0', 'intel-rapl-mmio:0'):
            self.assertEqual(self.sysfs.read(f'powercap/{zone}/constraint_0_power_limit_uw'), '29000000')
            self.assertEqual(self.sysfs.read(f'powercap/{zone}/constraint_0_time_window_us'), '28000000')
            self.assertEqual(self.sysfs.read(f'powercap/{zone}/constraint_1_power_limit_uw'), '44000000')
            # unset fields stay untouched
            self.assertEqual(self.sysfs.read(f'powercap/{zone}/constraint_1_time_window_us'), '2440')
            self.assertEqual(self.sysfs.read(f'powercap/{zone}/enabled'), '1')
        self.assertEqual(self.sysfs.read('powercap/intel-rapl-mmio:0/constraint_2_power_limit_uw'), '71000000')

    def test_a_rejected_field_disables_only_that_field(self):
        self.add_tiger_lake_zones()
        locked = self.sysfs.root / 'powercap/intel-rapl:0/constraint_1_time_window_us'
        locked.unlink()
        locked.mkdir()
        self.throttled.SYSFS_TARGETS['powercap'] = self.throttled.discover_powercap_zones()

        with mock.patch.object(self.throttled, 'warning') as warning:
            self.throttled.apply_sysfs_power_limits({'PL1': 29000000, 'TW2': 3000})
            self.throttled.apply_sysfs_power_limits({'PL1': 30000000, 'TW2': 3000})

        warning.assert_called_once()
        self.assertIn('disabling TW2 on this zone', warning.call_args.args[0])
        self.assertEqual(self.sysfs.read('powercap/intel-rapl:0/constraint_0_power_limit_uw'), '30000000')
        self.assertEqual(self.sysfs.read('powercap/intel-rapl-mmio:0/constraint_1_time_window_us'), '3000')

    def test_tcc_target_prefers_the_int340x_attribute(self):
        self.sysfs.write('thermal/cooling_device3/type', 'TCC Offset')
        self.sysfs.write('thermal/cooling_device3/max_state', 63)
        self.sysfs.write('thermal/cooling_device3/cur_state', 0)
        self.sysfs.write('pci/0000:00:04.0/tcc_offset_degree_celsius', 0)

        target = self.throttled.discover_tcc_target()

        self.assertTrue(target['path'].endswith('0000:00:04.0/tcc_offset_degree_celsius'))

    def test_tcc_target_falls_back_to_intel_tcc_cooling_and_clamps(self):
        self.sysfs.write('thermal/cooling_device0/type', 'Processor')
        self.sysfs.write('thermal/cooling_device3/type', 'TCC Offset')
        self.sysfs.write('thermal/cooling_device3/max_state', 31)
        self.sysfs.write('thermal/cooling_device3/cur_state', 0)
        self.throttled.SYSFS_TARGETS['tcc'] = self.throttled.discover_tcc_target()

        with mock.patch.object(self.throttled, 'warning') as warning:
            self.throttled.apply_sysfs_tcc_offset(40)

        self.assertEqual(self.sysfs.read('thermal/cooling_device3/cur_state'), '31')
        self.assertIn('clamping to 31', warning.call_args.args[0])

    def test_a_rejected_tcc_write_disables_the_trip_temperature(self):
        self.sysfs.root.joinpath('pci/0000:00:04.0/tcc_offset_degree_celsius').mkdir(parents=True)
        self.throttled.SYSFS_TARGETS['tcc'] = self.throttled.discover_tcc_target()

        with mock.patch.object(self.throttled, 'warning'):
            self.throttled.apply_sysfs_tcc_offset(15)

        self.assertIsNone(self.throttled.SYSFS_TARGETS['tcc'])

    def test_msr_values_translate_to_sysfs_units(self):
        config = make_config(
            {
                'AC': {'PL1_Tdp_W': '29', 'PL1_Duration_s': '28', 'PL2_Tdp_W': '44.5', 'cTDP': '0'},
                'BATTERY': {'PL2_Duration_S': '0.002', 'cTDP': '1'},
            }
        )
        regs = {
            'AC': {'MSR_TEMPERATURE_TARGET': 15 << 24, 'MSR_PKG_POWER_LIMIT': 1, 'MSR_CONFIG_TDP_CONTROL': 0},
            'BATTERY': {'MSR_PKG_POWER_LIMIT': 1, 'MSR_CONFIG_TDP_CONTROL': 1},
        }

        with mock.patch.object(self.throttled, 'warning') as warning:
            sysfs_regs = self.throttled.to_sysfs_reg_values(regs, config)

        self.assertEqual(
            sysfs_regs['AC'],
            {'TCC_OFFSET': 15, 'POWERCAP': {'PL1': 29000000, 'TW1': 28000000, 'PL2': 44500000}},
        )
        self.assertEqual(sysfs_regs['BATTERY'], {'POWERCAP': {'TW2': 2000}})
        warning.assert_called_once_with('cTDP cannot be changed through kernel interfaces: ignoring it.')

    def test_calc_reg_values_keeps_msr_validation_on_the_sysfs_backend(self):
        throttled = self.throttled
        throttled.BACKEND = 'sysfs'
        platform_info = {'feature_programmable_temperature_target': 1, 'feature_programmable_tdp_limit': 0}
        config = make_config({'AC': {'Trip_Temp_C': '85', 'PL1_Tdp_W': '5000'}})

        with mock.patch.multiple(
            throttled,
            get_critical_temp=mock.Mock(return_value=100),
            get_power_unit=mock.Mock(return_value=0.125),
            get_cur_pkg_power_limits=mock.Mock(return_value={'PL1': 1, 'TW1': 1, 'PL2': 1, 'TW2': 1}),
        ):
            with redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit):
                    throttled.calc_reg_values(platform_info, config)
            self.assertIn('Invalid package power limits in [AC]', stderr.getvalue())

            config.set('AC', 'PL1_Tdp_W', '29')
            regs = throttled.calc_reg_values(platform_info, config)

        self.assertEqual(regs['AC'], {'TCC_OFFSET': 15, 'POWERCAP': {'PL1': 29000000}})

    def test_coverage_check_exits_without_restart_when_nothing_applies(self):
        throttled = self.throttled
        config = make_config({'AC': {'Disable_BDPROCHOT': 'True'}, 'UNDERVOLT': {'CORE': '-100', 'CACHE': '-100'}})

        with mock.patch.object(throttled, 'warning') as warning:
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    throttled.check_sysfs_coverage(config, defaultdict(dict))

        self.assertEqual(raised.exception.code, throttled.EXIT_ACCESS_DENIED)
        warned = [call.args[0] for call in warning.call_args_list]
        self.assertIn('Undervolt cannot be applied through kernel interfaces: ignoring it.', warned)
        self.assertIn('BDPROCHOT cannot be disabled through kernel interfaces: ignoring it.', warned)

    def test_coverage_check_accepts_an_applicable_setting(self):
        throttled = self.throttled
        throttled.SYSFS_TARGETS['tcc'] = {'path': '/nonexistent', 'max': 63}
        config = make_config({'AC': {'Trip_Temp_C': '85'}, 'UNDERVOLT': {'CORE': '-100'}})
        regs = defaultdict(dict, {'AC': {'TCC_OFFSET': 15}})

        with mock.patch.object(throttled, 'warning') as warning:
            throttled.check_sysfs_coverage(config, regs)

        warning.assert_called_once_with('Undervolt cannot be applied through kernel interfaces: ignoring it.')

    def test_capability_probe_never_writes_msrs_on_the_sysfs_backend(self):
        throttled = self.throttled
        throttled.BACKEND = 'sysfs'
        throttled.SYSFS_TARGETS['epp'] = []

        with mock.patch.object(throttled, 'writemsr') as writemsr, mock.patch.object(throttled, 'readmsr'):
            throttled.test_msr_rw_capabilities()

        writemsr.assert_not_called()
        self.assertEqual(throttled.UNSUPPORTED_FEATURES, ['UNDERVOLT', 'ICCMAX', 'HWP'])

    def test_hwp_mode_writes_the_raw_epp_on_every_cpu(self):
        throttled = self.throttled
        throttled.BACKEND = 'sysfs'
        for cpu in (0, 1, 10, 2):
            self.sysfs.write(f'cpu/cpu{cpu}/cpufreq/energy_performance_preference', 'balance_performance')
        throttled.SYSFS_TARGETS['epp'] = throttled.discover_epp_files()

        with mock.patch.object(throttled, 'writemsr') as writemsr:
            throttled.set_hwp(True)

        writemsr.assert_not_called()
        self.assertEqual([Path(path).parts[-3] for path in throttled.SYSFS_TARGETS['epp']], ['cpu0', 'cpu1', 'cpu2', 'cpu10'])
        for cpu in (0, 1, 2, 10):
            self.assertEqual(self.sysfs.read(f'cpu/cpu{cpu}/cpufreq/energy_performance_preference'), '32')

        throttled.set_hwp(False)
        self.assertEqual(self.sysfs.read('cpu/cpu10/cpufreq/energy_performance_preference'), '128')

    def test_a_rejected_epp_write_disables_hwp_mode(self):
        throttled = self.throttled
        throttled.BACKEND = 'sysfs'
        self.sysfs.root.joinpath('cpu/cpu0/cpufreq/energy_performance_preference').mkdir(parents=True)
        throttled.SYSFS_TARGETS['epp'] = throttled.discover_epp_files()

        with mock.patch.object(throttled, 'warning'):
            throttled.set_hwp(True)
            throttled.set_hwp(True)

        self.assertEqual(throttled.UNSUPPORTED_FEATURES, ['HWP'])

    def test_power_thread_uses_only_sysfs_on_the_sysfs_backend(self):
        throttled = self.throttled
        throttled.BACKEND = 'sysfs'
        throttled.args.debug = True
        config = make_config({'GENERAL': {'Enabled': 'True'}, 'AC': {'Update_Rate_s': '5', 'Disable_BDPROCHOT': 'True'}})
        regs = defaultdict(dict, {'AC': {'TCC_OFFSET': 15, 'POWERCAP': {'PL1': 29000000}}})

        with mock.patch.multiple(
            throttled,
            writemsr=mock.DEFAULT,
            read_mchbar_base=mock.DEFAULT,
            get_reset_thermal_status=mock.DEFAULT,
            apply_sysfs_tcc_offset=mock.DEFAULT,
            apply_sysfs_power_limits=mock.DEFAULT,
            set_disable_bdprochot=mock.DEFAULT,
        ) as mocks:
            mocks['get_reset_thermal_status'].return_value = []
            throttled._power_thread({'config': config, 'regs': regs}, StopAfterWait())

        mocks['writemsr'].assert_not_called()
        mocks['read_mchbar_base'].assert_not_called()
        mocks['set_disable_bdprochot'].assert_not_called()
        mocks['get_reset_thermal_status'].assert_called_once_with(reset=False)
        mocks['apply_sysfs_tcc_offset'].assert_called_once_with(15)
        mocks['apply_sysfs_power_limits'].assert_called_once_with({'PL1': 29000000})

    def test_msr_permission_errors_exit_with_the_no_restart_code(self):
        throttled = self.throttled

        with mock.patch.object(throttled, '_ensure_msr_module', return_value=['/dev/cpu/0/msr']):
            with mock.patch.object(throttled.os, 'open', side_effect=PermissionError(EPERM, 'Operation not permitted')):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        throttled.writemsr('MSR_TEMPERATURE_TARGET', 0)

        self.assertEqual(raised.exception.code, throttled.EXIT_ACCESS_DENIED)

    def test_systemd_unit_does_not_restart_on_access_denied(self):
        service = (ROOT / 'systemd' / 'throttled.service').read_text()

        self.assertIn(f'RestartPreventExitStatus={self.throttled.EXIT_ACCESS_DENIED}', service)


if __name__ == '__main__':
    unittest.main()
