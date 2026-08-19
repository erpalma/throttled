#!/usr/bin/env python3
import argparse
import asyncio
import configparser
import glob
import gzip
import math
import os
import re
import struct
import subprocess
import sys
import traceback
from collections import defaultdict
from datetime import datetime
from errno import EACCES, EIO, ENOENT, EPERM
from platform import uname
from subprocess import check_output, CalledProcessError, PIPE
from threading import Event, Lock, Thread, current_thread, main_thread
from time import time

from mmio import MMIO, MMIOError
from throttled_version import __version__

DEFAULT_SYSFS_POWER_PATH = '/sys/class/power_supply/AC*/online'
UPOWER_SERVICE = 'org.freedesktop.UPower'
UPOWER_PATH = '/org/freedesktop/UPower'
LOGIN1_SERVICE = 'org.freedesktop.login1'
LOGIN1_PATH = '/org/freedesktop/login1'
LOGIN1_MANAGER_INTERFACE = 'org.freedesktop.login1.Manager'
DBUS_PROPERTIES_INTERFACE = 'org.freedesktop.DBus.Properties'
VOLTAGE_PLANES = {'CORE': 0, 'GPU': 1, 'CACHE': 2, 'UNCORE': 3, 'ANALOGIO': 4}
CURRENT_PLANES = {'CORE': 0, 'GPU': 1, 'CACHE': 2}
TRIP_TEMP_RANGE = [40, 97]
PKG_POWER_LIMIT_POWER_MASK = (1 << 15) - 1
PKG_POWER_LIMIT_TIME_WINDOW_MASK = (1 << 7) - 1
POWER_PROFILES = ('AC', 'BATTERY')
UNDERVOLT_KEYS = ('UNDERVOLT', 'UNDERVOLT.AC', 'UNDERVOLT.BATTERY')
ICCMAX_KEYS = ('ICCMAX', 'ICCMAX.AC', 'ICCMAX.BATTERY')
power = {'source': None, 'method': 'polling'}
# serializes config publication with the resume callback's read-then-write
config_lock = Lock()
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
UNDERVOLT_TICKS_PER_MV = 1.024
UNDERVOLT_MIN_TICKS = -(1 << 10)
UNDERVOLT_MAX_TICKS = 0
ICCMAX_STEPS_PER_A = 4
ICCMAX_MAX_FIELD = 0x3FF
MCHBAR_ENABLE_BIT = 1
MCHBAR_PACKAGE_POWER_LIMIT_OFFSET = 0x59A0
MCHBAR_PACKAGE_POWER_LIMIT_SIZE = 8
PCI_HOST_BRIDGE_SYSFS_PATH = '/sys/bus/pci/devices/0000:00:00.0'
PCI_VENDOR_ID_INTEL = 0x8086
# The masked address bits also encode the per-generation window alignment, so
# rejecting any bit outside the mask fully validates the BAR before /dev/mem
# is ever opened.
MCHBAR_ADDRESS_MASK_39_15 = ((1 << 39) - 1) & ~((1 << 15) - 1)
MCHBAR_ADDRESS_MASK_39_17 = ((1 << 39) - 1) & ~((1 << 17) - 1)
MCHBAR_ADDRESS_MASK_42_17 = ((1 << 42) - 1) & ~((1 << 17) - 1)


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
    (6, 37, 5): 'Arrandale',
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
    (6, 181, 0): 'ArrowLake-U',
    (6, 183, 1): 'RaptorLake-HX',
    (6, 186, 2): 'RaptorLake',
    (6, 186, 3): 'RaptorLake-U',
    (6, 189, 1): 'LunarLake',
    (6, 190, 0): 'AlderLake-N',
    (6, 198, 2): 'ArrowLake-HX',
}

