#!/usr/bin/env python3
from __future__ import print_function

import argparse
import configparser
import glob
import gzip
import os
import re
import struct
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from errno import EACCES, EIO, EPERM
from multiprocessing import cpu_count
from platform import uname
from subprocess import check_output, CalledProcessError
from threading import Event, Thread
from time import time

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from mmio import MMIO, MMIOError

DEFAULT_SYSFS_POWER_PATH = '/sys/class/power_supply/AC*/online'
VOLTAGE_PLANES = {'CORE': 0, 'GPU': 1, 'CACHE': 2, 'UNCORE': 3, 'ANALOGIO': 4}
CURRENT_PLANES = {'CORE': 0, 'GPU': 1, 'CACHE': 2}
TRIP_TEMP_RANGE = [40, 97]
UNDERVOLT_KEYS = ('UNDERVOLT', 'UNDERVOLT.AC', 'UNDERVOLT.BATTERY')
ICCMAX_KEYS = ('ICCMAX', 'ICCMAX.AC', 'ICCMAX.BATTERY')
power = {'source': None, 'method': 'polling'}
MSR_DICT = {
    'MSR_PLATFORM_INFO': 0xCE,
    'MSR_OC_MAILBOX': 0x150,
    'IA32_PERF_STATUS': 0x198,
    'IA32_THERM_STATUS': 0x19C,
    'MSR_TEMPERATURE_TARGET': 0x1A2,
    'MSR_POWER_CTL': 0x1FC,
    'MSR_RAPL_POWER_UNIT': 0x606,
    'MSR_PKG_POWER_LIMIT': 0x610,
    'MSR_INTEL_PKG_ENERGY_STATUS': 0x611,
    'MSR_DRAM_ENERGY_STATUS': 0x619,
    'MSR_PP1_ENERGY_STATUS': 0x641,
    'MSR_CONFIG_TDP_CONTROL': 0x64B,
    'IA32_HWP_REQUEST': 0x774,
}

HWP_PERFORMANCE_VALUE = 0x20
HWP_DEFAULT_VALUE = 0x80
HWP_INTERVAL = 60


platform_info_bits = {
    'maximum_non_turbo_ratio': [8, 15],
    'maximum_efficiency_ratio': [40, 47],
    'minimum_operating_ratio': [48, 55],
    'feature_ppin_cap': [23, 23],
    'feature_programmable_turbo_ratio': [28, 28],
    'feature_programmable_tdp_limit': [29, 29],
    'number_of_additional_tdp_profiles': [33, 34],
    'feature_programmable_temperature_target': [30, 30],
    'feature_low_power_mode': [32, 32],
}

thermal_status_bits = {
    'thermal_limit_status': [0, 0],
    'thermal_limit_log': [1, 1],
    'prochot_or_forcepr_status': [2, 2],
    'prochot_or_forcepr_log': [3, 3],
    'crit_temp_status': [4, 4],
    'crit_temp_log': [5, 5],
    'thermal_threshold1_status': [6, 6],
    'thermal_threshold1_log': [7, 7],
    'thermal_threshold2_status': [8, 8],
    'thermal_threshold2_log': [9, 9],
    'power_limit_status': [10, 10],
    'power_limit_log': [11, 11],
    'current_limit_status': [12, 12],
    'current_limit_log': [13, 13],
    'cross_domain_limit_status': [14, 14],
    'cross_domain_limit_log': [15, 15],
    'cpu_temp': [16, 22],
    'temp_resolution': [27, 30],
    'reading_valid': [31, 31],
}

