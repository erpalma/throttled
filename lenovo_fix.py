#!/usr/bin/env python2

import ConfigParser
import glob
import os
import struct
import subprocess

from collections import defaultdict
from periphery import MMIO
from time import sleep

SYSFS_POWER_PATH = '/sys/class/power_supply/AC/online'
CONFIG_PATH = '/etc/lenovo_fix.conf'


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
    for Y in xrange(2**5):
        for Z in xrange(2**2):
            if t <= (2**Y) * (1. + Z / 4.) * 0.000977:
                return (Y, Z)
    raise Exception('Unable to find a good combination!')


def load_config():
    config = ConfigParser.ConfigParser()
    config.read(CONFIG_PATH)

    for power_source in ('AC', 'BATTERY'):
        assert 0 < config.getfloat(power_source, 'Update_Rate_s')
        assert 0 < config.getfloat(power_source, 'PL1_Tdp_W')
        assert 0 < config.getfloat(power_source, 'PL1_Duration_s')
        assert 0 < config.getfloat(power_source, 'PL2_Tdp_W')
        assert 0 < config.getfloat(power_source, 'PL2_Duration_S')
        assert 40 < config.getfloat(power_source, 'Trip_Temp_C') < 98

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


def main():
    config = load_config()
    regs = calc_reg_values(config)

    if not config.getboolean('GENERAL', 'Enabled'):
        return

    mchbar_mmio = MMIO(0xfed159a0, 8)
    while True:
        power_source = 'BATTERY' if is_on_battery() else 'AC'

        # set temperature trip point
        writemsr(0x1a2, regs[power_source]['MSR_TEMPERATURE_TARGET'])

        # set PL1/2 on MSR
        writemsr(0x610, regs[power_source]['MSR_PKG_POWER_LIMIT'])
        # set MCHBAR register to the same PL1/2 values
        mchbar_mmio.write32(0, regs[power_source]['MSR_PKG_POWER_LIMIT'] & 0xffffffff)
        mchbar_mmio.write32(4, regs[power_source]['MSR_PKG_POWER_LIMIT'] >> 32)

        sleep(config.getfloat(power_source, 'Update_Rate_s'))


if __name__ == '__main__':
    main()
