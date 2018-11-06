#!/usr/bin/env python3
from __future__ import print_function

import argparse
import configparser
import dbus
import glob
import os
import re
import struct
import subprocess
import sys

from collections import defaultdict
from dbus.mainloop.glib import DBusGMainLoop
from errno import EACCES, EPERM
from gi.repository import GLib
from mmio import MMIO, MMIOError
from multiprocessing import cpu_count
from platform import uname
from threading import Event, Thread

DEFAULT_SYSFS_POWER_PATH = '/sys/class/power_supply/AC*/online'

VOLTAGE_PLANES = {'CORE': 0, 'GPU': 1, 'CACHE': 2, 'UNCORE': 3, 'ANALOGIO': 4}

TRIP_TEMP_RANGE = [40, 97]

power = {'source': None, 'method': 'polling'}

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


class bcolors:
    YELLOW = '\033[93m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


OK = bcolors.GREEN + bcolors.BOLD + 'OK' + bcolors.RESET
ERR = bcolors.RED + bcolors.BOLD + 'ERR' + bcolors.RESET
LIM = bcolors.YELLOW + bcolors.BOLD + 'LIM' + bcolors.RESET


def writemsr(msr, val):
    msr_list = ['/dev/cpu/{:d}/msr'.format(x) for x in range(cpu_count())]
    if not os.path.exists(msr_list[0]):
        try:
            subprocess.check_call(('modprobe', 'msr'))
        except subprocess.CalledProcessError:
            print('[E] Unable to load the msr module.')
            sys.exit(1)
    try:
        for addr in msr_list:
            f = os.open(addr, os.O_WRONLY)
            os.lseek(f, msr, os.SEEK_SET)
            os.write(f, struct.pack('Q', val))
            os.close(f)
    except (IOError, OSError) as e:
        if e.errno == EPERM or e.errno == EACCES:
            print(
                '[E] Unable to write to MSR. Try to disable Secure Boot '
                'and check if your kernel does not restrict access to MSR.'
            )
            sys.exit(1)
        else:
            raise e


# returns the value between from_bit and to_bit as unsigned long
def readmsr(msr, from_bit=0, to_bit=63, cpu=None, flatten=False):
    assert cpu is None or cpu in range(cpu_count())
    if from_bit > to_bit:
        print('[E] Wrong readmsr bit params')
        sys.exit(1)
    msr_list = ['/dev/cpu/{:d}/msr'.format(x) for x in range(cpu_count())]
    if not os.path.exists(msr_list[0]):
        try:
            subprocess.check_call(('modprobe', 'msr'))
        except subprocess.CalledProcessError:
            print('[E] Unable to load the msr module.')
            sys.exit(1)
    try:
        output = []
        for addr in msr_list:
            f = os.open(addr, os.O_RDONLY)
            os.lseek(f, msr, os.SEEK_SET)
            val = struct.unpack('Q', os.read(f, 8))[0]
            os.close(f)
            output.append(get_value_for_bits(val, from_bit, to_bit))
        if flatten:
            return output[0] if len(set(output)) == 1 else output
        return output[cpu] if cpu is not None else output
    except (IOError, OSError) as e:
        if e.errno == EPERM or e.errno == EACCES:
            print('[E] Unable to read from MSR. Try to disable Secure Boot.')
            sys.exit(1)
        else:
            raise e


def cpu_usage_pct(exit_event, interval=1.0):
    last_idle = last_total = 0

    for i in range(2):
        with open('/proc/stat') as f:
            fields = [float(column) for column in f.readline().strip().split()[1:]]
        idle, total = fields[3], sum(fields)
        idle_delta, total_delta = idle - last_idle, total - last_total
        last_idle, last_total = idle, total
        if i == 0:
            exit_event.wait(interval)

    return 100.0 * (1.0 - idle_delta / total_delta)


def get_value_for_bits(val, from_bit=0, to_bit=63):
    mask = sum(2 ** x for x in range(from_bit, to_bit + 1))
    return (val & mask) >> from_bit


def is_on_battery(config):
    try:
        for path in glob.glob(config.get('GENERAL', 'Sysfs_Power_Path', fallback=DEFAULT_SYSFS_POWER_PATH)):
            with open(path) as f:
                return not bool(int(f.read()))
    except:
        pass
    print('[E] No valid Sysfs_Power_Path found!')
    sys.exit(1)


def get_cpu_platform_info():
    features_msr_value = readmsr(0xCE, cpu=0)
    cpu_platform_info = {}
    for key, value in platform_info_bits.items():
        cpu_platform_info[key] = int(get_value_for_bits(features_msr_value, value[0], value[1]))
    return cpu_platform_info