supported_cpus = {
    (6, 26, 1): 'Nehalem',
    (6, 26, 2): 'Nehalem-EP',
    (6, 26, 4): 'Bloomfield',
    (6, 28, 2): 'Silverthorne',
    (6, 28, 10): 'PineView',
    (6, 29, 0): 'Dunnington-6C',
    (6, 29, 1): 'Dunnington',
    (6, 30, 0): 'Lynnfield',
    (6, 30, 5): 'Lynnfield_CPUID',
    (6, 31, 1): 'Auburndale',
    (6, 37, 2): 'Clarkdale',
    (6, 38, 1): 'TunnelCreek',
    (6, 39, 2): 'Medfield',
    (6, 42, 2): 'SandyBridge',
    (6, 42, 6): 'SandyBridge',
    (6, 42, 7): 'Sandy Bridge-DT',
    (6, 44, 1): 'Westmere-EP',
    (6, 44, 2): 'Gulftown',
    (6, 45, 5): 'Sandy Bridge-EP',
    (6, 45, 6): 'Sandy Bridge-E',
    (6, 46, 4): 'Beckton',
    (6, 46, 5): 'Beckton',
    (6, 46, 6): 'Beckton',
    (6, 47, 2): 'Eagleton',
    (6, 53, 1): 'Cloverview',
    (6, 54, 1): 'Cedarview-D',
    (6, 54, 9): 'Centerton',
    (6, 55, 3): 'Bay Trail-D',
    (6, 55, 8): 'Silvermont',
    (6, 58, 9): 'Ivy Bridge-DT',
    (6, 60, 3): 'Haswell-DT',
    (6, 61, 4): 'Broadwell-U',
    (6, 62, 3): 'IvyBridgeEP',
    (6, 62, 4): 'Ivy Bridge-E',
    (6, 63, 2): 'Haswell-EP',
    (6, 69, 1): 'HaswellULT',
    (6, 70, 1): 'Crystal Well-DT',
    (6, 71, 1): 'Broadwell-H',
    (6, 76, 3): 'Braswell',
    (6, 77, 8): 'Avoton',
    (6, 78, 3): 'Skylake',
    (6, 79, 1): 'BroadwellE',
    (6, 85, 4): 'SkylakeXeon',
    (6, 85, 6): 'CascadeLakeSP',
    (6, 85, 7): 'CascadeLakeXeon2',
    (6, 86, 2): 'BroadwellDE',
    (6, 86, 4): 'BroadwellDE',
    (6, 87, 0): 'KnightsLanding',
    (6, 87, 1): 'KnightsLanding',
    (6, 90, 0): 'Moorefield',
    (6, 92, 9): 'Apollo Lake',
    (6, 93, 1): 'SoFIA',
    (6, 94, 0): 'Skylake',
    (6, 94, 3): 'Skylake-S',
    (6, 95, 1): 'Denverton',
    (6, 102, 3): 'Cannon Lake-U',
    (6, 117, 10): 'Spreadtrum',
    (6, 122, 1): 'Gemini Lake-D',
    (6, 122, 8): 'GoldmontPlus',
    (6, 126, 5): 'IceLakeY',
    (6, 138, 1): 'Lakefield',
    (6, 140, 1): 'TigerLake-U',
    (6, 140, 2): 'TigerLake-U',
    (6, 141, 1): 'TigerLake-H',
    (6, 142, 9): 'KabyLake',
    (6, 142, 10): 'KabyLake',
    (6, 142, 11): 'WhiskeyLake',
    (6, 142, 12): 'CometLake-U',
    (6, 151, 2): 'AlderLake-S/HX',
    (6, 151, 5): 'AlderLake-S',
    (6, 154, 3): 'AlderLake-P/H',
    (6, 154, 4): 'AlderLake-U',
    (6, 156, 0): 'JasperLake',
    (6, 158, 9): 'KabyLakeG',
    (6, 158, 10): 'CoffeeLake',
    (6, 158, 11): 'CoffeeLake',
    (6, 158, 12): 'CoffeeLake',
    (6, 158, 13): 'CoffeeLake',
    (6, 165, 2): 'CometLake',
    (6, 165, 4): 'CometLake',
    (6, 165, 5): 'CometLake-S',
    (6, 166, 0): 'CometLake',
    (6, 167, 1): 'RocketLake',
    (6, 170, 4): 'MeteorLake',
    (6, 183, 1): 'RaptorLake-HX',
    (6, 186, 2): 'RaptorLake',
    (6, 186, 3): 'RaptorLake-U',
    (6, 189, 1): 'LunarLake',
}

TESTMSR = False
UNSUPPORTED_FEATURES = []


class bcolors:
    YELLOW = '\033[93m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


OK = bcolors.GREEN + bcolors.BOLD + 'OK' + bcolors.RESET
ERR = bcolors.RED + bcolors.BOLD + 'ERR' + bcolors.RESET
LIM = bcolors.YELLOW + bcolors.BOLD + 'LIM' + bcolors.RESET

log_history = set()


def log(msg, oneshot=False, end='\n'):
    outfile = args.log if args.log else sys.stdout
    if msg.strip() not in log_history or oneshot is False:
        tstamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        full_msg = '{:s}: {:s}'.format(tstamp, msg) if args.log else msg
        print(full_msg, file=outfile, end=end)
        log_history.add(msg.strip())


def fatal(msg, code=1, end='\n'):
    outfile = args.log if args.log else sys.stderr
    tstamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    full_msg = '{:s}: [E] {:s}'.format(tstamp, msg) if args.log else '[E] {:s}'.format(msg)
    print(full_msg, file=outfile, end=end)
    sys.exit(code)


def warning(msg, oneshot=True, end='\n'):
    outfile = args.log if args.log else sys.stderr
    if msg.strip() not in log_history or oneshot is False:
        tstamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        full_msg = '{:s}: [W] {:s}'.format(tstamp, msg) if args.log else '[W] {:s}'.format(msg)
        print(full_msg, file=outfile, end=end)
        log_history.add(msg.strip())


def get_msr_list():
    return ['/dev/cpu/{:d}/msr'.format(int(x)) for x in os.listdir("/dev/cpu")]

def writemsr(msr, val):
    msr_list = get_msr_list()
    if not os.path.exists(msr_list[0]):
        try:
            subprocess.check_call(('modprobe', 'msr'))
        except subprocess.CalledProcessError:
            fatal('Unable to load the msr module.')
    try:
        for addr in msr_list:
            f = os.open(addr, os.O_WRONLY)
            os.lseek(f, MSR_DICT[msr], os.SEEK_SET)
            os.write(f, struct.pack('Q', val))
            os.close(f)
    except (IOError, OSError) as e:
        if TESTMSR:
            raise e
        if e.errno == EPERM or e.errno == EACCES:
            fatal(
                'Unable to write to MSR {} ({:x}). Try to disable Secure Boot '
                'and check if your kernel does not restrict access to MSR.'.format(msr, MSR_DICT[msr])
            )
        elif e.errno == EIO:
            fatal('Unable to write to MSR {} ({:x}). Unknown error.'.format(msr, MSR_DICT[msr]))
        else:
            raise e


