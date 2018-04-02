#!/usr/bin/env python2

import glob
import os
import struct

from periphery import MMIO
from time import sleep

config = {
    'AC': {
        'UPDATE_RATE_SEC': 5,  # Update the registers every this many seconds
        'PL1_TDP_W': 44,  # Max package power for time window #1
        'PL1_DURATION_S': 28,  # Time window #1 duration
        'PL2_TDP_W': 44,  # Max package power for time window #2
        'PL2_DURATION_S': 0.002,  # Time window #2 duration
        'TRIP_TEMP_C': 97  # Max allowed temperature before throttling
    },
    'BATTERY': {
        'UPDATE_RATE_SEC': 30,  # Update the registers every this many seconds
        'PL1_TDP_W': 29,  # Max package power for time window #1
        'PL1_DURATION_S': 28,  # Time window #1 duration
        'PL2_TDP_W': 44,  # Max package power for time window #2
        'PL2_DURATION_S': 0.002,  # Time window #2 duration
        'TRIP_TEMP_C': 85  # Max allowed temperature before throttling
    },
}


def writemsr(msr, val):
    n = glob.glob('/dev/cpu/[0-9]*/msr')
    for c in n:
        f = os.open(c, os.O_WRONLY)
        os.lseek(f, msr, os.SEEK_SET)
        os.write(f, struct.pack('Q', val))
        os.close(f)
    if not n:
        raise OSError("msr module not loaded (run modprobe msr)")


def is_on_battery():
    with open('/sys/class/power_supply/AC/online') as f:
        return not bool(int(f.read()))


def calc_time_window_vars(t):
    for Y in xrange(2**5):
        for Z in xrange(2**2):
            if t <= (2**Y) * (1. + Z / 4.) * 0.000977:
                return (Y, Z)
    raise Exception('Unable to find a good combination!')


def check_config():
    for k in config:
        assert 0 < config[k]['UPDATE_RATE_SEC']
        assert 0 < config[k]['PL1_TDP_W']
        assert 0 < config[k]['PL2_TDP_W']
        assert 0 < config[k]['PL1_DURATION_S']
        assert 0 < config[k]['PL2_DURATION_S']
        assert 40 < config[k]['TRIP_TEMP_C'] < 98


def calc_reg_values():
    for k in config:
        # the critical temperature for this CPU is 100 C
        trip_offset = int(round(100 - config[k]['TRIP_TEMP_C']))
        config[k]['MSR_TEMPERATURE_TARGET'] = trip_offset << 24

        # 0.125 is the power unit of this CPU
        PL1 = int(round(config[k]['PL1_TDP_W'] / 0.125))
        Y, Z = calc_time_window_vars(config[k]['PL1_DURATION_S'])
        TW1 = Y | (Z << 5)

        PL2 = int(round(config[k]['PL2_TDP_W'] / 0.125))
        Y, Z = calc_time_window_vars(config[k]['PL2_DURATION_S'])
        TW2 = Y | (Z << 5)

        config[k]['MSR_PKG_POWER_LIMIT'] = PL1 | (1 << 15) | (TW1 << 17) | (PL2 << 32) | (1 << 47) | (TW2 << 49)


def main():
    check_config()
    calc_reg_values()

    mchbar_mmio = MMIO(0xfed159a0, 8)
    while True:
        cur_config = config['BATTERY' if is_on_battery() else 'AC']

        # set temperature trip point
        writemsr(0x1a2, cur_config['MSR_TEMPERATURE_TARGET'])

        # set PL1/2 on MSR
        writemsr(0x610, cur_config['MSR_PKG_POWER_LIMIT'])
        # set MCHBAR register to the same PL1/2 values
        mchbar_mmio.write32(0, cur_config['MSR_PKG_POWER_LIMIT'] & 0xffffffff)
        mchbar_mmio.write32(4, cur_config['MSR_PKG_POWER_LIMIT'] >> 32)

        sleep(cur_config['UPDATE_RATE_SEC'])


if __name__ == '__main__':
    main()
