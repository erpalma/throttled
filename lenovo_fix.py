#!/usr/bin/env python3

import configparser
import dbus
import glob
import os
import psutil
import struct
import subprocess

from collections import defaultdict
from dbus.mainloop.glib import DBusGMainLoop
from mmio import MMIO
from multiprocessing import cpu_count
from threading import Event, Thread

try:
    from gi.repository import GObject
except ImportError:
    import gobject as GObject

SYSFS_POWER_PATH = '/sys/class/power_supply/AC/online'
CONFIG_PATH = '/etc/lenovo_fix.conf'

VOLTAGE_PLANES = {
    'CORE': 0,
    'GPU': 1,
    'CACHE': 2,
    'UNCORE': 3,
    'ANALOGIO': 4,
}


def writemsr(msr, val):
    n = glob.glob('/dev/cpu/[0-9]*/msr')
    for c in n:
        f = os.open(c, os.O_WRONLY)
        os.lseek(f, msr, os.SEEK_SET)
        os.write(f, struct.pack('Q', val))
        os.close(f)
    if not n:
        try:
            subprocess.check_call(('modprobe', 'msr'))
        except subprocess.CalledProcessError:
            raise OSError("Unable to load msr module.")


def is_on_battery():
    with open(SYSFS_POWER_PATH) as f:
        return not bool(int(f.read()))


def calc_time_window_vars(t):
    for Y in range(2**5):
        for Z in range(2**2):
            if t <= (2**Y) * (1. + Z / 4.) * 0.000977:
                return (Y, Z)
    raise Exception('Unable to find a good combination!')


def undervolt(config):
    for plane in VOLTAGE_PLANES:
        writemsr(0x150, calc_undervolt_msr(plane, config.getfloat('UNDERVOLT', plane)))


def calc_undervolt_msr(plane, offset):
    assert offset <= 0
    assert plane in VOLTAGE_PLANES
    offset = int(round(offset * 1.024))
    offset = 0xFFE00000 & ((offset & 0xFFF) << 21)
    return 0x8000001100000000 | (VOLTAGE_PLANES[plane] << 40) | offset


def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)

    for power_source in ('AC', 'BATTERY'):
        assert 0 < config.getfloat(power_source, 'Update_Rate_s')
        assert 0 < config.getfloat(power_source, 'PL1_Tdp_W')
        assert 0 < config.getfloat(power_source, 'PL1_Duration_s')
        assert 0 < config.getfloat(power_source, 'PL2_Tdp_W')
        assert 0 < config.getfloat(power_source, 'PL2_Duration_S')
        assert 40 < config.getfloat(power_source, 'Trip_Temp_C') < 98

    for plane in VOLTAGE_PLANES:
        assert config.getfloat('UNDERVOLT', plane) <= 0

    return config


def calc_reg_values(config):
    regs = defaultdict(dict)
    for power_source in ('AC', 'BATTERY'):
        # the critical temperature for this CPU is 100 C
        trip_offset = int(round(100 - config.getfloat(power_source, 'Trip_Temp_C')))
        regs[power_source]['MSR_TEMPERATURE_TARGET'] = trip_offset << 24

        # 0.125 is the power unit of this CPU
        PL1 = int(round(config.getfloat(power_source, 'PL1_Tdp_W') / 0.125))
        Y, Z = calc_time_window_vars(config.getfloat(power_source, 'PL1_Duration_s'))
        TW1 = Y | (Z << 5)

        PL2 = int(round(config.getfloat(power_source, 'PL2_Tdp_W') / 0.125))
        Y, Z = calc_time_window_vars(config.getfloat(power_source, 'PL2_Duration_s'))
        TW2 = Y | (Z << 5)

        regs[power_source]['MSR_PKG_POWER_LIMIT'] = PL1 | (1 << 15) | (TW1 << 17) | (PL2 << 32) | (1 << 47) | (
            TW2 << 49)

    return regs


def set_hwp(pref):
    # set HWP energy performance hints
    assert pref in ('performance', 'balance_performance', 'default', 'balance_power', 'power')
    n = glob.glob('/sys/devices/system/cpu/cpu[0-9]*/cpufreq/energy_performance_preference')
    for c in n:
        with open(c, 'wb') as f:
            f.write(pref.encode())


def power_thread(config, regs, exit_event):
    mchbar_mmio = MMIO(0xfed159a0, 8)

    while not exit_event.is_set():
        power_source = 'BATTERY' if is_on_battery() else 'AC'

        # set temperature trip point
        writemsr(0x1a2, regs[power_source]['MSR_TEMPERATURE_TARGET'])

        # set PL1/2 on MSR
        writemsr(0x610, regs[power_source]['MSR_PKG_POWER_LIMIT'])
        # set MCHBAR register to the same PL1/2 values
        mchbar_mmio.write32(0, regs[power_source]['MSR_PKG_POWER_LIMIT'] & 0xffffffff)
        mchbar_mmio.write32(4, regs[power_source]['MSR_PKG_POWER_LIMIT'] >> 32)

        wait_t = config.getfloat(power_source, 'Update_Rate_s')
        enable_hwp_mode = config.getboolean('AC', 'HWP_Mode', fallback=False)
        if power_source == 'AC' and enable_hwp_mode:
            cpu_usage = float(psutil.cpu_percent(interval=wait_t))
            # set full performance mode only when load is greater than this threshold (~ at least 1 core full speed)
            performance_mode = cpu_usage > 100. / (cpu_count() * 1.25)
            # check again if we are on AC, since in the meantime we might have switched to BATTERY
            if not is_on_battery():
                set_hwp('performance' if performance_mode else 'balance_performance')
        else:
            exit_event.wait(wait_t)


def main():
    config = load_config()
    regs = calc_reg_values(config)

    if not config.getboolean('GENERAL', 'Enabled'):
        return

    exit_event = Event()
    t = Thread(target=power_thread, args=(config, regs, exit_event))
    t.daemon = True
    t.start()

    undervolt(config)

    # handle dbus events for applying undervolt on resume from sleep/hybernate
    def handle_sleep_callback(sleeping):
        if not sleeping:
            undervolt(config)

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    # add dbus receiver only if undervolt is enabled in config
    if any(config.getfloat('UNDERVOLT', plane) != 0 for plane in VOLTAGE_PLANES):
        bus.add_signal_receiver(handle_sleep_callback, 'PrepareForSleep', 'org.freedesktop.login1.Manager',
                                'org.freedesktop.login1')

    try:
        GObject.threads_init()
        loop = GObject.MainLoop()
        loop.run()
    except (KeyboardInterrupt, SystemExit):
        pass

    exit_event.set()
    loop.quit()
    t.join(timeout=1)


if __name__ == '__main__':
    main()