# returns the value between from_bit and to_bit as unsigned long
def readmsr(msr, from_bit=0, to_bit=63, cpu=None, flatten=False):
    assert cpu is None or cpu in range(cpu_count())
    if from_bit > to_bit:
        fatal('Wrong readmsr bit params')
    msr_list = get_msr_list()
    if not os.path.exists(msr_list[0]):
        try:
            subprocess.check_call(('modprobe', 'msr'))
        except subprocess.CalledProcessError:
            fatal('Unable to load the msr module.')
    try:
        output = []
        for addr in msr_list:
            f = os.open(addr, os.O_RDONLY)
            os.lseek(f, MSR_DICT[msr], os.SEEK_SET)
            val = struct.unpack('Q', os.read(f, 8))[0]
            os.close(f)
            output.append(get_value_for_bits(val, from_bit, to_bit))
        if flatten:
            if len(set(output)) > 1:
                warning('Found multiple values for {:s} ({:x}). This should never happen.'.format(msr, MSR_DICT[msr]))
            return output[0]
        return output[cpu] if cpu is not None else output
    except (IOError, OSError) as e:
        if TESTMSR:
            raise e
        if e.errno == EPERM or e.errno == EACCES:
            fatal('Unable to read from MSR {} ({:x}). Try to disable Secure Boot.'.format(msr, MSR_DICT[msr]))
        elif e.errno == EIO:
            fatal('Unable to read to MSR {} ({:x}). Unknown error.'.format(msr, MSR_DICT[msr]))
        else:
            raise e


def get_value_for_bits(val, from_bit=0, to_bit=63):
    mask = sum(2 ** x for x in range(from_bit, to_bit + 1))
    return (val & mask) >> from_bit


def set_msr_allow_writes():
    log('[I] Trying to unlock MSR allow_writes.')
    if not os.path.exists('/sys/module/msr'):
        try:
            subprocess.check_call(('modprobe', 'msr'))
        except subprocess.CalledProcessError:
            return
    if os.path.exists('/sys/module/msr/parameters/allow_writes'):
        try:
            with open('/sys/module/msr/parameters/allow_writes', 'w') as f:
                f.write('on')
        except:
            warning('Unable to set MSR allow_writes to on. You might experience warnings in kernel logs.')


def is_on_battery(config):
    try:
        for path in glob.glob(config.get('GENERAL', 'Sysfs_Power_Path', fallback=DEFAULT_SYSFS_POWER_PATH)):
            with open(path) as f:
                return not bool(int(f.read()))
        raise
    except:
        warning('No valid Sysfs_Power_Path found! Trying upower method')
    try:
        bus = dbus.SystemBus()
        proxy = bus.get_object('org.freedesktop.UPower', '/org/freedesktop/UPower')
        iface = dbus.Interface(proxy, 'org.freedesktop.DBus.Properties')
        return iface.Get('org.freedesktop.UPower', 'OnBattery')
    except:
        pass

    warning('No valid power detection methods found. Assuming that the system is running on battery power.')
    return True


def get_cpu_platform_info():
    features_msr_value = readmsr('MSR_PLATFORM_INFO', cpu=0)
    cpu_platform_info = {}
    for key, value in platform_info_bits.items():
        cpu_platform_info[key] = int(get_value_for_bits(features_msr_value, value[0], value[1]))
    return cpu_platform_info


def get_reset_thermal_status():
    # read thermal status
    thermal_status_msr_value = readmsr('IA32_THERM_STATUS')
    thermal_status = []
    for core in range(cpu_count()):
        thermal_status_core = {}
        for key, value in thermal_status_bits.items():
            thermal_status_core[key] = int(get_value_for_bits(thermal_status_msr_value[core], value[0], value[1]))
        thermal_status.append(thermal_status_core)
    # reset log bits
    writemsr('IA32_THERM_STATUS', 0)
    return thermal_status


def get_time_unit():
    # 0.000977 is the time unit of my CPU
    # TODO formula might be different for other CPUs
    return 1.0 / 2 ** readmsr('MSR_RAPL_POWER_UNIT', 16, 19, cpu=0)


def get_power_unit():
    # 0.125 is the power unit of my CPU
    # TODO formula might be different for other CPUs
    return 1.0 / 2 ** readmsr('MSR_RAPL_POWER_UNIT', 0, 3, cpu=0)


def get_critical_temp():
    # the critical temperature for my CPU is 100 'C
    return readmsr('MSR_TEMPERATURE_TARGET', 16, 23, cpu=0)


def get_cur_pkg_power_limits():
    value = readmsr('MSR_PKG_POWER_LIMIT', 0, 55, flatten=True)
    return {
        'PL1': get_value_for_bits(value, 0, 14),
        'TW1': get_value_for_bits(value, 17, 23),
        'PL2': get_value_for_bits(value, 32, 46),
        'TW2': get_value_for_bits(value, 49, 55),
    }


def calc_time_window_vars(t):
    time_unit = get_time_unit()
    for Y in range(2 ** 5):
        for Z in range(2 ** 2):
            if t <= (2 ** Y) * (1.0 + Z / 4.0) * time_unit:
                return (Y, Z)
    raise ValueError('Unable to find a good combination!')


def calc_undervolt_msr(plane, offset):
    """Return the value to be written in the MSR 150h for setting the given
    offset voltage (in mV) to the given voltage plane.
    """
    assert offset <= 0
    assert plane in VOLTAGE_PLANES
    offset = int(round(offset * 1.024))
    offset = 0xFFE00000 & ((offset & 0xFFF) << 21)
    return 0x8000001100000000 | (VOLTAGE_PLANES[plane] << 40) | offset