def get_reset_thermal_status():
    # read thermal status
    thermal_status_msr_value = readmsr(0x19C)
    thermal_status = []
    for core in range(cpu_count()):
        thermal_status_core = {}
        for key, value in thermal_status_bits.items():
            thermal_status_core[key] = int(get_value_for_bits(thermal_status_msr_value[core], value[0], value[1]))
        thermal_status.append(thermal_status_core)
    # reset log bits
    writemsr(0x19C, 0)
    return thermal_status


def get_time_unit():
    # 0.000977 is the time unit of my CPU
    # TODO formula might be different for other CPUs
    return 1.0 / 2 ** readmsr(0x606, 16, 19, cpu=0)


def get_power_unit():
    # 0.125 is the power unit of my CPU
    # TODO formula might be different for other CPUs
    return 1.0 / 2 ** readmsr(0x606, 0, 3, cpu=0)


def get_critical_temp():
    # the critical temperature for my CPU is 100 'C
    return readmsr(0x1A2, 16, 23, cpu=0)


def get_cur_pkg_power_limits():
    value = readmsr(0x610, 0, 55, flatten=True)
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
    """Return the offset voltage (in mV) from the given raw MSR 150h value.
    """
    offset = (msr_value & 0xFFE00000) >> 21
    offset = offset if offset <= 0x400 else -(0x800 - offset)
    return int(round(offset / 1.024))


def undervolt(config):
    for plane in VOLTAGE_PLANES:
        write_offset_mv = config.getfloat('UNDERVOLT', plane, fallback=0.0)
        write_value = calc_undervolt_msr(plane, write_offset_mv)
        writemsr(0x150, write_value)
        if args.debug:
            write_value &= 0xFFFFFFFF
            writemsr(0x150, 0x8000001000000000 | (VOLTAGE_PLANES[plane] << 40))
            read_value = readmsr(0x150, flatten=True)
            read_offset_mv = calc_undervolt_mv(read_value)
            match = OK if write_value == read_value else ERR
            print(
                '[D] Undervolt plane {:s} - write {:.0f} mV ({:#x}) - read {:.0f} mV ({:#x}) - match {}'.format(
                    plane, write_offset_mv, write_value, read_offset_mv, read_value, match
                )
            )


def load_config():
    config = configparser.ConfigParser()
    config.read(args.config)

    # config values sanity check
    for power_source in ('AC', 'BATTERY'):
        for option in ('Update_Rate_s', 'PL1_Tdp_W', 'PL1_Duration_s', 'PL2_Tdp_W', 'PL2_Duration_S'):
            value = config.getfloat(power_source, option, fallback=None)
            if value is not None:
                value = config.set(power_source, option, str(max(0.1, value)))
            elif option == 'Update_Rate_s':
                print('[E] The mandatory "Update_Rate_s" parameter is missing.')
                sys.exit(1)

        trip_temp = config.getfloat(power_source, 'Trip_Temp_C', fallback=None)
        if trip_temp is not None:
            valid_trip_temp = min(TRIP_TEMP_RANGE[1], max(TRIP_TEMP_RANGE[0], trip_temp))
            if trip_temp != valid_trip_temp:
                config.set(power_source, 'Trip_Temp_C', str(valid_trip_temp))
                print(
                    '[!] Overriding invalid "Trip_Temp_C" value in "{:s}": {:.1f} -> {:.1f}'.format(
                        power_source, trip_temp, valid_trip_temp
                    )
                )

    for plane in VOLTAGE_PLANES:
        value = config.getfloat('UNDERVOLT', plane)
        valid_value = min(0, value)
        if value != valid_value:
            config.set('UNDERVOLT', plane, str(valid_value))
            print(
                '[!] Overriding invalid "UNDERVOLT" value in "{:s}" voltage plane: {:.0f} -> {:.0f}'.format(
                    plane, value, valid_value
                )
            )

    return config


