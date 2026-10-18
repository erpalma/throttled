import importlib.util
import io
import os
import tempfile
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


class ConfigValidationTests(unittest.TestCase):
    def write_config(self, text):
        tmp = tempfile.NamedTemporaryFile('w', suffix='.conf', delete=False)
        tmp.write(text)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def monitor_output(self, throttled, cpuid):
        energy_values = iter((2**32 - 1_000_000, 1_000_000))

        def readmsr(msr, *args, **kwargs):
            if msr == 'MSR_RAPL_POWER_UNIT':
                return 14
            if msr == 'MSR_DRAM_ENERGY_STATUS':
                return next(energy_values)
            return 0

        throttled.MONITOR_POWER_DOMAINS = {'DRAM': 'MSR_DRAM_ENERGY_STATUS'}
        throttled.UNSUPPORTED_FEATURES.extend(('UNDERVOLT', 'ICCMAX'))
        with (
            mock.patch.object(throttled, 'readmsr', side_effect=readmsr),
            mock.patch.object(throttled, 'time', side_effect=(0, 1)),
            mock.patch.object(throttled, 'log') as log,
        ):
            throttled.monitor(StopAfterWait(), 1, cpuid=cpuid)

        return next(call.args[0] for call in log.call_args_list if 'DRAM:' in call.args[0])

    def test_load_config_survives_a_partial_undervolt_section(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n[UNDERVOLT]\nCORE: -50\n'
        )

        with mock.patch.object(throttled, 'warning'):
            config = throttled.load_config()

        self.assertEqual(config.getfloat('UNDERVOLT', 'CORE'), -50)
        self.assertEqual(config.getfloat('UNDERVOLT', 'CACHE', fallback=0.0), 0.0)

    def test_load_config_removes_unencodable_undervolt_values(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: True\n'
            '[AC]\nUpdate_Rate_s: 5\n'
            '[UNDERVOLT]\nCORE: -1001\nCACHE: -1000\nGPU: nan\nUNCORE: -inf\nANALOGIO: 1\n'
        )

        with mock.patch.object(throttled, 'warning'), mock.patch.object(throttled, 'log'):
            config = throttled.load_config()

        self.assertFalse(config.has_option('UNDERVOLT', 'CORE'))
        self.assertEqual(config.getfloat('UNDERVOLT', 'CACHE'), -1000)
        self.assertFalse(config.has_option('UNDERVOLT', 'GPU'))
        self.assertFalse(config.has_option('UNDERVOLT', 'UNCORE'))
        self.assertEqual(config.getfloat('UNDERVOLT', 'ANALOGIO'), 0)

    def test_load_config_rejects_iccmax_values_that_overflow_the_field(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n'
            '[ICCMAX.AC]\nCORE: 300\nGPU: 100\nCACHE: nan\n[ICCMAX.BATTERY]\nCORE: 0.1\n'
        )

        with mock.patch.object(throttled, 'warning'):
            config = throttled.load_config()

        self.assertFalse(config.has_option('ICCMAX.AC', 'CORE'))
        self.assertEqual(config.getfloat('ICCMAX.AC', 'GPU'), 100)
        self.assertFalse(config.has_option('ICCMAX.AC', 'CACHE'))
        self.assertFalse(config.has_option('ICCMAX.BATTERY', 'CORE'))

    def test_mchbar_probe_keeps_setpci_stderr_out_of_the_journal(self):
        throttled = load_throttled()
        failure = throttled.CalledProcessError(
            1, ['setpci'], output=b'', stderr=b'setpci: Cannot map ecam region: Operation not permitted.\n'
        )

        with mock.patch.object(throttled, 'check_output', side_effect=failure) as check:
            with mock.patch.object(throttled, 'log') as log:
                self.assertIsNone(throttled._read_mchbar_dword('ecam'))

        self.assertEqual(check.call_args.kwargs['stderr'], throttled.PIPE)
        log.assert_not_called()

    def test_mchbar_probe_surfaces_setpci_stderr_in_debug_mode(self):
        throttled = load_throttled()
        throttled.args.debug = True
        failure = throttled.CalledProcessError(
            1, ['setpci'], output=b'', stderr=b'setpci: Cannot map ecam region: Operation not permitted.\n'
        )

        with mock.patch.object(throttled, 'check_output', side_effect=failure):
            with mock.patch.object(throttled, 'log') as log:
                self.assertIsNone(throttled._read_mchbar_dword('ecam'))

        self.assertIn('Cannot map ecam region', log.call_args.args[0])

    def test_forced_msr_backend_warns_when_lockdown_is_active(self):
        throttled = load_throttled()
        config = throttled.configparser.ConfigParser()
        config.read_dict({'GENERAL': {'Backend': 'msr'}})

        with mock.patch.object(throttled, '_read_sysfs', return_value='none [integrity] confidentiality'):
            with mock.patch.object(throttled, 'warning') as warning:
                self.assertEqual(throttled.select_backend(config), 'msr')

        warning.assert_called_once_with('Kernel lockdown is active: MSR and /dev/mem writes will be blocked.')

    def test_monitor_handles_rapl_counter_wraparound(self):
        throttled = load_throttled()
        energy_values = {
            'MSR_INTEL_PKG_ENERGY_STATUS': iter((2**32 - 10, 5)),
            'MSR_PP1_ENERGY_STATUS': iter((100, 110)),
            'MSR_DRAM_ENERGY_STATUS': iter((100, 120)),
        }

        def readmsr(msr, *args, **kwargs):
            if msr == 'MSR_RAPL_POWER_UNIT':
                return 0
            if msr in energy_values:
                return next(energy_values[msr])
            return 0

        with mock.patch.object(throttled, 'readmsr', side_effect=readmsr):
            with mock.patch.object(throttled, 'time', side_effect=(0, 0, 0, 1, 1, 1)):
                with mock.patch.object(throttled, 'get_undervolt', return_value=dict.fromkeys(throttled.VOLTAGE_PLANES, 0)):
                    with mock.patch.object(throttled, 'get_icc_max', return_value=dict.fromkeys(throttled.CURRENT_PLANES, 0)):
                        with mock.patch.object(throttled, 'log') as log:
                            throttled.monitor(StopAfterWait(), 1)

        output = next(call.args[0] for call in log.call_args_list if 'Package:' in call.args[0])
        self.assertIn('Package: 15.0 W', output)
        self.assertIn('Graphics: 10.0 W', output)
        self.assertIn('DRAM: 20.0 W', output)
        self.assertIn('Total: 35.0 W', output)

    def test_monitor_selects_dram_energy_unit_by_cpu_model(self):
        cases = (
            ('model 63', (6, 63, 2), 30.6),
            ('model 79', (6, 79, 1), 30.6),
            ('model 85', (6, 85, 4), 30.6),
            ('model 87', (6, 87, 1), 30.6),
            ('Broadwell-DE', (6, 86, 2), 30.6),
            ('force', None, 122.1),
        )

        for name, cpuid, expected_watts in cases:
            with self.subTest(name=name):
                throttled = load_throttled()
                output = self.monitor_output(throttled, cpuid)

                self.assertIn(f'DRAM: {expected_watts:.1f} W', output)

    def test_main_passes_checked_cpuid_to_monitor_thread(self):
        throttled = load_throttled()
        config = throttled.configparser.ConfigParser()
        config.read_dict({'GENERAL': {'Enabled': 'True'}, 'AC': {'Update_Rate_s': '5'}})
        parsed_args = SimpleNamespace(
            log=None,
            debug=False,
            config='/tmp/throttled.conf',
            monitor=0.5,
            force=False,
        )
        parser = mock.Mock()
        parser.parse_args.return_value = parsed_args
        cpuid = (6, 85, 4)

        with (
            mock.patch.object(throttled, 'build_arg_parser', return_value=parser),
            mock.patch.object(throttled, 'load_config', return_value=config),
            mock.patch.object(throttled, 'check_kernel'),
            mock.patch.object(throttled, 'check_cpu', return_value=cpuid),
            mock.patch.object(throttled, 'set_msr_allow_writes'),
            mock.patch.object(throttled, 'test_msr_rw_capabilities'),
            mock.patch.object(throttled, 'is_on_battery', return_value=False),
            mock.patch.object(throttled, 'get_cpu_platform_info', return_value={}),
            mock.patch.object(throttled, 'calc_reg_values', return_value={}),
            mock.patch.object(throttled, 'undervolt'),
            mock.patch.object(throttled, 'set_icc_max'),
            mock.patch.object(throttled, 'set_hwp'),
            mock.patch.object(throttled, 'log'),
            mock.patch.object(throttled, 'Thread') as thread,
            mock.patch.object(throttled, 'run_dbus_loop', new=mock.Mock(return_value=object())),
            mock.patch.object(throttled.asyncio, 'run'),
        ):
            throttled.main()

        thread.assert_any_call(target=throttled.monitor, args=(mock.ANY, 0.5, cpuid))

    def test_load_config_disables_malformed_power_limits(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: True\n'
            '[AC]\nUpdate_Rate_s: 5\nPL1_Tdp_W: inf\nPL2_Duration_s: nan\nPL2_Tdp_W: abc\nPL1_Duration_s: 1\n'
        )

        with mock.patch.object(throttled, 'warning'):
            config = throttled.load_config()

        self.assertFalse(config.has_option('AC', 'PL1_Tdp_W'))
        self.assertFalse(config.has_option('AC', 'PL2_Duration_s'))
        self.assertFalse(config.has_option('AC', 'PL2_Tdp_W'))
        self.assertEqual(config.getfloat('AC', 'PL1_Duration_s'), 1)
        self.assertEqual(config.getfloat('AC', 'Update_Rate_s'), 5)

    def test_load_config_still_dies_on_a_nonfinite_update_rate(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config('[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: nan\n')
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                throttled.load_config()

        self.assertIn('must be a finite number', stderr.getvalue())

    def test_load_config_rejects_malformed_control_booleans(self):
        cases = (
            ('GENERAL', 'Enabled'),
            ('GENERAL', 'Autoreload'),
            ('AC', 'HWP_Mode'),
            ('AC', 'Disable_BDPROCHOT'),
            ('BATTERY', 'Disable_BDPROCHOT'),
        )

        for section, option in cases:
            with self.subTest(section=section, option=option):
                throttled = load_throttled()
                throttled.args.config = self.write_config(
                    '[GENERAL]\n'
                    f'Enabled: {"invalid" if option == "Enabled" else "True"}\n'
                    f'Autoreload: {"invalid" if option == "Autoreload" else "False"}\n'
                    '[AC]\nUpdate_Rate_s: 5\n'
                    f'HWP_Mode: {"invalid" if option == "HWP_Mode" else "False"}\n'
                    f'Disable_BDPROCHOT: {"invalid" if section == "AC" and option == "Disable_BDPROCHOT" else "False"}\n'
                    '[BATTERY]\nUpdate_Rate_s: 5\n'
                    f'Disable_BDPROCHOT: {"invalid" if section == "BATTERY" else "False"}\n'
                )
                stderr = io.StringIO()

                with redirect_stderr(stderr), self.assertRaises(SystemExit):
                    throttled.load_config()

                message = stderr.getvalue()
                self.assertIn(option, message)
                self.assertIn(f'[{section}]', message)

    def test_load_config_rejects_an_inherited_malformed_boolean(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nHWP_Mode: invalid\n'
            '[GENERAL]\nEnabled: yes\nAutoreload: no\n'
            '[AC]\nUpdate_Rate_s: 5\n'
        )
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            throttled.load_config()

        self.assertIn('HWP_Mode', stderr.getvalue())
        self.assertIn('[AC]', stderr.getvalue())

    def test_load_config_accepts_supported_boolean_spellings(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: yes\nAutoreload: off\n'
            '[AC]\nUpdate_Rate_s: 5\nHWP_Mode: on\nDisable_BDPROCHOT: 0\n'
            '[BATTERY]\nUpdate_Rate_s: 5\nDisable_BDPROCHOT: 1\n'
        )

        config = throttled.load_config()

        self.assertTrue(config.getboolean('GENERAL', 'Enabled'))
        self.assertFalse(config.getboolean('GENERAL', 'Autoreload'))
        self.assertTrue(config.getboolean('AC', 'HWP_Mode'))
        self.assertFalse(config.getboolean('AC', 'Disable_BDPROCHOT'))
        self.assertTrue(config.getboolean('BATTERY', 'Disable_BDPROCHOT'))

    def test_load_config_accepts_a_battery_only_profile(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: true\nAutoreload: false\n'
            '[BATTERY]\nUpdate_Rate_s: 5\nDisable_BDPROCHOT: false\n'
        )

        config = throttled.load_config()

        self.assertNotIn('AC', config)
        self.assertIn('BATTERY', config)

    def test_monitor_skips_only_the_undervolt_read_when_undervolt_is_unsupported(self):
        throttled = load_throttled()
        throttled.UNSUPPORTED_FEATURES.append('UNDERVOLT')

        with mock.patch.object(throttled, 'readmsr', return_value=0):
            with mock.patch.object(throttled, 'get_undervolt') as get_undervolt:
                with mock.patch.object(
                    throttled, 'get_icc_max', return_value=dict.fromkeys(throttled.CURRENT_PLANES, 0)
                ) as get_icc_max:
                    with mock.patch.object(throttled, 'log') as log:
                        throttled.monitor(StopAfterWait(), 1)

        get_undervolt.assert_not_called()
        get_icc_max.assert_called_once_with(convert=True)
        messages = [call.args[0] for call in log.call_args_list]
        self.assertIn('[D] Undervolt offsets: unsupported', messages)
        self.assertIn('[D] IccMax: CORE: 0.00 A | GPU: 0.00 A | CACHE: 0.00 A', messages)

    def test_monitor_skips_only_the_iccmax_read_when_iccmax_is_unsupported(self):
        throttled = load_throttled()
        throttled.UNSUPPORTED_FEATURES.append('ICCMAX')

        with mock.patch.object(throttled, 'readmsr', return_value=0):
            with mock.patch.object(
                throttled, 'get_undervolt', return_value=dict.fromkeys(throttled.VOLTAGE_PLANES, -50)
            ) as get_undervolt:
                with mock.patch.object(throttled, 'get_icc_max') as get_icc_max:
                    with mock.patch.object(throttled, 'log') as log:
                        throttled.monitor(StopAfterWait(), 1)

        get_undervolt.assert_called_once_with(convert=True)
        get_icc_max.assert_not_called()
        messages = [call.args[0] for call in log.call_args_list]
        self.assertIn('[D] IccMax: unsupported', messages)
        self.assertTrue(any('CORE: -50.00 mV' in message for message in messages))

    def test_monitor_skips_the_oc_mailbox_when_both_probes_fail(self):
        throttled = load_throttled()
        throttled.UNSUPPORTED_FEATURES.extend(('UNDERVOLT', 'ICCMAX'))

        with mock.patch.object(throttled, 'readmsr', return_value=0):
            with mock.patch.object(throttled, 'get_undervolt') as get_undervolt:
                with mock.patch.object(throttled, 'get_icc_max') as get_icc_max:
                    with mock.patch.object(throttled, 'log') as log:
                        throttled.monitor(StopAfterWait(), 1)

        get_undervolt.assert_not_called()
        get_icc_max.assert_not_called()
        messages = [call.args[0] for call in log.call_args_list]
        self.assertIn('[D] Undervolt offsets: unsupported', messages)
        self.assertIn('[D] IccMax: unsupported', messages)

    def test_monitor_keeps_supported_undervolt_output(self):
        throttled = load_throttled()
        undervolt = dict.fromkeys(throttled.VOLTAGE_PLANES, -50)

        with mock.patch.object(throttled, 'readmsr', return_value=0):
            with mock.patch.object(throttled, 'get_undervolt', return_value=undervolt):
                with mock.patch.object(
                    throttled, 'get_icc_max', return_value=dict.fromkeys(throttled.CURRENT_PLANES, 0)
                ):
                    with mock.patch.object(throttled, 'log') as log:
                        throttled.monitor(StopAfterWait(), 1)

        self.assertTrue(any('CORE: -50.00 mV' in call.args[0] for call in log.call_args_list))

    def test_probe_marks_only_undervolt_when_its_mailbox_command_fails(self):
        throttled = load_throttled()

        with mock.patch.object(throttled, 'get_undervolt', side_effect=OSError):
            with mock.patch.object(
                throttled, 'get_icc_max', return_value=dict.fromkeys(throttled.CURRENT_PLANES, 0)
            ):
                with mock.patch.object(throttled, 'readmsr', return_value=0):
                    with mock.patch.object(throttled, 'writemsr'):
                        with mock.patch.object(throttled, 'warning'):
                            with mock.patch.object(throttled, 'log'):
                                throttled.test_msr_rw_capabilities()

        self.assertIn('UNDERVOLT', throttled.UNSUPPORTED_FEATURES)
        self.assertNotIn('ICCMAX', throttled.UNSUPPORTED_FEATURES)

    def test_probe_marks_only_iccmax_when_its_mailbox_command_fails(self):
        throttled = load_throttled()

        with mock.patch.object(
            throttled, 'get_undervolt', return_value=dict.fromkeys(throttled.VOLTAGE_PLANES, 0)
        ):
            with mock.patch.object(throttled, 'get_icc_max', side_effect=OSError):
                with mock.patch.object(throttled, 'readmsr', return_value=0):
                    with mock.patch.object(throttled, 'writemsr'):
                        with mock.patch.object(throttled, 'warning'):
                            with mock.patch.object(throttled, 'log'):
                                throttled.test_msr_rw_capabilities()

        self.assertNotIn('UNDERVOLT', throttled.UNSUPPORTED_FEATURES)
        self.assertIn('ICCMAX', throttled.UNSUPPORTED_FEATURES)

    def test_probe_omits_an_unreadable_monitor_power_domain(self):
        throttled = load_throttled()
        throttled.args.monitor = 1

        def readmsr(msr, *args, **kwargs):
            if msr == 'MSR_PP1_ENERGY_STATUS':
                raise OSError
            return 0

        with mock.patch.object(throttled, 'get_undervolt', return_value={}):
            with mock.patch.object(throttled, 'get_icc_max', return_value={}):
                with mock.patch.object(throttled, 'readmsr', side_effect=readmsr):
                    with mock.patch.object(throttled, 'writemsr'):
                        with mock.patch.object(throttled, 'warning'):
                            with mock.patch.object(throttled, 'log'):
                                throttled.test_msr_rw_capabilities()

        self.assertNotIn('Graphics', throttled.MONITOR_POWER_DOMAINS)
        self.assertIn('Package', throttled.MONITOR_POWER_DOMAINS)
        self.assertIn('DRAM', throttled.MONITOR_POWER_DOMAINS)

        throttled.UNSUPPORTED_FEATURES.extend(('UNDERVOLT', 'ICCMAX'))
        with (
            mock.patch.object(throttled, 'readmsr', side_effect=readmsr),
            mock.patch.object(throttled, 'log'),
        ):
            throttled.monitor(StopAfterWait(), 1)

    def test_monitor_without_power_domains_skips_the_rapl_unit(self):
        throttled = load_throttled()
        forbidden_msrs = {'MSR_RAPL_POWER_UNIT', *throttled.MONITOR_POWER_DOMAINS.values()}
        throttled.MONITOR_POWER_DOMAINS.clear()
        throttled.UNSUPPORTED_FEATURES.extend(('UNDERVOLT', 'ICCMAX'))

        def readmsr(msr, *args, **kwargs):
            self.assertNotIn(msr, forbidden_msrs)
            return 0

        with (
            mock.patch.object(throttled, 'readmsr', side_effect=readmsr),
            mock.patch.object(throttled, 'log') as log,
        ):
            throttled.monitor(StopAfterWait(), 1)

        output = next(call.args[0] for call in log.call_args_list if 'VCore:' in call.args[0])
        self.assertIn('Total: 0.0 W', output)

    def test_set_icc_max_skips_the_mailbox_when_iccmax_is_unsupported(self):
        throttled = load_throttled()
        throttled.UNSUPPORTED_FEATURES.append('ICCMAX')
        config = throttled.configparser.ConfigParser()
        config.add_section('ICCMAX.AC')
        config.set('ICCMAX.AC', 'CORE', '100')

        with mock.patch.object(throttled, 'writemsr') as writemsr:
            throttled.set_icc_max(config, source='AC')

        writemsr.assert_not_called()


    def test_loader_warns_on_unknown_iccmax_plane_keys_but_not_default_keys(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nshared_default: 1\n[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n'
            '[ICCMAX.AC]\nCORE: 100\nCORF: 110\n'
        )

        with mock.patch.object(throttled, 'warning') as warning:
            config = throttled.load_config()

        messages = [call.args[0] for call in warning.call_args_list]
        self.assertTrue(any('Unknown IccMax plane "corf"' in message for message in messages))
        self.assertFalse(any('shared_default' in message for message in messages))
        self.assertEqual(config.get('ICCMAX.AC', 'CORE'), '100')

    def test_loader_removes_malformed_trip_temperatures(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: True\n'
            '[AC]\nUpdate_Rate_s: 5\nTrip_Temp_C: warm\n[BATTERY]\nUpdate_Rate_s: 5\nTrip_Temp_C: nan\n'
        )

        with mock.patch.object(throttled, 'warning') as warning:
            config = throttled.load_config()

        self.assertFalse(config.has_option('AC', 'Trip_Temp_C'))
        self.assertFalse(config.has_option('BATTERY', 'Trip_Temp_C'))
        self.assertEqual(warning.call_count, 2)

    def test_loader_removes_a_malformed_trip_temperature_inherited_from_default(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nTrip_Temp_C: warm\n[GENERAL]\nEnabled: True\n'
            '[AC]\nUpdate_Rate_s: 5\n[BATTERY]\nUpdate_Rate_s: 5\n'
        )

        with mock.patch.object(throttled, 'warning') as warning:
            config = throttled.load_config()

        self.assertIsNone(config.getfloat('AC', 'Trip_Temp_C', fallback=None))
        self.assertIsNone(config.getfloat('BATTERY', 'Trip_Temp_C', fallback=None))
        warning.assert_called_once()
        self.assertIn('[DEFAULT]', warning.call_args.args[0])

    def test_loader_removes_a_default_trip_temperature_masked_by_a_malformed_profile_value(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nTrip_Temp_C: nan\n[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\nTrip_Temp_C: warm\n'
        )

        with mock.patch.object(throttled, 'warning') as warning:
            config = throttled.load_config()

        self.assertIsNone(config.getfloat('AC', 'Trip_Temp_C', fallback=None))
        self.assertEqual(warning.call_count, 2)

    def test_loader_removes_an_interpolation_breaking_trip_temperature(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\nTrip_Temp_C: 95%\n'
        )

        with mock.patch.object(throttled, 'warning') as warning:
            config = throttled.load_config()

        self.assertIsNone(config.getfloat('AC', 'Trip_Temp_C', fallback=None))
        warning.assert_called_once()

    def test_loader_falls_back_to_a_valid_default_trip_temperature(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nTrip_Temp_C: 90\n[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\nTrip_Temp_C: warm\n'
        )

        with mock.patch.object(throttled, 'warning') as warning:
            config = throttled.load_config()

        self.assertEqual(config.getfloat('AC', 'Trip_Temp_C'), 90.0)
        warning.assert_called_once()

    def test_loader_removes_malformed_power_limits_inherited_from_default(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nPL1_Tdp_W: hot\nPL2_Tdp_W: nan\n[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n'
        )

        with mock.patch.object(throttled, 'warning') as warning:
            config = throttled.load_config()

        self.assertIsNone(config.getfloat('AC', 'PL1_Tdp_W', fallback=None))
        self.assertIsNone(config.getfloat('AC', 'PL2_Tdp_W', fallback=None))
        messages = [call.args[0] for call in warning.call_args_list]
        self.assertEqual(len(messages), 2)
        self.assertTrue(all('[DEFAULT]' in message for message in messages))

    def test_loader_shadows_malformed_undervolt_planes_inherited_from_default(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nCORE: deep\n[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n[UNDERVOLT.AC]\nCACHE: -50\n'
        )

        with mock.patch.object(throttled, 'warning') as warning:
            config = throttled.load_config()

        self.assertEqual(config.getfloat('UNDERVOLT.AC', 'CORE', fallback=0.0), 0.0)
        self.assertEqual(config.getfloat('UNDERVOLT.AC', 'CACHE'), -50)
        messages = [call.args[0] for call in warning.call_args_list]
        self.assertTrue(any('[DEFAULT]' in message for message in messages))

    def test_loader_shadows_malformed_iccmax_planes_inherited_from_default(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nCORE: lots\n[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n[ICCMAX.AC]\nCACHE: 100\n'
        )

        with mock.patch.object(throttled, 'warning') as warning:
            config = throttled.load_config()

        self.assertEqual(config.getfloat('ICCMAX.AC', 'CORE'), 0.0)
        messages = [call.args[0] for call in warning.call_args_list]
        self.assertTrue(any('[DEFAULT]' in message for message in messages))

        with mock.patch.object(throttled, 'writemsr') as writemsr:
            throttled.set_icc_max(config, source='AC')

        writemsr.assert_called_once()

    def test_loader_vets_the_synthesized_undervolt_profile_against_default_planes(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nGPU: bogus\n[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n[UNDERVOLT.AC]\nGPU: -50\n'
        )

        with mock.patch.object(throttled, 'warning'):
            config = throttled.load_config()

        self.assertEqual(config.getfloat('UNDERVOLT.AC', 'GPU'), -50)
        self.assertEqual(config.getfloat('UNDERVOLT.BATTERY', 'GPU'), 0)

    def test_loader_lets_the_synthesized_undervolt_profile_inherit_valid_defaults(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nCORE: -100\nCACHE: -100\n[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n'
            '[UNDERVOLT.AC]\nGPU: -50\n'
        )

        with mock.patch.object(throttled, 'warning'):
            config = throttled.load_config()

        self.assertEqual(config.getfloat('UNDERVOLT.BATTERY', 'CORE'), -100)
        self.assertEqual(config.getfloat('UNDERVOLT.BATTERY', 'CACHE'), -100)
        self.assertEqual(config.getfloat('UNDERVOLT.BATTERY', 'GPU'), 0)
        self.assertEqual(config.getfloat('UNDERVOLT.AC', 'CORE'), -100)

    def test_loader_removes_masked_power_limits_from_both_layers(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nPL1_Tdp_W: 45%\n[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\nPL1_Tdp_W: hot\n'
        )

        with mock.patch.object(throttled, 'warning') as warning:
            config = throttled.load_config()

        self.assertIsNone(config.getfloat('AC', 'PL1_Tdp_W', fallback=None))
        messages = [call.args[0] for call in warning.call_args_list]
        self.assertEqual(len(messages), 2)
        self.assertIn('[AC]', messages[0])
        self.assertIn('[DEFAULT]', messages[1])

    def test_loader_shadows_a_default_plane_masked_by_a_malformed_own_value(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nCORE: junk\n[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n[UNDERVOLT.AC]\nCORE: zzz\n'
        )

        with mock.patch.object(throttled, 'warning') as warning:
            config = throttled.load_config()

        self.assertEqual(config.getfloat('UNDERVOLT.AC', 'CORE'), 0.0)
        self.assertEqual(config.getfloat('UNDERVOLT.BATTERY', 'CORE'), 0.0)
        messages = [call.args[0] for call in warning.call_args_list]
        self.assertTrue(any('[UNDERVOLT.AC]' in message for message in messages))
        self.assertTrue(any('[DEFAULT]' in message for message in messages))

    def test_loader_keeps_a_shared_default_undervolt_rejected_by_iccmax(self):
        throttled = load_throttled()
        throttled.args.config = self.write_config(
            '[DEFAULT]\nCORE: -100\nCACHE: -100\n[GENERAL]\nEnabled: True\n[AC]\nUpdate_Rate_s: 5\n'
            '[UNDERVOLT]\nGPU: -50\n[ICCMAX.AC]\nGPU: 100\n'
        )

        with mock.patch.object(throttled, 'warning'):
            config = throttled.load_config()

        self.assertEqual(config.getfloat('UNDERVOLT', 'CORE'), -100)
        self.assertEqual(config.getfloat('UNDERVOLT', 'CACHE'), -100)
        self.assertEqual(config.getfloat('ICCMAX.AC', 'CORE'), 0.0)
        self.assertEqual(config.getfloat('ICCMAX.AC', 'GPU'), 100)


if __name__ == '__main__':
    unittest.main()