def calc_undervolt_mv(msr_value):
    """Return the offset voltage (in mV) from the given raw MSR 150h value."""
    offset = (msr_value & 0xFFE00000) >> 21
    offset = offset if offset <= 0x400 else -(0x800 - offset)
    return int(round(offset / 1.024))


def get_undervolt(plane=None, convert=False):
    if 'UNDERVOLT' in UNSUPPORTED_FEATURES:
        return 0
    planes = [plane] if plane in VOLTAGE_PLANES else VOLTAGE_PLANES
    out = {}
    for plane in planes:
        writemsr('MSR_OC_MAILBOX', 0x8000001000000000 | (VOLTAGE_PLANES[plane] << 40))
        read_value = readmsr('MSR_OC_MAILBOX', flatten=True) & 0xFFFFFFFF
        out[plane] = calc_undervolt_mv(read_value) if convert else read_value

    return out


def undervolt(config):
    if ('UNDERVOLT.{:s}'.format(power['source']) not in config and 'UNDERVOLT' not in config) or (
        'UNDERVOLT' in UNSUPPORTED_FEATURES
    ):
        return
    for plane in VOLTAGE_PLANES:
        write_offset_mv = config.getfloat(
            'UNDERVOLT.{:s}'.format(power['source']), plane, fallback=config.getfloat('UNDERVOLT', plane, fallback=0.0)
        )
        write_value = calc_undervolt_msr(plane, write_offset_mv)
        writemsr('MSR_OC_MAILBOX', write_value)
        if args.debug:
            write_value &= 0xFFFFFFFF
            read_value = get_undervolt(plane)[plane]
            read_offset_mv = calc_undervolt_mv(read_value)
            match = OK if write_value == read_value else ERR
            log(
                '[D] Undervolt plane {:s} - write {:.0f} mV ({:#x}) - read {:.0f} mV ({:#x}) - match {}'.format(
                    plane, write_offset_mv, write_value, read_offset_mv, read_value, match
                )
            )


def calc_icc_max_msr(plane, current):
    """Return the value to be written in the MSR 150h for setting the given
    IccMax (in A) to the given current plane.
    """
    assert 0 < current <= 0x3FF
    assert plane in CURRENT_PLANES
    current = int(round(current * 4))
    return 0x8000001700000000 | (CURRENT_PLANES[plane] << 40) | current


def calc_icc_max_amp(msr_value):
    """Return the max current (in A) from the given raw MSR 150h value."""
    return (msr_value & 0x3FF) / 4.0


def get_icc_max(plane=None, convert=False):
    planes = [plane] if plane in CURRENT_PLANES else CURRENT_PLANES
    out = {}
    for plane in planes:
        writemsr('MSR_OC_MAILBOX', 0x8000001600000000 | (CURRENT_PLANES[plane] << 40))
        read_value = readmsr('MSR_OC_MAILBOX', flatten=True) & 0x3FF
        out[plane] = calc_icc_max_amp(read_value) if convert else read_value

    return out


def set_icc_max(config):
    for plane in CURRENT_PLANES:
        try:
            write_current_amp = config.getfloat(
                'ICCMAX.{:s}'.format(power['source']), plane, fallback=config.getfloat('ICCMAX', plane, fallback=-1.0)
            )
            if write_current_amp > 0:
                write_value = calc_icc_max_msr(plane, write_current_amp)
                writemsr('MSR_OC_MAILBOX', write_value)
                if args.debug:
                    write_value &= 0x3FF
                    read_value = get_icc_max(plane)[plane]
                    read_current_A = calc_icc_max_amp(read_value)
                    match = OK if write_value == read_value else ERR
                    log(
                        '[D] IccMax plane {:s} - write {:.2f} A ({:#x}) - read {:.2f} A ({:#x}) - match {}'.format(
                            plane, write_current_amp, write_value, read_current_A, read_value, match
                        )
                    )
        except (configparser.NoSectionError, configparser.NoOptionError):
            pass


