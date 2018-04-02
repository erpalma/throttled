#!/usr/bin/env python2

import glob
import os
import struct

from periphery import MMIO
from time import sleep

UPDATE_RATE_SEC = 15


def writemsr(msr, val):
    n = glob.glob('/dev/cpu/[0-9]*/msr')
    for c in n:
        f = os.open(c, os.O_WRONLY)
        os.lseek(f, msr, os.SEEK_SET)
        os.write(f, struct.pack('Q', val))
        os.close(f)
    if not n:
        raise OSError("msr module not loaded (run modprobe msr)")


def main():
    mchbar_mmio = MMIO(0xfed159a0, 8)
    while True:
        # set temperature trip point to 97 C
        writemsr(0x1a2, 0x3000000)
        # set MSR to PL1 45W, max duration - PL2 45W, 2ms
        writemsr(0x610, 0x42816800fe8168)
        # set MCHBAR register to the same PL1/2 values
        mchbar_mmio.write32(0, 0x00fe8168)
        mchbar_mmio.write32(4, 0x00428168)
        sleep(UPDATE_RATE_SEC)


if __name__ == '__main__':
    main()