def calc_reg_values(platform_info, config):
    regs = defaultdict(dict)
    for power_source in ('AC', 'BATTERY'):
        if platform_info['feature_programmable_temperature_target'] != 1:
            print("[W] Setting temperature target is not supported by this CPU")
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
                print('[I] {:s} trip temperature is disabled in config.'.format(power_source))

        power_unit = get_power_unit()

        PL1_Tdp_W = config.getfloat(power_source, 'PL1_Tdp_W', fallback=None)
        PL1_Duration_s = config.getfloat(power_source, 'PL1_Duration_s', fallback=None)
        PL2_Tdp_W = config.getfloat(power_source, 'PL2_Tdp_W', fallback=None)
        PL2_Duration_s = config.getfloat(power_source, 'PL2_Duration_s', fallback=None)

        if (PL1_Tdp_W, PL1_Duration_s, PL2_Tdp_W, PL2_Duration_s).count(None) < 4:
            cur_pkg_power_limits = get_cur_pkg_power_limits()
            if PL1_Tdp_W is None:
                PL1 = cur_pkg_power_limits['PL1']
                print('[I] {:s} PL1_Tdp_W disabled in config.'.format(power_source))
            else:
                PL1 = int(round(PL1_Tdp_W / power_unit))

            if PL1_Duration_s is None:
                TW1 = cur_pkg_power_limits['TW1']
                print('[I] {:s} PL1_Duration_s disabled in config.'.format(power_source))
            else:
                Y, Z = calc_time_window_vars(PL1_Duration_s)
                TW1 = Y | (Z << 5)

            if PL2_Tdp_W is None:
                PL2 = cur_pkg_power_limits['PL2']
                print('[I] {:s} PL2_Tdp_W disabled in config.'.format(power_source))
            else:
                PL2 = int(round(PL2_Tdp_W / power_unit))

            if PL2_Duration_s is None:
                TW2 = cur_pkg_power_limits['TW2']
                print('[I] {:s} PL2_Duration_s disabled in config.'.format(power_source))
            else:
                Y, Z = calc_time_window_vars(PL2_Duration_s)
                TW2 = Y | (Z << 5)

            regs[power_source]['MSR_PKG_POWER_LIMIT'] = (
                PL1 | (1 << 15) | (TW1 << 17) | (PL2 << 32) | (1 << 47) | (TW2 << 49)
            )
        else:
            print('[I] {:s} package power limits are disabled in config.'.format(power_source))

        # cTDP
        c_tdp_target_value = config.getint(power_source, 'cTDP', fallback=None)
        if c_tdp_target_value is not None:
            if platform_info['feature_programmable_tdp_limit'] != 1:
                print("[W] cTDP setting not supported by this CPU")
            elif platform_info['number_of_additional_tdp_profiles'] < c_tdp_target_value:
                print("[W] the configured cTDP profile is not supported by this CPU")
            else:
                valid_c_tdp_target_value = max(0, c_tdp_target_value)
                regs[power_source]['MSR_CONFIG_TDP_CONTROL'] = valid_c_tdp_target_value
    return regs


def set_hwp(pref):
    # set HWP energy performance hints
    assert pref in ('performance', 'balance_performance', 'default', 'balance_power', 'power')
    CPUs = [
        '/sys/devices/system/cpu/cpu{:d}/cpufreq/energy_performance_preference'.format(x) for x in range(cpu_count())
    ]
    for i, c in enumerate(CPUs):
        with open(c, 'wb') as f:
            f.write(pref.encode())
        if args.debug:
            with open(c) as f:
                read_value = f.read().strip()
                match = OK if pref == read_value else ERR
                print('[D] HWP for cpu{:d} - write "{:s}" - read "{:s}" - match {}'.format(i, pref, read_value, match))


def power_thread(config, regs, exit_event):
    try:
        mchbar_mmio = MMIO(0xFED159A0, 8)
    except MMIOError:
        print('[E] Unable to open /dev/mem. Try to disable Secure Boot.')
        sys.exit(1)

    while not exit_event.is_set():
        # print thermal status
        if args.debug:
            thermal_status = get_reset_thermal_status()
            for index, core_thermal_status in enumerate(thermal_status):
                for key, value in core_thermal_status.items():
                    print('[D] core {} thermal status: {} = {}'.format(index, key.replace("_", " "), value))

        # switch back to sysfs polling
        if power['method'] == 'polling':
            power['source'] = 'BATTERY' if is_on_battery(config) else 'AC'

        # set temperature trip point
        if 'MSR_TEMPERATURE_TARGET' in regs[power['source']]:
            write_value = regs[power['source']]['MSR_TEMPERATURE_TARGET']
            writemsr(0x1A2, write_value)
            if args.debug:
                read_value = readmsr(0x1A2, 24, 29, flatten=True)
                match = OK if write_value >> 24 == read_value else ERR
                print(
                    '[D] TEMPERATURE_TARGET - write {:#x} - read {:#x} - match {}'.format(
                        write_value >> 24, read_value, match
                    )
                )

        # set cTDP
        if 'MSR_CONFIG_TDP_CONTROL' in regs[power['source']]:
            write_value = regs[power['source']]['MSR_CONFIG_TDP_CONTROL']
            writemsr(0x64B, write_value)
            if args.debug:
                read_value = readmsr(0x64B, 0, 1, flatten=True)
                match = OK if write_value == read_value else ERR
                print(
                    '[D] CONFIG_TDP_CONTROL - write {:#x} - read {:#x} - match {}'.format(
                        write_value, read_value, match
                    )
                )

        # set PL1/2 on MSR
        write_value = regs[power['source']]['MSR_PKG_POWER_LIMIT']
        writemsr(0x610, write_value)
        if args.debug:
            read_value = readmsr(0x610, 0, 55, flatten=True)
            match = OK if write_value == read_value else ERR
            print(
                '[D] MSR PACKAGE_POWER_LIMIT - write {:#x} - read {:#x} - match {}'.format(
                    write_value, read_value, match
                )
            )
        # set MCHBAR register to the same PL1/2 values
        mchbar_mmio.write32(0, write_value & 0xFFFFFFFF)
        mchbar_mmio.write32(4, write_value >> 32)
        if args.debug:
            read_value = mchbar_mmio.read32(0) | (mchbar_mmio.read32(4) << 32)
            match = OK if write_value == read_value else ERR
            print(
                '[D] MCHBAR PACKAGE_POWER_LIMIT - write {:#x} - read {:#x} - match {}'.format(
                    write_value, read_value, match
                )
            )

        wait_t = config.getfloat(power['source'], 'Update_Rate_s')
        enable_hwp_mode = config.getboolean('AC', 'HWP_Mode', fallback=False)
        if power['source'] == 'AC' and enable_hwp_mode:
            cpu_usage = cpu_usage_pct(exit_event, interval=wait_t)
            # set full performance mode only when load is greater than this threshold (~ at least 1 core full speed)
            performance_mode = cpu_usage > 100.0 / (cpu_count() * 1.25)
            # check again if we are on AC, since in the meantime we might have switched to BATTERY
            if not is_on_battery(config):
                set_hwp('performance' if performance_mode else 'balance_performance')
        else:
            exit_event.wait(wait_t)