def load_config():
    config = configparser.ConfigParser()
    config.read(args.config)

    # config values sanity check
    for power_source in ('AC', 'BATTERY'):
        for option in ('Update_Rate_s', 'PL1_Tdp_W', 'PL1_Duration_s', 'PL2_Tdp_W', 'PL2_Duration_S'):
            value = config.getfloat(power_source, option, fallback=None)
            if value is not None:
                value = config.set(power_source, option, str(max(0.001, value)))
            elif option == 'Update_Rate_s':
                fatal('The mandatory "Update_Rate_s" parameter is missing.')

        trip_temp = config.getfloat(power_source, 'Trip_Temp_C', fallback=None)
        if trip_temp is not None:
            valid_trip_temp = min(TRIP_TEMP_RANGE[1], max(TRIP_TEMP_RANGE[0], trip_temp))
            if trip_temp != valid_trip_temp:
                config.set(power_source, 'Trip_Temp_C', str(valid_trip_temp))
                log(
                    '[!] Overriding invalid "Trip_Temp_C" value in "{:s}": {:.1f} -> {:.1f}'.format(
                        power_source, trip_temp, valid_trip_temp
                    )
                )

    # fix any invalid value (ie. > 0) in the undervolt settings
    for key in UNDERVOLT_KEYS:
        for plane in VOLTAGE_PLANES:
            if key in config:
                value = config.getfloat(key, plane)
                valid_value = min(0, value)
                if value != valid_value:
                    config.set(key, plane, str(valid_value))
                    log(
                        '[!] Overriding invalid "{:s}" value in "{:s}" voltage plane: {:.0f} -> {:.0f}'.format(
                            key, plane, value, valid_value
                        )
                    )

    # handle the case where only one of UNDERVOLT.AC, UNDERVOLT.BATTERY keys exists
    # by forcing the other key to all zeros (ie. no undervolt)
    if any(key in config for key in UNDERVOLT_KEYS[1:]):
        for key in UNDERVOLT_KEYS[1:]:
            if key not in config:
                config.add_section(key)
            for plane in VOLTAGE_PLANES:
                value = config.getfloat(key, plane, fallback=0.0)
                config.set(key, plane, str(value))

    # Check for CORE/CACHE values mismatch
    for key in UNDERVOLT_KEYS:
        if key in config:
            if config.getfloat(key, 'CORE', fallback=0) != config.getfloat(key, 'CACHE', fallback=0):
                warning('On Skylake and newer CPUs CORE and CACHE values should match!')
                break

    iccmax_enabled = False
    # check for invalid values (ie. <= 0 or > 0x3FF) in the IccMax settings
    for key in ICCMAX_KEYS:
        for plane in CURRENT_PLANES:
            if key in config:
                try:
                    value = config.getfloat(key, plane)
                    if value <= 0 or value >= 0x3FF:
                        raise ValueError
                    iccmax_enabled = True
                except ValueError:
                    warning('Invalid value for {:s} in {:s}'.format(plane, key), oneshot=False)
                    config.remove_option(key, plane)
                except configparser.NoOptionError:
                    pass
    if iccmax_enabled:
        warning('Warning! Raising IccMax above design limits can damage your system!')

    return config


def calc_reg_values(platform_info, config):
    regs = defaultdict(dict)
    for power_source in ('AC', 'BATTERY'):
        if platform_info['feature_programmable_temperature_target'] != 1:
            warning("Setting temperature target is not supported by this CPU")
        else:
            # the critical temperature for my CPU is 100 'C
            critical_temp = get_critical_temp()
            # update the allowed temp range to keep at least 3 'C from the CPU critical temperature
            global TRIP_TEMP_RANGE
            TRIP_TEMP_RANGE[1] = min(TRIP_TEMP_RANGE[1], critical_temp - 3)

            Trip_Temp_C = config.getfloat(power_source, 'Trip_Temp_C', fallback=None)
            if Trip_Temp_C is not None:
                trip_offset = int(round(critical_temp - Trip_Temp_C))
                regs[power_source]['MSR_TEMPERATURE_TARGET'] = trip_offset << 24
            else:
                log('[I] {:s} trip temperature is disabled in config.'.format(power_source))

        power_unit = get_power_unit()

        PL1_Tdp_W = config.getfloat(power_source, 'PL1_Tdp_W', fallback=None)
        PL1_Duration_s = config.getfloat(power_source, 'PL1_Duration_s', fallback=None)
        PL2_Tdp_W = config.getfloat(power_source, 'PL2_Tdp_W', fallback=None)
        PL2_Duration_s = config.getfloat(power_source, 'PL2_Duration_s', fallback=None)

        if (PL1_Tdp_W, PL1_Duration_s, PL2_Tdp_W, PL2_Duration_s).count(None) < 4:
            cur_pkg_power_limits = get_cur_pkg_power_limits()
            if PL1_Tdp_W is None:
                PL1 = cur_pkg_power_limits['PL1']
                log('[I] {:s} PL1_Tdp_W disabled in config.'.format(power_source))
            else:
                PL1 = int(round(PL1_Tdp_W / power_unit))

            if PL1_Duration_s is None:
                TW1 = cur_pkg_power_limits['TW1']
                log('[I] {:s} PL1_Duration_s disabled in config.'.format(power_source))
            else:
                Y, Z = calc_time_window_vars(PL1_Duration_s)
                TW1 = Y | (Z << 5)

            if PL2_Tdp_W is None:
                PL2 = cur_pkg_power_limits['PL2']
                log('[I] {:s} PL2_Tdp_W disabled in config.'.format(power_source))
            else:
                PL2 = int(round(PL2_Tdp_W / power_unit))

            if PL2_Duration_s is None:
                TW2 = cur_pkg_power_limits['TW2']
                log('[I] {:s} PL2_Duration_s disabled in config.'.format(power_source))
            else:
                Y, Z = calc_time_window_vars(PL2_Duration_s)
                TW2 = Y | (Z << 5)

            regs[power_source]['MSR_PKG_POWER_LIMIT'] = (
                PL1 | (1 << 15) | (1 << 16) | (TW1 << 17) | (PL2 << 32) | (1 << 47) | (TW2 << 49)
            )
        else:
            log('[I] {:s} package power limits are disabled in config.'.format(power_source))

        # cTDP
        c_tdp_target_value = config.getint(power_source, 'cTDP', fallback=None)
        if c_tdp_target_value is not None:
            if platform_info['feature_programmable_tdp_limit'] != 1:
                log("[W] cTDP setting not supported by this CPU")
            elif platform_info['number_of_additional_tdp_profiles'] < c_tdp_target_value:
                log("[W] the configured cTDP profile is not supported by this CPU")
            else:
                valid_c_tdp_target_value = max(0, c_tdp_target_value)
                regs[power_source]['MSR_CONFIG_TDP_CONTROL'] = valid_c_tdp_target_value
    return regs