# MCHBAR belongs to the PCI host bridge, not to a CPUID signature. This is a
# deliberately narrow allowlist: an unlisted device remains MSR-only.
#
# TGL/ADL/RPL/Core Ultra datasheets document PACKAGE_RAPL_LIMIT_0_0_0_MCHBAR_PCU
# at MCHBAR + 0x59a0 (Intel docs 631122, 767625/767626, 764981/767624, 795258,
# 819323, 844345). Kaby and Coffee Lake do not publish the offset: those two
# rest on coreboot's MCH_PKG_POWER_LIMIT_LO (the MSR 0x610 mirror) and a live
# MMIO == MSR equality check on 0x3ec4. Groups follow Linux igen6/ie31200 EDAC.
MCHBAR_ADDRESS_MASKS_BY_PCI_DEVICE = {
    # Kaby Lake-U/R and Coffee Lake-H target systems (T480/T480s/X1C6, P53).
    0x5914: MCHBAR_ADDRESS_MASK_39_15,
    0x3EC4: MCHBAR_ADDRESS_MASK_39_15,
    # Tiger Lake (Linux igen6 tgl_cfg).
    0x9A14: MCHBAR_ADDRESS_MASK_39_17,
    # Alder Lake (Linux igen6 adl_cfg).
    0x4601: MCHBAR_ADDRESS_MASK_42_17,
    0x4602: MCHBAR_ADDRESS_MASK_42_17,
    0x4621: MCHBAR_ADDRESS_MASK_42_17,
    0x4641: MCHBAR_ADDRESS_MASK_42_17,
    # Alder/Raptor Lake S/HX (Linux ie31200 rpl_s_cfg).
    0x4660: MCHBAR_ADDRESS_MASK_42_17,
    0x4668: MCHBAR_ADDRESS_MASK_42_17,
    0x4648: MCHBAR_ADDRESS_MASK_42_17,
    0xA703: MCHBAR_ADDRESS_MASK_42_17,
    0x4640: MCHBAR_ADDRESS_MASK_42_17,
    0x4630: MCHBAR_ADDRESS_MASK_42_17,
    0xA700: MCHBAR_ADDRESS_MASK_42_17,
    0xA740: MCHBAR_ADDRESS_MASK_42_17,
    0xA704: MCHBAR_ADDRESS_MASK_42_17,
    0xA702: MCHBAR_ADDRESS_MASK_42_17,
    # Raptor Lake-P (Linux igen6 rpl_p_cfg).
    0xA706: MCHBAR_ADDRESS_MASK_42_17,
    0xA707: MCHBAR_ADDRESS_MASK_42_17,
    0xA708: MCHBAR_ADDRESS_MASK_42_17,
    0xA716: MCHBAR_ADDRESS_MASK_42_17,
    0xA718: MCHBAR_ADDRESS_MASK_42_17,
    # Meteor Lake and Arrow Lake-U/H (Linux igen6 mtl_ps/mtl_p_cfg).
    0x7D21: MCHBAR_ADDRESS_MASK_42_17,
    0x7D22: MCHBAR_ADDRESS_MASK_42_17,
    0x7D23: MCHBAR_ADDRESS_MASK_42_17,
    0x7D24: MCHBAR_ADDRESS_MASK_42_17,
    0x7D01: MCHBAR_ADDRESS_MASK_42_17,
    0x7D02: MCHBAR_ADDRESS_MASK_42_17,
    0x7D14: MCHBAR_ADDRESS_MASK_42_17,
    0x7D06: MCHBAR_ADDRESS_MASK_42_17,
    0x7D20: MCHBAR_ADDRESS_MASK_42_17,
    0x7D30: MCHBAR_ADDRESS_MASK_42_17,
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

ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*m')


def _format(prefix, msg):
    if args.log:
        tstamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        return f'{tstamp}: {prefix}{ANSI_ESCAPE_RE.sub("", msg)}'
    return f'{prefix}{msg}'


def log(msg, oneshot=False, end='\n'):
    outfile = args.log if args.log else sys.stdout
    if not oneshot or msg.strip() not in log_history:
        print(_format('', msg), file=outfile, end=end)
        if oneshot:
            log_history.add(msg.strip())


def fatal(msg, code=1, end='\n'):
    outfile = args.log if args.log else sys.stderr
    print(_format('[E] ', msg), file=outfile, end=end)
    if current_thread() is not main_thread():
        # sys.exit() would only kill the calling thread, leaving a zombie
        # daemon that looks healthy to systemd but no longer touches MSRs
        outfile.flush()
        os._exit(code)
    sys.exit(code)


def warning(msg, oneshot=True, end='\n'):
    outfile = args.log if args.log else sys.stderr
    if not oneshot or msg.strip() not in log_history:
        print(_format('[W] ', msg), file=outfile, end=end)
        if oneshot:
            log_history.add(msg.strip())


def get_cpu_list():
    """Return sorted CPU indices that expose a /dev/cpu/N entry."""
    try:
        entries = os.listdir('/dev/cpu')
    except FileNotFoundError:
        return []
    return sorted(int(x) for x in entries if x.isdigit())


def get_msr_list():
    """Return the per-CPU MSR device paths in CPU-index order."""
    return [f'/dev/cpu/{cpu:d}/msr' for cpu in get_cpu_list()]


def _ensure_msr_module(cpu=None):
    """Return all MSR devices, or the MSR device for CPU N, loading the module if needed."""
    if cpu is not None:
        target = f'/dev/cpu/{cpu:d}/msr'
        if not os.path.exists(target) and not os.path.exists('/sys/module/msr'):
            try:
                subprocess.check_call(('modprobe', 'msr'))
            except subprocess.CalledProcessError:
                fatal('Unable to load the msr module.')
        if not os.path.exists(target):
            fatal(f'CPU {cpu:d} has no MSR device under /dev/cpu; it may have gone offline.')
        return target

    msr_list = get_msr_list()
    if not msr_list or not os.path.exists(msr_list[0]):
        try:
            subprocess.check_call(('modprobe', 'msr'))
        except subprocess.CalledProcessError:
            fatal('Unable to load the msr module.')
        msr_list = get_msr_list()
    if not msr_list or not os.path.exists(msr_list[0]):
        fatal('No MSR devices found under /dev/cpu after loading the msr module.')
    return msr_list


def writemsr(msr, val, cpu=None):
    """Write a 64-bit value to the named MSR on every online CPU or CPU N."""
    if cpu is not None and cpu < 0:
        fatal('Wrong writemsr cpu param')
    try:
        msr_list = [_ensure_msr_module(cpu)] if cpu is not None else _ensure_msr_module()
        for addr in msr_list:
            f = os.open(addr, os.O_WRONLY)
            try:
                os.lseek(f, MSR_DICT[msr], os.SEEK_SET)
                os.write(f, struct.pack('Q', val))
            finally:
                os.close(f)
    except (IOError, OSError) as e:
        if TESTMSR:
            raise e
        if cpu is not None and e.errno == ENOENT:
            fatal(f'CPU {cpu:d} went offline while writing MSR {msr} ({MSR_DICT[msr]:x}); aborting.')
        if e.errno == EPERM or e.errno == EACCES:
            fatal(
                f'Unable to write to MSR {msr} ({MSR_DICT[msr]:x}). Check that the msr kernel module '
                'is loaded with allow_writes=on and that kernel lockdown is disabled (many kernels '
                'enable lockdown automatically when Secure Boot is on).'
            )
        elif e.errno == EIO:
            fatal(f'Unable to write to MSR {msr} ({MSR_DICT[msr]:x}). Unknown error.')
        else:
            raise e


def readmsr(msr, from_bit=0, to_bit=63, cpu=None, flatten=False):
    """Read the named MSR and return the [from_bit, to_bit] field as
    an unsigned integer. By default returns one value per CPU; with
    cpu=N returns just CPU N, with flatten=True returns the shared value
    (warning if CPUs disagree).
    """
    if cpu is not None and cpu < 0:
        fatal('Wrong readmsr cpu param')
    if from_bit > to_bit:
        fatal('Wrong readmsr bit params')
    try:
        msr_list = [_ensure_msr_module(cpu)] if cpu is not None else _ensure_msr_module()
        output = []
        for addr in msr_list:
            f = os.open(addr, os.O_RDONLY)
            try:
                os.lseek(f, MSR_DICT[msr], os.SEEK_SET)
                val = struct.unpack('Q', os.read(f, 8))[0]
            finally:
                os.close(f)
            output.append(get_value_for_bits(val, from_bit, to_bit))
        if flatten:
            if len(set(output)) > 1:
                warning(f'Found multiple values for {msr:s} ({MSR_DICT[msr]:x}). This should never happen.')
            return output[0]
        if cpu is not None:
            return output[0]
        return output
    except (IOError, OSError) as e:
        if TESTMSR:
            raise e
        if cpu is not None and e.errno == ENOENT:
            fatal(f'CPU {cpu:d} went offline while reading MSR {msr} ({MSR_DICT[msr]:x}); aborting.')
        if e.errno == EPERM or e.errno == EACCES:
            fatal(
                f'Unable to read from MSR {msr} ({MSR_DICT[msr]:x}). Check that the msr kernel module '
                'is loaded and not restricted by kernel lockdown.'
            )
        elif e.errno == EIO:
            fatal(f'Unable to read to MSR {msr} ({MSR_DICT[msr]:x}). Unknown error.')
        else:
            raise e


def get_value_for_bits(val, from_bit=0, to_bit=63):
    """Extract bits [from_bit, to_bit] (inclusive) from val."""
    mask = sum(2**x for x in range(from_bit, to_bit + 1))
    return (val & mask) >> from_bit


def set_msr_allow_writes():
    """Try to enable msr.allow_writes; tolerate kernels that don't expose it."""
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
        except OSError:
            warning('Unable to set MSR allow_writes to on. You might experience warnings in kernel logs.')


def get_dbus_fast():
    """Import dbus-fast lazily so tests and --help do not need a live DBus stack."""
    from dbus_fast.aio import MessageBus
    from dbus_fast.constants import BusType

    return MessageBus, BusType


def unwrap_dbus_value(value):
    return value.value if hasattr(value, 'value') else value


async def get_upower_on_battery_async():
    MessageBus, BusType = get_dbus_fast()
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        introspection = await bus.introspect(UPOWER_SERVICE, UPOWER_PATH)
        upower = bus.get_proxy_object(UPOWER_SERVICE, UPOWER_PATH, introspection)
        properties = upower.get_interface(DBUS_PROPERTIES_INTERFACE)
        return bool(unwrap_dbus_value(await properties.call_get(UPOWER_SERVICE, 'OnBattery')))
    finally:
        bus.disconnect()


def get_upower_on_battery():
    return asyncio.run(get_upower_on_battery_async())


def is_on_battery(config):
    """Return True if the system is on battery power.

    Every adapter matched by Sysfs_Power_Path is checked and any one online
    means AC; falls back to UPower over D-Bus on unreadable paths.
    """
    paths = sorted(glob.glob(config.get('GENERAL', 'Sysfs_Power_Path', fallback=DEFAULT_SYSFS_POWER_PATH)))
    values = []
    errors = []
    for path in paths:
        try:
            with open(path) as f:
                value = int(f.read())
            if value not in (0, 1):
                raise ValueError(f'expected 0 or 1, got {value!r}')
            values.append(value)
        except (IOError, OSError, ValueError) as e:
            errors.append(f'{path}: {e}')

    if values and any(value == 1 for value in values):
        if errors:
            warning(f'Sysfs_Power_Path read failed for {len(errors)} path(s): {"; ".join(errors)}')
        return False
    if values and not errors:
        return True
    if errors:
        warning(f'Sysfs_Power_Path read failed for {len(errors)} path(s): {"; ".join(errors)}. Trying upower method.')
    else:
        warning('No valid Sysfs_Power_Path found! Trying upower method')
    try:
        return get_upower_on_battery()
    except Exception:
        pass

    warning('No valid power detection methods found. Assuming that the system is running on battery power.')
    return True


def _current_config(config_or_state):
    return config_or_state['config'] if isinstance(config_or_state, dict) else config_or_state


def config_is_enabled(config):
    """Return whether hardware changes are enabled in the loaded config."""
    return config.getboolean('GENERAL', 'Enabled', fallback=False)


def handle_sleep_prepare(sleeping, config_or_state):
    if not sleeping:
        with config_lock:
            config = _current_config(config_or_state)
            if config_is_enabled(config):
                undervolt(config)
                set_icc_max(config)


def handle_ac_properties_changed(if_name, changed, invalidated):
    if "OnBattery" in changed:
        power['method'] = 'dbus'
        power['source'] = 'BATTERY' if bool(unwrap_dbus_value(changed['OnBattery'])) else 'AC'


def should_listen_for_resume(config):
    return config_is_enabled(config) and any(
        config.getfloat(key, plane, fallback=0) != 0
        for keys, planes in ((UNDERVOLT_KEYS, VOLTAGE_PLANES), (ICCMAX_KEYS, CURRENT_PLANES))
        for key in keys
        for plane in planes
    )


async def setup_dbus_signal_handlers(config_or_state):
    config = _current_config(config_or_state)
    MessageBus, BusType = get_dbus_fast()
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    context = {'bus': bus}
    try:
        upower_introspection = await bus.introspect(UPOWER_SERVICE, UPOWER_PATH)
        upower = bus.get_proxy_object(UPOWER_SERVICE, UPOWER_PATH, upower_introspection)
        upower_properties = upower.get_interface(DBUS_PROPERTIES_INTERFACE)
        upower_properties.on_properties_changed(handle_ac_properties_changed)
        context['upower'] = upower
        context['upower_properties'] = upower_properties

        if should_listen_for_resume(config):
            login1_introspection = await bus.introspect(LOGIN1_SERVICE, LOGIN1_PATH)
            login1 = bus.get_proxy_object(LOGIN1_SERVICE, LOGIN1_PATH, login1_introspection)
            login1_manager = login1.get_interface(LOGIN1_MANAGER_INTERFACE)
            login1_manager.on_prepare_for_sleep(lambda sleeping: handle_sleep_prepare(sleeping, config_or_state))
            context['login1'] = login1
            context['login1_manager'] = login1_manager

        return context
    except Exception:
        bus.disconnect()
        raise


async def run_dbus_loop(config_or_state):
    context = await setup_dbus_signal_handlers(config_or_state)
    try:
        await asyncio.Future()
    finally:
        context['bus'].disconnect()


def get_cpu_platform_info():
    """Decode MSR_PLATFORM_INFO into a dict of named feature bits."""
    features_msr_value = readmsr('MSR_PLATFORM_INFO', cpu=0)
    cpu_platform_info = {}
    for key, value in platform_info_bits.items():
        cpu_platform_info[key] = int(get_value_for_bits(features_msr_value, value[0], value[1]))
    return cpu_platform_info


def get_reset_thermal_status():
    """Read IA32_THERM_STATUS for every CPU, then clear the sticky log bits."""
    thermal_status_msr_value = readmsr('IA32_THERM_STATUS')
    thermal_status = []
    for msr_value in thermal_status_msr_value:
        thermal_status_core = {}
        for key, value in thermal_status_bits.items():
            thermal_status_core[key] = int(get_value_for_bits(msr_value, value[0], value[1]))
        thermal_status.append(thermal_status_core)
    # reset log bits
    writemsr('IA32_THERM_STATUS', 0)
    return thermal_status


def get_time_unit():
    """Return the RAPL time unit in seconds (Intel SDM Vol. 4, MSR 0x606)."""
    return 1.0 / 2 ** readmsr('MSR_RAPL_POWER_UNIT', 16, 19, cpu=0)


def get_power_unit():
    """Return the RAPL power unit in watts (Intel SDM Vol. 4, MSR 0x606)."""
    return 1.0 / 2 ** readmsr('MSR_RAPL_POWER_UNIT', 0, 3, cpu=0)


def get_critical_temp():
    """Return the package critical temperature offset in degrees Celsius."""
    return readmsr('MSR_TEMPERATURE_TARGET', 16, 23, cpu=0)


def get_cur_pkg_power_limits():
    """Return the current PL1/PL2 power and time-window fields from
    MSR_PKG_POWER_LIMIT."""
    value = readmsr('MSR_PKG_POWER_LIMIT', 0, 55, flatten=True)
    return {
        'PL1': get_value_for_bits(value, 0, 14),
        'TW1': get_value_for_bits(value, 17, 23),
        'PL2': get_value_for_bits(value, 32, 46),
        'TW2': get_value_for_bits(value, 49, 55),
    }


def calc_time_window_vars(t):
    """Encode a time-window duration (s) as the (Y, Z) pair used by
    MSR_PKG_POWER_LIMIT."""
    time_unit = get_time_unit()
    for Y in range(2**5):
        for Z in range(2**2):
            if t <= (2**Y) * (1.0 + Z / 4.0) * time_unit:
                return (Y, Z)
    raise ValueError('Unable to find a good combination!')


def _encode_pkg_power_limit(pl1, tw1, pl2, tw2):
    """Encode MSR_PKG_POWER_LIMIT fields without allowing adjacent-bit spill."""
    fields = (
        ('PL1', pl1, PKG_POWER_LIMIT_POWER_MASK),
        ('TW1', tw1, PKG_POWER_LIMIT_TIME_WINDOW_MASK),
        ('PL2', pl2, PKG_POWER_LIMIT_POWER_MASK),
        ('TW2', tw2, PKG_POWER_LIMIT_TIME_WINDOW_MASK),
    )
    encoded = {}
    for name, value, mask in fields:
        if not isinstance(value, int) or not 0 <= value <= mask:
            raise ValueError(f'{name:s} value {value!r} does not fit its {mask.bit_length():d}-bit field.')
        encoded[name] = value
    return (
        encoded['PL1']
        | (1 << 15)
        | (1 << 16)
        | (encoded['TW1'] << 17)
        | (encoded['PL2'] << 32)
        | (1 << 47)
        | (encoded['TW2'] << 49)
    )


def _undervolt_offset_to_ticks(offset):
    """Convert an undervolt in mV to the signed 11-bit mailbox field."""
    try:
        offset = float(offset)
    except (TypeError, ValueError) as e:
        raise ValueError(f'Undervolt offset must be a number, got {offset!r}.') from e
    minimum_mv = UNDERVOLT_MIN_TICKS / UNDERVOLT_TICKS_PER_MV
    if not math.isfinite(offset) or not minimum_mv <= offset <= 0:
        raise ValueError(f'Undervolt offset must be between {minimum_mv:g} and 0 mV, got {offset!r}.')
    ticks = int(round(offset * UNDERVOLT_TICKS_PER_MV))
    if not UNDERVOLT_MIN_TICKS <= ticks <= UNDERVOLT_MAX_TICKS:
        raise ValueError(f'Undervolt offset {offset!r} mV does not fit the signed 11-bit mailbox field.')
    return ticks


def calc_undervolt_msr(plane, offset):
    """Return the value to be written in the MSR 150h for setting the given
    offset voltage (in mV) to the given voltage plane.
    """
    if plane not in VOLTAGE_PLANES:
        raise ValueError(f'Unknown voltage plane: {plane!r}.')
    ticks = _undervolt_offset_to_ticks(offset)
    encoded_offset = (ticks & 0x7FF) << 21
    return 0x8000001100000000 | (VOLTAGE_PLANES[plane] << 40) | encoded_offset


def calc_undervolt_mv(msr_value):
    """Return the offset voltage (in mV) from the given raw MSR 150h value."""
    offset = (msr_value & 0xFFE00000) >> 21
    # 11-bit two's complement: values >= 0x400 are negative
    offset = offset if offset < 0x400 else -(0x800 - offset)
    return int(round(offset / UNDERVOLT_TICKS_PER_MV))


def get_undervolt(plane=None, convert=False):
    """Read the current undervolt offset from one or all voltage planes."""
    if 'UNDERVOLT' in UNSUPPORTED_FEATURES:
        return 0
    planes = [plane] if plane in VOLTAGE_PLANES else VOLTAGE_PLANES
    out = {}
    for plane in planes:
        writemsr('MSR_OC_MAILBOX', 0x8000001000000000 | (VOLTAGE_PLANES[plane] << 40))
        read_value = readmsr('MSR_OC_MAILBOX', flatten=True) & 0xFFFFFFFF
        out[plane] = calc_undervolt_mv(read_value) if convert else read_value

    return out


def undervolt(config, source=None):
    """Apply the undervolt offsets from the config to all voltage planes."""
    source = source or power['source']
    section = f'UNDERVOLT.{source}'
    if (section not in config and 'UNDERVOLT' not in config) or 'UNDERVOLT' in UNSUPPORTED_FEATURES:
        return
    for plane in VOLTAGE_PLANES:
        write_offset_mv = config.getfloat(section, plane, fallback=config.getfloat('UNDERVOLT', plane, fallback=0.0))
        write_value = calc_undervolt_msr(plane, write_offset_mv)
        writemsr('MSR_OC_MAILBOX', write_value)
        if args.debug:
            write_value &= 0xFFFFFFFF
            read_value = get_undervolt(plane)[plane]
            read_offset_mv = calc_undervolt_mv(read_value)
            match = OK if write_value == read_value else ERR
            log(
                f'[D] Undervolt plane {plane:s} - write {write_offset_mv:.0f} mV ({write_value:#x}) - read {read_offset_mv:.0f} mV ({read_value:#x}) - match {match}'
            )


def _icc_max_to_field(current):
    """Convert an IccMax in A to the unsigned 10-bit quarter-ampere field."""
    try:
        current = float(current)
    except (TypeError, ValueError) as e:
        raise ValueError(f'IccMax must be a number, got {current!r}.') from e
    maximum_a = ICCMAX_MAX_FIELD / ICCMAX_STEPS_PER_A
    if not math.isfinite(current) or not 0 < current <= maximum_a:
        raise ValueError(f'IccMax must be between 0 (exclusive) and {maximum_a:g} A, got {current!r}.')
    # floor: quantisation must never enforce a ceiling above the configured one
    field = int(current * ICCMAX_STEPS_PER_A)
    if not 1 <= field <= ICCMAX_MAX_FIELD:
        raise ValueError(f'IccMax {current!r} A quantises outside the unsigned 10-bit field.')
    return field


def calc_icc_max_msr(plane, current):
    """Return the value to be written in the MSR 150h for setting the given
    IccMax (in A) to the given current plane.
    """
    if plane not in CURRENT_PLANES:
        raise ValueError(f'Unknown current plane: {plane!r}.')
    return 0x8000001700000000 | (CURRENT_PLANES[plane] << 40) | _icc_max_to_field(current)


def calc_icc_max_amp(msr_value):
    """Return the max current (in A) from the given raw MSR 150h value."""
    return (msr_value & 0x3FF) / 4.0


def get_configured_power_profiles(config):
    """Return the AC/BATTERY power profiles present in the config file."""
    return [profile for profile in POWER_PROFILES if profile in config]


def get_update_rate(config, power_source):
    """Return the update rate for power_source, or any configured profile."""
    update_rate = config.getfloat(power_source, 'Update_Rate_s', fallback=None)
    if update_rate is not None:
        return update_rate

    for fallback_power_source in get_configured_power_profiles(config):
        update_rate = config.getfloat(fallback_power_source, 'Update_Rate_s', fallback=None)
        if update_rate is not None:
            return update_rate

    fatal('At least one configured power profile must define "Update_Rate_s".')


def get_icc_max(plane=None, convert=False):
    """Read the IccMax setting from one or all current planes."""
    planes = [plane] if plane in CURRENT_PLANES else CURRENT_PLANES
    out = {}
    for plane in planes:
        writemsr('MSR_OC_MAILBOX', 0x8000001600000000 | (CURRENT_PLANES[plane] << 40))
        read_value = readmsr('MSR_OC_MAILBOX', flatten=True) & 0x3FF
        out[plane] = calc_icc_max_amp(read_value) if convert else read_value

    return out


def set_icc_max(config, source=None):
    """Apply the IccMax limits from the config to all current planes."""
    if 'ICCMAX' in UNSUPPORTED_FEATURES:
        return
    source = source or power['source']
    section = f'ICCMAX.{source}'
    for plane in CURRENT_PLANES:
        try:
            write_current_amp = config.getfloat(
                section, plane, fallback=config.getfloat('ICCMAX', plane, fallback=-1.0)
            )
            if write_current_amp <= 0 and any(
                config.getfloat(key, plane, fallback=-1.0) > 0 for key in ICCMAX_KEYS
            ):
                warning(f'IccMax {plane:s} is not configured for the {source:s} profile: leaving it untouched.')
            if write_current_amp > 0:
                write_value = calc_icc_max_msr(plane, write_current_amp)
                writemsr('MSR_OC_MAILBOX', write_value)
                if args.debug:
                    write_value &= 0x3FF
                    read_value = get_icc_max(plane)[plane]
                    read_current_A = calc_icc_max_amp(read_value)
                    match = OK if write_value == read_value else ERR
                    log(
                        f'[D] IccMax plane {plane:s} - write {calc_icc_max_amp(write_value):.2f} A ({write_value:#x}) - read {read_current_A:.2f} A ({read_value:#x}) - match {match}'
                    )
        except (configparser.NoSectionError, configparser.NoOptionError):
            pass


def _remove_config_option(config, section, option):
    """Drop option from the layer holding it and return that layer's name."""
    if config.remove_option(section, option):
        return section
    config.remove_option(config.default_section, option)
    return config.default_section


def load_config():
    """Parse the config file, validating and clamping out-of-range values."""
    config = configparser.ConfigParser()
    config.read(args.config)

    power_profiles = get_configured_power_profiles(config)
    if not power_profiles:
        fatal('At least one power profile ([AC] or [BATTERY]) is required.')

    # config values sanity check
    for power_source in power_profiles:
        for option in ('Update_Rate_s', 'PL1_Tdp_W', 'PL1_Duration_s', 'PL2_Tdp_W', 'PL2_Duration_S'):
            value = None
            # a malformed profile value may mask a second malformed value inherited from [DEFAULT]
            for _ in range(2):
                try:
                    value = config.getfloat(power_source, option, fallback=None)
                    if value is None or math.isfinite(value):
                        break
                    raise ValueError(value)
                except (ValueError, configparser.InterpolationError):
                    value = None
                    section = _remove_config_option(config, power_source, option)
                    if option == 'Update_Rate_s':
                        fatal(f'The mandatory "Update_Rate_s" parameter in [{section:s}] must be a finite number.')
                    warning(f'Invalid "{option:s}" value in [{section:s}]: ignoring it.', oneshot=False)
            if value is not None:
                config.set(power_source, option, str(max(0.001, value)))
            elif option == 'Update_Rate_s':
                fatal(f'The mandatory "Update_Rate_s" parameter is missing in the [{power_source:s}] profile.')

        trip_temp = None
        for _ in range(2):
            try:
                trip_temp = config.getfloat(power_source, 'Trip_Temp_C', fallback=None)
                if trip_temp is None or math.isfinite(trip_temp):
                    break
                raise ValueError(trip_temp)
            except (ValueError, configparser.InterpolationError):
                trip_temp = None
                section = _remove_config_option(config, power_source, 'Trip_Temp_C')
                warning(f'Invalid "Trip_Temp_C" value in [{section:s}]: ignoring it.', oneshot=False)
        if trip_temp is not None:
            valid_trip_temp = min(TRIP_TEMP_RANGE[1], max(TRIP_TEMP_RANGE[0], trip_temp))
            if trip_temp != valid_trip_temp:
                config.set(power_source, 'Trip_Temp_C', str(valid_trip_temp))
                log(
                    f'[!] Overriding invalid "Trip_Temp_C" value in "{power_source:s}": {trip_temp:.1f} -> {valid_trip_temp:.1f}'
                )

    # handle the case where only one of UNDERVOLT.AC, UNDERVOLT.BATTERY keys exists
    # by forcing the other key to all zeros (ie. no undervolt); synthesizing it
    # before the vetting below puts its [DEFAULT]-inherited planes through it too
    if any(key in config for key in UNDERVOLT_KEYS[1:]):
        for key in UNDERVOLT_KEYS[1:]:
            if key not in config:
                config.add_section(key)

    # the mailbox field is signed 11-bit: reject anything below -1000 mV instead of wrapping it positive
    for key in UNDERVOLT_KEYS:
        for plane in VOLTAGE_PLANES:
            if key in config:
                for _ in range(2):
                    try:
                        value = config.getfloat(key, plane, fallback=0.0)
                        if not math.isfinite(value):
                            raise ValueError(f'Undervolt offset must be finite, got {value!r}.')
                        if value > 0:
                            config.set(key, plane, '0')
                            log(
                                f'[!] Overriding invalid "{key:s}" value in "{plane:s}" voltage plane: {value:.0f} -> 0'
                            )
                        else:
                            _undervolt_offset_to_ticks(value)
                        break
                    except (ValueError, configparser.InterpolationError) as e:
                        section = key if config.remove_option(key, plane) else config.default_section
                        warning(f'Invalid value for {plane:s} in [{section:s}]: {e}', oneshot=False)
                        if section == config.default_section:
                            # the plane names are shared with ICCMAX: shadow the inherited value, never touch [DEFAULT]
                            config.set(key, plane, '0')
                            break

    for key in UNDERVOLT_KEYS[1:]:
        if key in config:
            for plane in VOLTAGE_PLANES:
                config.set(key, plane, str(config.getfloat(key, plane, fallback=0.0)))

    # Check for CORE/CACHE values mismatch
    for key in UNDERVOLT_KEYS:
        if key in config:
            if config.getfloat(key, 'CORE', fallback=0) != config.getfloat(key, 'CACHE', fallback=0):
                warning('On Skylake and newer CPUs CORE and CACHE values should match!')
                break

    iccmax_enabled = False
    # check for invalid values (ie. <= 0 or > 0x3FF) in the IccMax settings
    for key in ICCMAX_KEYS:
        if key not in config:
            continue
        for option in config[key]:
            if option in config.defaults():
                continue
            if option.upper() not in CURRENT_PLANES:
                warning(f'Unknown IccMax plane "{option:s}" in [{key:s}]: ignoring it.', oneshot=False)
        for plane in CURRENT_PLANES:
            if key in config:
                for _ in range(2):
                    try:
                        value = config.getfloat(key, plane)
                        _icc_max_to_field(value)
                        iccmax_enabled = True
                        break
                    except (ValueError, configparser.InterpolationError) as e:
                        section = key if config.remove_option(key, plane) else config.default_section
                        warning(f'Invalid value for {plane:s} in [{section:s}]: {e}', oneshot=False)
                        if section == config.default_section:
                            # the plane names are shared with UNDERVOLT: shadow the inherited value, never touch [DEFAULT]
                            config.set(key, plane, '0')
                            break
                    except configparser.NoOptionError:
                        break
    if iccmax_enabled:
        warning('Warning! Raising IccMax above design limits can damage your system!')

    return config


def calc_reg_values(platform_info, config):
    """Compute the MSR values to apply for each power source from the config."""
    regs = defaultdict(dict)
    for power_source in get_configured_power_profiles(config):
        if platform_info['feature_programmable_temperature_target'] != 1:
            warning("Setting temperature target is not supported by this CPU")
        else:
            critical_temp = get_critical_temp()
            # update the allowed temp range to keep at least 3 'C from the CPU critical temperature
            global TRIP_TEMP_RANGE
            TRIP_TEMP_RANGE[1] = min(TRIP_TEMP_RANGE[1], critical_temp - 3)

            Trip_Temp_C = config.getfloat(power_source, 'Trip_Temp_C', fallback=None)
            if Trip_Temp_C is not None:
                valid_trip_temp = min(TRIP_TEMP_RANGE[1], max(TRIP_TEMP_RANGE[0], Trip_Temp_C))
                if valid_trip_temp != Trip_Temp_C:
                    log(
                        f'[!] Overriding "Trip_Temp_C" in "{power_source:s}" to stay within '
                        f'[{TRIP_TEMP_RANGE[0]:d}, {TRIP_TEMP_RANGE[1]:d}] C: '
                        f'{Trip_Temp_C:.1f} -> {valid_trip_temp:.1f}'
                    )
                    Trip_Temp_C = valid_trip_temp
                trip_offset = int(round(critical_temp - Trip_Temp_C))
                if trip_offset > 63:
                    # the offset field is 6 bits wide: a larger value would
                    # spill into adjacent bits and corrupt the register
                    log(
                        f'[!] Overriding "Trip_Temp_C" in "{power_source:s}": offset {trip_offset:d} '
                        f'exceeds the 6-bit MSR field, clamping to {critical_temp - 63:d} C'
                    )
                    trip_offset = 63
                regs[power_source]['MSR_TEMPERATURE_TARGET'] = trip_offset << 24
            else:
                log(f'[I] {power_source:s} trip temperature is disabled in config.')

        PL1_Tdp_W = config.getfloat(power_source, 'PL1_Tdp_W', fallback=None)
        PL1_Duration_s = config.getfloat(power_source, 'PL1_Duration_s', fallback=None)
        PL2_Tdp_W = config.getfloat(power_source, 'PL2_Tdp_W', fallback=None)
        PL2_Duration_s = config.getfloat(power_source, 'PL2_Duration_s', fallback=None)

        if (PL1_Tdp_W, PL1_Duration_s, PL2_Tdp_W, PL2_Duration_s).count(None) < 4:
            power_unit = get_power_unit()
            cur_pkg_power_limits = get_cur_pkg_power_limits()
            if PL1_Tdp_W is None:
                PL1 = cur_pkg_power_limits['PL1']
                log(f'[I] {power_source:s} PL1_Tdp_W disabled in config.')
            else:
                PL1 = int(round(PL1_Tdp_W / power_unit))

            if PL1_Duration_s is None:
                TW1 = cur_pkg_power_limits['TW1']
                log(f'[I] {power_source:s} PL1_Duration_s disabled in config.')
            else:
                Y, Z = calc_time_window_vars(PL1_Duration_s)
                TW1 = Y | (Z << 5)

            if PL2_Tdp_W is None:
                PL2 = cur_pkg_power_limits['PL2']
                log(f'[I] {power_source:s} PL2_Tdp_W disabled in config.')
            else:
                PL2 = int(round(PL2_Tdp_W / power_unit))

            if PL2_Duration_s is None:
                TW2 = cur_pkg_power_limits['TW2']
                log(f'[I] {power_source:s} PL2_Duration_s disabled in config.')
            else:
                Y, Z = calc_time_window_vars(PL2_Duration_s)
                TW2 = Y | (Z << 5)

            try:
                regs[power_source]['MSR_PKG_POWER_LIMIT'] = _encode_pkg_power_limit(PL1, TW1, PL2, TW2)
            except ValueError as e:
                fatal(f'Invalid package power limits in [{power_source:s}]: {e}')
        else:
            log(f'[I] {power_source:s} package power limits are disabled in config.')

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
    """Set the IA32_HWP_REQUEST energy/performance preference field."""
    if performance_mode not in (True, False) or 'HWP' in UNSUPPORTED_FEATURES:
        return
    hwp_mode = HWP_PERFORMANCE_VALUE if performance_mode is True else HWP_DEFAULT_VALUE
    # a CPU that disappears mid-loop is fatal rather than falling back to a broadcast write
    for addr in _ensure_msr_module():
        cpu = int(os.path.basename(os.path.dirname(addr)))
        cur_val = readmsr('IA32_HWP_REQUEST', cpu=cpu)
        new_val = (cur_val & 0xFFFFFFFF00FFFFFF) | (hwp_mode << 24)
        writemsr('IA32_HWP_REQUEST', new_val, cpu=cpu)
        if args.debug:
            read_value = readmsr('IA32_HWP_REQUEST', from_bit=24, to_bit=31, cpu=cpu)
            match = OK if hwp_mode == read_value else ERR
            log(f'[D] HWP CPU {cpu:d} - write "{hwp_mode:#02x}" - read "{read_value:#02x}" - match {match}')


def set_disable_bdprochot():
    """Clear bit 0 of MSR_POWER_CTL to disable BDPROCHOT."""
    cur_val = readmsr('MSR_POWER_CTL', flatten=True)
    new_val = cur_val & 0xFFFFFFFFFFFFFFFE

    writemsr('MSR_POWER_CTL', new_val)
    if args.debug:
        read_value = readmsr('MSR_POWER_CTL', from_bit=0, to_bit=0)[0]
        match = OK if read_value == 0 else ERR
        log(f'[D] BDPROCHOT - write "{0:#02x}" - read "{read_value:#02x}" - match {match}')


def get_config_write_time():
    """Return the config file's mtime, or None if it doesn't exist."""
    try:
        return os.stat(args.config).st_mtime
    except FileNotFoundError:
        return None


def reload_config():
    """Re-read the config and re-apply undervolt, IccMax and HWP settings."""
    config = load_config()
    if not config_is_enabled(config):
        log('[I] Reloading changes.')
        return config, defaultdict(dict)
    regs = calc_reg_values(get_cpu_platform_info(), config)
    undervolt(config)
    set_icc_max(config)
    set_hwp(config.getboolean('AC', 'HWP_Mode', fallback=None))
    log('[I] Reloading changes.')
    return config, regs


def _read_mchbar_dword(method=None, register='48.l'):
    """Read one MCHBAR PCI config DWORD for device 0:0.0."""
    cmd = ['setpci', '-s', '0000:00:00.0', register]
    if method:
        cmd[1:1] = ['-A', method]
    try:
        # capture stderr: a probe method is allowed to fail (e.g. the ECAM
        # region is not mappable under STRICT_DEVMEM) and its complaint must
        # not leak to the journal; read_mchbar_base() warns if all methods fail
        raw_value = check_output(cmd, stderr=PIPE).strip()
        if re.fullmatch(rb'[0-9a-fA-F]{8}', raw_value) is None:
            return None
        return int(raw_value, 16)
    except CalledProcessError as e:
        if args.debug:
            err = (e.stderr or b'').decode(errors='replace').strip()
            log(f'[D] MCHBAR - setpci {method or "default"} probe failed: {err}')
        return None
    except OSError:
        return None


def _read_host_bridge_identity():
    """Return the D0:F0 PCI vendor/device IDs from sysfs, or None."""
    values = []
    for attribute in ('vendor', 'device'):
        try:
            with open(os.path.join(PCI_HOST_BRIDGE_SYSFS_PATH, attribute), encoding='ascii') as attribute_file:
                raw_value = attribute_file.read().strip()
            if re.fullmatch(r'0x[0-9a-fA-F]{4}', raw_value) is None:
                return None
            values.append(int(raw_value[2:], 16))
        except (OSError, UnicodeDecodeError):
            return None
    return tuple(values)


def read_mchbar_base():
    """Return a validated enabled 64-bit MCHBAR base, or None."""
    identity = _read_host_bridge_identity()
    if identity is None:
        warning('Could not identify the PCI host bridge; disabling only MMIO package power-limit writes.')
        return None

    vendor_id, device_id = identity
    if vendor_id != PCI_VENDOR_ID_INTEL:
        warning(
            f'PCI host bridge vendor {vendor_id:#06x} is not Intel; disabling only MMIO package power-limit writes.'
        )
        return None

    address_mask = MCHBAR_ADDRESS_MASKS_BY_PCI_DEVICE.get(device_id)
    if address_mask is None:
        warning(
            f'Unknown Intel PCI host bridge device {device_id:#06x}; '
            f'disabling only MMIO package power-limit writes.'
        )
        return None

    allowed_mask = address_mask | MCHBAR_ENABLE_BIT
    for method in ('ecam', None):
        low = _read_mchbar_dword(method, '48.l')
        high = _read_mchbar_dword(method, '4c.l')
        if low is None or high is None:
            continue
        mchbar = low | (high << 32)
        if not mchbar & MCHBAR_ENABLE_BIT or mchbar & ~allowed_mask:
            continue
        base = mchbar & address_mask
        if base != 0:
            return base

    warning('Could not read a valid enabled MCHBAR base via setpci; disabling only MMIO package power-limit writes.')
    return None


def power_thread(state, exit_event):
    """Crash-loud wrapper for _power_thread: an uncaught exception (or a
    sys.exit() from a helper) would only kill this thread, leaving a zombie
    daemon that looks healthy to systemd while no longer touching the hardware.
    """
    try:
        _power_thread(state, exit_event)
    except Exception:
        warning(f'power thread crashed:\n{traceback.format_exc()}', oneshot=False)
        if args.log:
            args.log.flush()
        os._exit(1)


def _power_thread(state, exit_event):
    """Daemon main loop: periodically (re-)apply throttling MSRs."""
    config, regs = state['config'], state['regs']
    mchbar_base = read_mchbar_base()
    mchbar_mmio = None
    if mchbar_base is not None:
        try:
            mchbar_mmio = MMIO(
                mchbar_base + MCHBAR_PACKAGE_POWER_LIMIT_OFFSET,
                MCHBAR_PACKAGE_POWER_LIMIT_SIZE,
            )
        except MMIOError:
            warning('Unable to open /dev/mem. MMIO package power-limit writes are disabled.')
            warning(
                'Check CONFIG_DEVMEM=y and that kernel lockdown is disabled '
                '(CONFIG_IO_STRICT_DEVMEM can also block this region).'
            )

    next_hwp_write = 0
    applied_source = power['source']
    last_config_write_time = (
        get_config_write_time() if config.getboolean('GENERAL', 'Autoreload', fallback=False) else None
    )
    while not exit_event.is_set():
        # Reload config on changes (unless it's deleted)
        if config.getboolean('GENERAL', 'Autoreload', fallback=False):
            config_write_time = get_config_write_time()
            if config_write_time and last_config_write_time != config_write_time:
                last_config_write_time = config_write_time
                with config_lock:
                    state['config'], state['regs'] = reload_config()
                config, regs = state['config'], state['regs']

        # switch back to sysfs polling
        if power['method'] == 'polling':
            power['source'] = 'BATTERY' if is_on_battery(config) else 'AC'

        # snapshot the power source once per iteration: the D-Bus callback can
        # flip it concurrently and every write below must agree on one profile
        power_source = power['source']

        # Enabled=False is a hard write barrier, including the iteration that
        # observes an enabled -> disabled autoreload transition.
        if not config_is_enabled(config):
            applied_source = power_source
            exit_event.wait(get_update_rate(config, power_source))
            continue

        # log thermal status
        if args.debug:
            thermal_status = get_reset_thermal_status()
            for index, core_thermal_status in enumerate(thermal_status):
                for key, value in core_thermal_status.items():
                    log(f'[D] core {index} thermal status: {key.replace("_", " ")} = {value}')

        # re-apply the one-shot per-profile settings when the power source flips
        if power_source != applied_source:
            log(f'[I] Power source changed: {applied_source:s} -> {power_source:s}')
            undervolt(config, source=power_source)
            set_icc_max(config, source=power_source)
            if config.getboolean('AC', 'HWP_Mode', fallback=False):
                if power_source == 'AC':
                    next_hwp_write = 0
                else:
                    # restore the default energy/performance preference on battery
                    set_hwp(False)
            applied_source = power_source

        # set temperature trip point
        if 'MSR_TEMPERATURE_TARGET' in regs[power_source]:
            write_value = regs[power_source]['MSR_TEMPERATURE_TARGET']
            writemsr('MSR_TEMPERATURE_TARGET', write_value)
            if args.debug:
                read_value = readmsr('MSR_TEMPERATURE_TARGET', 24, 29, flatten=True)
                match = OK if write_value >> 24 == read_value else ERR
                log(f'[D] TEMPERATURE_TARGET - write {write_value >> 24:#x} - read {read_value:#x} - match {match}')

        # set cTDP
        if 'MSR_CONFIG_TDP_CONTROL' in regs[power_source]:
            write_value = regs[power_source]['MSR_CONFIG_TDP_CONTROL']
            writemsr('MSR_CONFIG_TDP_CONTROL', write_value)
            if args.debug:
                read_value = readmsr('MSR_CONFIG_TDP_CONTROL', 0, 1, flatten=True)
                match = OK if write_value == read_value else ERR
                log(f'[D] CONFIG_TDP_CONTROL - write {write_value:#x} - read {read_value:#x} - match {match}')

        # set PL1/2 on MSR
        if 'MSR_PKG_POWER_LIMIT' in regs[power_source]:
            write_value = regs[power_source]['MSR_PKG_POWER_LIMIT']
            writemsr('MSR_PKG_POWER_LIMIT', write_value)
            if args.debug:
                read_value = readmsr('MSR_PKG_POWER_LIMIT', 0, 55, flatten=True)
                match = OK if write_value == read_value else ERR
                log(f'[D] MSR PACKAGE_POWER_LIMIT - write {write_value:#x} - read {read_value:#x} - match {match}')
            if mchbar_mmio is not None:
                # set MCHBAR register to the same PL1/2 values
                try:
                    mchbar_mmio.write64(0, write_value)
                    if args.debug:
                        read_value = mchbar_mmio.read64(0)
                        match = OK if write_value == read_value else ERR
                        log(
                            f'[D] MCHBAR PACKAGE_POWER_LIMIT - write {write_value:#x} - read {read_value:#x} - match {match}'
                        )
                except OSError as e:
                    warning(f'Unable to write MCHBAR package power limits ({e}); disabling only MMIO writes.')
                    mchbar_mmio = None

        # Disable BDPROCHOT
        disable_bdprochot = config.getboolean(power_source, 'Disable_BDPROCHOT', fallback=None)
        if disable_bdprochot:
            set_disable_bdprochot()

        wait_t = get_update_rate(config, power_source)
        enable_hwp_mode = config.getboolean('AC', 'HWP_Mode', fallback=None)
        # set HWP less frequently. Just to be safe since (e.g.) TLP might reset this value
        if enable_hwp_mode and next_hwp_write <= time() and power_source == 'AC':
            set_hwp(enable_hwp_mode)
            next_hwp_write = time() + HWP_INTERVAL

        exit_event.wait(wait_t)


def check_kernel():
    """Verify we run as root and that the kernel exposes MSR/devmem."""
    if os.geteuid() != 0:
        fatal('No root no party. Try again with sudo.')

    try:
        with open('/sys/kernel/security/lockdown') as f:
            if '[none]' not in f.read():
                warning('Kernel lockdown is active: MSR and /dev/mem writes will be blocked.')
    except OSError:
        pass

    kernel_config = None
    try:
        with open(os.path.join('/boot', f'config-{uname()[2]:s}')) as f:
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
    """Identify the CPU from /proc/cpuinfo and refuse to run on unsupported models."""
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

        log(f'[I] Detected CPU architecture: Intel {supported_cpus[cpuid]:s}')
        return cpuid
    except SystemExit:
        raise
    except (OSError, KeyError, ValueError) as e:
        fatal(f'Unable to identify CPU model: {e}')


def test_msr_rw_capabilities():
    """Probe undervolt, IccMax and HWP support; mark unavailable features as such."""
    global TESTMSR
    TESTMSR = True
    try:
        try:
            log('[I] Testing if undervolt is supported...')
            get_undervolt()
        except (IOError, OSError):
            warning('Undervolt seems not to be supported by your system, disabling.')
            UNSUPPORTED_FEATURES.append('UNDERVOLT')

        try:
            log('[I] Testing if IccMax is supported...')
            get_icc_max()
        except (IOError, OSError):
            warning('IccMax seems not to be supported by your system, disabling.')
            UNSUPPORTED_FEATURES.append('ICCMAX')

        try:
            log('[I] Testing if HWP is supported...')
            cur_val = readmsr('IA32_HWP_REQUEST', cpu=0)
            writemsr('IA32_HWP_REQUEST', cur_val, cpu=0)
        except (IOError, OSError):
            warning('HWP seems not to be supported by your system, disabling.')
            UNSUPPORTED_FEATURES.append('HWP')
    finally:
        TESTMSR = False


def monitor(exit_event, wait):
    """Live-display throttling causes and per-domain power until exit_event is set."""
    wait = max(0.1, wait)
    rapl_power_unit = 0.5 ** readmsr('MSR_RAPL_POWER_UNIT', from_bit=8, to_bit=12, cpu=0)
    # the RAPL energy counters are 32 bits wide and wrap every few minutes
    rapl_counter_range = 2**32 * rapl_power_unit
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

    if 'UNDERVOLT' in UNSUPPORTED_FEATURES:
        log('[D] Undervolt offsets: unsupported')
    else:
        undervolt_values = get_undervolt(convert=True)
        undervolt_output = ' | '.join(f'{plane:s}: {undervolt_values[plane]:.2f} mV' for plane in VOLTAGE_PLANES)
        log(f'[D] Undervolt offsets: {undervolt_output:s}')

    if 'ICCMAX' in UNSUPPORTED_FEATURES:
        log('[D] IccMax: unsupported')
    else:
        iccmax_values = get_icc_max(convert=True)
        iccmax_output = ' | '.join(f'{plane:s}: {iccmax_values[plane]:.2f} A' for plane in CURRENT_PLANES)
        log(f'[D] IccMax: {iccmax_output:s}')

    log('[D] Realtime monitoring of throttling causes:\n')
    while not exit_event.is_set():
        value = readmsr('IA32_THERM_STATUS', from_bit=0, to_bit=15, cpu=0)
        offsets = {'Thermal': 0, 'Power': 10, 'Current': 12, 'Cross-domain (e.g. GPU)': 14}
        output = (f'{cause:s}: {LIM if bool((value >> offsets[cause]) & 1) else OK:s}' for cause in offsets)

        vcore = readmsr('IA32_PERF_STATUS', from_bit=32, to_bit=47, cpu=0) / (2.0**13) * 1000
        stats2 = {'VCore': f'{vcore:.0f} mV'}
        total = 0.0
        for power_plane in ('Package', 'Graphics', 'DRAM'):
            energy_j = readmsr(power_plane_msr[power_plane], cpu=0) * rapl_power_unit
            now = time()
            prev_j, prev_t = prev_energy[power_plane]
            energy_w = ((energy_j - prev_j) % rapl_counter_range) / (now - prev_t)
            prev_energy[power_plane] = (energy_j, now)
            stats2[power_plane] = f'{energy_w:.1f} W'
            total += energy_w

        stats2['Total'] = f'{total:.1f} W'

        output2 = (f'{label}: {stats2[label]}' for label in stats2)
        terminator = '\n' if args.log else '\r'
        log(
            f"[{power['source']}] {' - '.join(output)}  ||  {' - '.join(output2)}{' ' * 10}",
            end=terminator,
        )
        exit_event.wait(wait)


def build_arg_parser():
    """Create the command-line parser, disabling argparse's own color output when available."""
    try:
        parser = argparse.ArgumentParser(color=False)
    except TypeError:
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
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    return parser


def main():
    """Daemon entrypoint: parse args, validate platform, start power thread."""
    global args

    args = build_arg_parser().parse_args()

    if args.log:
        try:
            args.log = open(args.log, 'w')
        except OSError as e:
            args.log = None
            fatal(f'Unable to write to the log file: {e}')

    log('[I] Loading config file.')
    config = load_config()
    if not config_is_enabled(config):
        log('[I] Throttled is disabled in config file... Quitting. :(')
        return

    if not args.force:
        check_kernel()
        check_cpu()

    set_msr_allow_writes()

    test_msr_rw_capabilities()

    power['source'] = 'BATTERY' if is_on_battery(config) else 'AC'

    platform_info = get_cpu_platform_info()
    if args.debug:
        for key, value in platform_info.items():
            log(f'[D] cpu platform info: {key.replace("_", " ")} = {value}')
    regs = calc_reg_values(platform_info, config)

    undervolt(config)
    set_icc_max(config)
    set_hwp(config.getboolean('AC', 'HWP_Mode', fallback=None))

    state = {'config': config, 'regs': regs}

    exit_event = Event()
    thread = Thread(target=power_thread, args=(state, exit_event))
    thread.daemon = True
    thread.start()

    log('[I] Starting main loop.')

    monitor_thread = None
    if args.monitor is not None:
        monitor_thread = Thread(target=monitor, args=(exit_event, args.monitor))
        monitor_thread.daemon = True
        monitor_thread.start()

    try:
        asyncio.run(run_dbus_loop(state))
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        exit_event.set()
        thread.join(timeout=1)
        if monitor_thread is not None:
            monitor_thread.join(timeout=0.1)


if __name__ == '__main__':
    main()