def check_kernel():
    if os.geteuid() != 0:
        print('[E] No root no party. Try again with sudo.')
        sys.exit(1)

    kernel_config = None
    try:
        with open(os.path.join('/boot', 'config-{:s}'.format(uname()[2]))) as f:
            kernel_config = f.read()
    except IOError:
        try:
            with open(os.path.join('/proc', 'config.gz')) as f:
                kernel_config = f.read()
        except IOError:
            pass
    if kernel_config is None:
        print('[W] Unable to obtain and validate kernel config.')
    elif not re.search('CONFIG_DEVMEM=y', kernel_config):
        print('[E] Bad kernel config: you need CONFIG_DEVMEM=y.')
        sys.exit(1)
    elif not re.search('CONFIG_X86_MSR=(y|m)', kernel_config):
        print('[E] Bad kernel config: you need CONFIG_X86_MSR builtin or as module.')
        sys.exit(1)


def monitor(exit_event, wait):
    wait = max(0.1, wait)
    print('Realtime monitoring of throttling causes:')
    while not exit_event.is_set():
        value = readmsr(0x19C, from_bit=0, to_bit=15, cpu=0)
        offsets = {'Thermal': 0, 'Power': 10, 'Current': 12, 'Cross-comain (e.g. GPU)': 14}
        output = ('{:s}: {:s}'.format(cause, LIM if bool((value >> offsets[cause]) & 1) else OK) for cause in offsets)
        print(' - '.join(output) + ' ' * 10, end='\r')
        exit_event.wait(wait)


def main():
    global args

    check_kernel()

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
    parser.add_argument('--config', default='/etc/lenovo_fix.conf', help='override default config file path')
    args = parser.parse_args()

    config = load_config()
    power['source'] = 'BATTERY' if is_on_battery(config) else 'AC'

    platform_info = get_cpu_platform_info()
    if args.debug:
        for key, value in platform_info.items():
            print('[D] cpu platform info: {} = {}'.format(key.replace("_", " "), value))
    regs = calc_reg_values(platform_info, config)

    if not config.getboolean('GENERAL', 'Enabled'):
        return

    exit_event = Event()
    thread = Thread(target=power_thread, args=(config, regs, exit_event))
    thread.daemon = True
    thread.start()

    undervolt(config)

    # handle dbus events for applying undervolt on resume from sleep/hybernate
    def handle_sleep_callback(sleeping):
        if not sleeping:
            undervolt(config)

    def handle_ac_callback(*args):
        try:
            power['source'] = 'BATTERY' if args[1]['Online'] == 0 else 'AC'
            power['method'] = 'dbus'
        except:
            power['method'] = 'polling'

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    # add dbus receiver only if undervolt is enabled in config
    if any(config.getfloat('UNDERVOLT', plane) != 0 for plane in VOLTAGE_PLANES):
        bus.add_signal_receiver(
            handle_sleep_callback, 'PrepareForSleep', 'org.freedesktop.login1.Manager', 'org.freedesktop.login1'
        )
    bus.add_signal_receiver(
        handle_ac_callback,
        signal_name="PropertiesChanged",
        dbus_interface="org.freedesktop.DBus.Properties",
        path="/org/freedesktop/UPower/devices/line_power_AC",
    )

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