def set_hwp(performance_mode):
    if performance_mode not in (True, False) or 'HWP' in UNSUPPORTED_FEATURES:
        return
    # set HWP energy performance preference
    cur_val = readmsr('IA32_HWP_REQUEST', cpu=0)
    hwp_mode = HWP_PERFORMANCE_VALUE if performance_mode is True else HWP_DEFAULT_VALUE
    new_val = (cur_val & 0xFFFFFFFF00FFFFFF) | (hwp_mode << 24)

    writemsr('IA32_HWP_REQUEST', new_val)
    if args.debug:
        read_value = readmsr('IA32_HWP_REQUEST', from_bit=24, to_bit=31)[0]
        match = OK if hwp_mode == read_value else ERR
        log('[D] HWP - write "{:#02x}" - read "{:#02x}" - match {}'.format(hwp_mode, read_value, match))


def set_disable_bdprochot():
    # Disable BDPROCHOT
    cur_val = readmsr('MSR_POWER_CTL', flatten=True)
    new_val = cur_val & 0xFFFFFFFFFFFFFFFE

    writemsr('MSR_POWER_CTL', new_val)
    if args.debug:
        read_value = readmsr('MSR_POWER_CTL', from_bit=0, to_bit=0)[0]
        match = OK if ~read_value else ERR
        log('[D] BDPROCHOT - write "{:#02x}" - read "{:#02x}" - match {}'.format(0, read_value, match))


def get_config_write_time():
    try:
        return os.stat(args.config).st_mtime
    except FileNotFoundError:
        return None


def reload_config():
    config = load_config()
    regs = calc_reg_values(get_cpu_platform_info(), config)
    undervolt(config)
    set_icc_max(config)
    set_hwp(config.getboolean('AC', 'HWP_Mode', fallback=None))
    log('[I] Reloading changes.')
    return config, regs


def power_thread(config, regs, exit_event, cpuid):
    try:
        MCHBAR_BASE = int(check_output(('setpci', '-s', '0:0.0', '48.l')), 16)
    except CalledProcessError:
        warning('Please ensure that "setpci" is in path. This is typically provided by the "pciutils" package.')
        warning('Trying to guess the MCHBAR address from the CPUID. This MIGHT NOT WORK!')
        if cpuid in ((6, 140, 1),(6, 140, 2),(6, 141, 1),(6, 151, 2),(6, 151, 5), (6, 154, 3),(6, 154, 4)):
            MCHBAR_BASE = 0xFEDC0001
        else:
            MCHBAR_BASE = 0xFED10001
    try:
        mchbar_mmio = MMIO(MCHBAR_BASE + 0x599F, 8)
    except MMIOError:
        warning('Unable to open /dev/mem. TDP override might not work correctly.')
        warning('Try to disable Secure Boot and/or enable CONFIG_DEVMEM in kernel config.')
        mchbar_mmio = None

    next_hwp_write = 0
    last_config_write_time = (
        get_config_write_time() if config.getboolean('GENERAL', 'Autoreload', fallback=False) else None
    )
    while not exit_event.is_set():
        # log thermal status
        if args.debug:
            thermal_status = get_reset_thermal_status()
            for index, core_thermal_status in enumerate(thermal_status):
                for key, value in core_thermal_status.items():
                    log('[D] core {} thermal status: {} = {}'.format(index, key.replace("_", " "), value))

        # Reload config on changes (unless it's deleted)
        if config.getboolean('GENERAL', 'Autoreload', fallback=False):
            config_write_time = get_config_write_time()
            if config_write_time and last_config_write_time != config_write_time:
                last_config_write_time = config_write_time
                config, regs = reload_config()

        # switch back to sysfs polling
        if power['method'] == 'polling':
            power['source'] = 'BATTERY' if is_on_battery(config) else 'AC'

        # set temperature trip point
        if 'MSR_TEMPERATURE_TARGET' in regs[power['source']]:
            write_value = regs[power['source']]['MSR_TEMPERATURE_TARGET']
            writemsr('MSR_TEMPERATURE_TARGET', write_value)
            if args.debug:
                read_value = readmsr('MSR_TEMPERATURE_TARGET', 24, 29, flatten=True)
                match = OK if write_value >> 24 == read_value else ERR
                log(
                    '[D] TEMPERATURE_TARGET - write {:#x} - read {:#x} - match {}'.format(
                        write_value >> 24, read_value, match
                    )
                )

        # set cTDP
        if 'MSR_CONFIG_TDP_CONTROL' in regs[power['source']]:
            write_value = regs[power['source']]['MSR_CONFIG_TDP_CONTROL']
            writemsr('MSR_CONFIG_TDP_CONTROL', write_value)
            if args.debug:
                read_value = readmsr('MSR_CONFIG_TDP_CONTROL', 0, 1, flatten=True)
                match = OK if write_value == read_value else ERR
                log(
                    '[D] CONFIG_TDP_CONTROL - write {:#x} - read {:#x} - match {}'.format(
                        write_value, read_value, match
                    )
                )

        # set PL1/2 on MSR
        write_value = regs[power['source']]['MSR_PKG_POWER_LIMIT']
        writemsr('MSR_PKG_POWER_LIMIT', write_value)
        if args.debug:
            read_value = readmsr('MSR_PKG_POWER_LIMIT', 0, 55, flatten=True)
            match = OK if write_value == read_value else ERR
            log(
                '[D] MSR PACKAGE_POWER_LIMIT - write {:#x} - read {:#x} - match {}'.format(
                    write_value, read_value, match
                )
            )
        if mchbar_mmio is not None:
            # set MCHBAR register to the same PL1/2 values
            mchbar_mmio.write32(0, write_value & 0xFFFFFFFF)
            mchbar_mmio.write32(4, write_value >> 32)
            if args.debug:
                read_value = mchbar_mmio.read32(0) | (mchbar_mmio.read32(4) << 32)
                match = OK if write_value == read_value else ERR
                log(
                    '[D] MCHBAR PACKAGE_POWER_LIMIT - write {:#x} - read {:#x} - match {}'.format(
                        write_value, read_value, match
                    )
                )

        # Disable BDPROCHOT
        disable_bdprochot = config.getboolean(power['source'], 'Disable_BDPROCHOT', fallback=None)
        if disable_bdprochot:
            set_disable_bdprochot()

        wait_t = config.getfloat(power['source'], 'Update_Rate_s')
        enable_hwp_mode = config.getboolean('AC', 'HWP_Mode', fallback=None)
        # set HWP less frequently. Just to be safe since (e.g.) TLP might reset this value
        if (
            enable_hwp_mode
            and next_hwp_write <= time()
            and (
                (power['method'] == 'dbus' and power['source'] == 'AC')
                or (power['method'] == 'polling' and not is_on_battery(config))
            )
        ):
            set_hwp(enable_hwp_mode)
            next_hwp_write = time() + HWP_INTERVAL

        else:
            exit_event.wait(wait_t)


def check_kernel():
    if os.geteuid() != 0:
        fatal('No root no party. Try again with sudo.')

    kernel_config = None
    try:
        with open(os.path.join('/boot', 'config-{:s}'.format(uname()[2]))) as f:
            kernel_config = f.read()
    except IOError:
        config_gz_path = os.path.join('/proc', 'config.gz')
        try:
            if not os.path.isfile(config_gz_path):
                subprocess.check_call(('modprobe', 'configs'))
            with gzip.open(config_gz_path) as f:
                kernel_config = f.read().decode()
        except (subprocess.CalledProcessError, IOError):
            pass
    if kernel_config is None:
        log('[W] Unable to obtain and validate kernel config.')
        return
    elif not re.search('CONFIG_DEVMEM=y', kernel_config):
        warning('Bad kernel config: you need CONFIG_DEVMEM=y.')
    if not re.search('CONFIG_X86_MSR=(y|m)', kernel_config):
        fatal('Bad kernel config: you need CONFIG_X86_MSR builtin or as module.')


def check_cpu():
    try:
        with open('/proc/cpuinfo') as f:
            cpuinfo = {}
            for row in f.readlines():
                try:
                    key, value = map(lambda x: x.strip(), row.split(':'))
                    if key == 'processor' and value == '1':
                        break
                    try:
                        cpuinfo[key] = int(value, 0)
                    except ValueError:
                        cpuinfo[key] = value
                except ValueError:
                    pass
        if cpuinfo['vendor_id'] != 'GenuineIntel':
            fatal('This tool is designed for Intel CPUs only.')

        cpuid = (cpuinfo['cpu family'], cpuinfo['model'], cpuinfo['stepping'])
        if cpuid not in supported_cpus:
            fatal(
                'Your CPU model is not supported.\n\n'
                'Please open a new issue (https://github.com/erpalma/throttled/issues) specifying:\n'
                ' - model name\n'
                ' - cpu family\n'
                ' - model\n'
                ' - stepping\n'
                'from /proc/cpuinfo.'
            )

        log('[I] Detected CPU architecture: Intel {:s}'.format(supported_cpus[cpuid]))
        return cpuid
    except SystemExit:
        sys.exit(1)
    except:
        fatal('Unable to identify CPU model.')


def test_msr_rw_capabilities():
    TESTMSR = True

    try:
        log('[I] Testing if undervolt is supported...')
        get_undervolt()
    except:
        warning('Undervolt seems not to be supported by your system, disabling.')
        UNSUPPORTED_FEATURES.append('UNDERVOLT')

    try:
        log('[I] Testing if HWP is supported...')
        cur_val = readmsr('IA32_HWP_REQUEST', cpu=0)
        writemsr('IA32_HWP_REQUEST', cur_val)
    except:
        warning('HWP seems not to be supported by your system, disabling.')
        UNSUPPORTED_FEATURES.append('HWP')

    TESTMSR = False


def monitor(exit_event, wait):
    wait = max(0.1, wait)
    rapl_power_unit = 0.5 ** readmsr('MSR_RAPL_POWER_UNIT', from_bit=8, to_bit=12, cpu=0)
    power_plane_msr = {
        'Package': 'MSR_INTEL_PKG_ENERGY_STATUS',
        'Graphics': 'MSR_PP1_ENERGY_STATUS',
        'DRAM': 'MSR_DRAM_ENERGY_STATUS',
    }
    prev_energy = {
        'Package': (readmsr('MSR_INTEL_PKG_ENERGY_STATUS', cpu=0) * rapl_power_unit, time()),
        'Graphics': (readmsr('MSR_PP1_ENERGY_STATUS', cpu=0) * rapl_power_unit, time()),
        'DRAM': (readmsr('MSR_DRAM_ENERGY_STATUS', cpu=0) * rapl_power_unit, time()),
    }

    undervolt_values = get_undervolt(convert=True)
    undervolt_output = ' | '.join('{:s}: {:.2f} mV'.format(plane, undervolt_values[plane]) for plane in VOLTAGE_PLANES)
    log('[D] Undervolt offsets: {:s}'.format(undervolt_output))

    iccmax_values = get_icc_max(convert=True)
    iccmax_output = ' | '.join('{:s}: {:.2f} A'.format(plane, iccmax_values[plane]) for plane in CURRENT_PLANES)
    log('[D] IccMax: {:s}'.format(iccmax_output))

    log('[D] Realtime monitoring of throttling causes:\n')
    while not exit_event.is_set():
        value = readmsr('IA32_THERM_STATUS', from_bit=0, to_bit=15, cpu=0)
        offsets = {'Thermal': 0, 'Power': 10, 'Current': 12, 'Cross-domain (e.g. GPU)': 14}
        output = ('{:s}: {:s}'.format(cause, LIM if bool((value >> offsets[cause]) & 1) else OK) for cause in offsets)

        # ugly code, just testing...
        vcore = readmsr('IA32_PERF_STATUS', from_bit=32, to_bit=47, cpu=0) / (2.0 ** 13) * 1000
        stats2 = {'VCore': '{:.0f} mV'.format(vcore)}
        total = 0.0
        for power_plane in ('Package', 'Graphics', 'DRAM'):
            energy_j = readmsr(power_plane_msr[power_plane], cpu=0) * rapl_power_unit
            now = time()
            prev_energy[power_plane], energy_w = (
                (energy_j, now),
                (energy_j - prev_energy[power_plane][0]) / (now - prev_energy[power_plane][1]),
            )
            stats2[power_plane] = '{:.1f} W'.format(energy_w)
            total += energy_w

        stats2['Total'] = '{:.1f} W'.format(total)

        output2 = ('{:s}: {:s}'.format(label, stats2[label]) for label in stats2)
        terminator = '\n' if args.log else '\r'
        log(
            '[{}] {}  ||  {}{}'.format(power['source'], ' - '.join(output), ' - '.join(output2), ' ' * 10),
            end=terminator,
        )
        exit_event.wait(wait)


def main():
    global args

    parser = argparse.ArgumentParser()
    exclusive_group = parser.add_mutually_exclusive_group()
    exclusive_group.add_argument('--debug', action='store_true', help='add some debug info and additional checks')
    exclusive_group.add_argument(
        '--monitor',
        metavar='update_rate',
        const=1.0,
        type=float,
        nargs='?',
        help='realtime monitoring of throttling causes (default 1s)',
    )
    parser.add_argument('--config', default='/etc/throttled.conf', help='override default config file path')
    parser.add_argument('--force', action='store_true', help='bypass compatibility checks (EXPERTS only)')
    parser.add_argument('--log', metavar='/path/to/file', help='log to file instead of stdout')
    args = parser.parse_args()

    if args.log:
        try:
            args.log = open(args.log, 'w')
        except:
            args.log = None
            fatal('Unable to write to the log file!')

    if not args.force:
        check_kernel()
        cpuid = check_cpu()

    set_msr_allow_writes()

    test_msr_rw_capabilities()

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    log('[I] Loading config file.')
    config = load_config()
    power['source'] = 'BATTERY' if is_on_battery(config) else 'AC'

    platform_info = get_cpu_platform_info()
    if args.debug:
        for key, value in platform_info.items():
            log('[D] cpu platform info: {} = {}'.format(key.replace("_", " "), value))
    regs = calc_reg_values(platform_info, config)

    if not config.getboolean('GENERAL', 'Enabled'):
        log('[I] Throttled is disabled in config file... Quitting. :(')
        return

    undervolt(config)
    set_icc_max(config)
    set_hwp(config.getboolean('AC', 'HWP_Mode', fallback=None))

    exit_event = Event()
    thread = Thread(target=power_thread, args=(config, regs, exit_event, cpuid))
    thread.daemon = True
    thread.start()

    # handle dbus events for applying undervolt/IccMax on resume from sleep/hibernate
    def handle_sleep_callback(sleeping):
        if not sleeping:
            undervolt(config)
            set_icc_max(config)

    def handle_ac_callback(if_name, changed, invalidated):
        if "OnBattery" in changed:
            power['method'] = 'dbus'
            power['source'] = 'BATTERY' if bool(changed['OnBattery']) else 'AC'

    # add dbus receiver only if undervolt/IccMax is enabled in config
    if any(
        config.getfloat(key, plane, fallback=0) != 0 for plane in VOLTAGE_PLANES for key in UNDERVOLT_KEYS + ICCMAX_KEYS
    ):
        bus.add_signal_receiver(
            handle_sleep_callback, 'PrepareForSleep', 'org.freedesktop.login1.Manager', 'org.freedesktop.login1'
        )
    bus.add_signal_receiver(
        handle_ac_callback,
        signal_name="PropertiesChanged",
        dbus_interface="org.freedesktop.DBus.Properties",
        path="/org/freedesktop/UPower",
    )

    log('[I] Starting main loop.')

    if args.monitor is not None:
        monitor_thread = Thread(target=monitor, args=(exit_event, args.monitor))
        monitor_thread.daemon = True
        monitor_thread.start()

    try:
        loop = GLib.MainLoop()
        loop.run()
    except (KeyboardInterrupt, SystemExit):
        pass

    exit_event.set()
    loop.quit()
    thread.join(timeout=1)
    if args.monitor is not None:
        monitor_thread.join(timeout=0.1)


if __name__ == '__main__':
    main()
