"""
Stripped down version from https://github.com/vsergeev/python-periphery/blob/master/periphery/mmio.py
"""

import mmap
import os
import struct


class MMIOError(IOError):
    """Base class for MMIO errors."""


class MMIO(object):
    def __init__(self, physaddr: int, size: int) -> None:
        """Instantiate an MMIO object and map the region of physical memory
        specified by the address base `physaddr` and size `size` in bytes.
        Args:
            physaddr (int): base physical address of memory region.
            size (int): size of memory region.
        Returns:
            MMIO: MMIO object.
        Raises:
            MMIOError: if an I/O or OS error occurs.
            TypeError: if `physaddr` or `size` types are invalid.
        """
        self.mapping = None
        self._open(physaddr, size)

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, t, value, traceback):
        self.close()

    def _open(self, physaddr: int, size: int) -> None:
        if not isinstance(physaddr, int):
            raise TypeError("Invalid physaddr type, should be integer.")
        if not isinstance(size, int):
            raise TypeError("Invalid size type, should be integer.")

        pagesize = os.sysconf(os.sysconf_names['SC_PAGESIZE'])

        self._physaddr = physaddr
        self._size = size
        self._aligned_physaddr = physaddr - (physaddr % pagesize)
        self._aligned_size = size + (physaddr - self._aligned_physaddr)

        try:
            fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        except OSError as e:
            raise MMIOError(e.errno, "Opening /dev/mem: " + e.strerror)

        try:
            self.mapping = mmap.mmap(
                fd,
                self._aligned_size,
                flags=mmap.MAP_SHARED,
                prot=mmap.PROT_READ | mmap.PROT_WRITE,
                offset=self._aligned_physaddr,
            )
        except OSError as e:
            os.close(fd)
            raise MMIOError(e.errno, "Mapping /dev/mem: " + e.strerror)

        try:
            os.close(fd)
        except OSError as e:
            raise MMIOError(e.errno, "Closing /dev/mem: " + e.strerror)

    # Methods

    def _adjust_offset(self, offset):
        return offset + (self._physaddr - self._aligned_physaddr)

    def _validate_offset(self, offset, length):
        if (offset + length) > self._aligned_size:
            raise ValueError("Offset out of bounds.")

    def read32(self, offset: int) -> int:
        """Read 32-bits from the specified `offset` in bytes, relative to the
        base physical address of the MMIO region.
        Args:
            offset (int): offset from base physical address, in bytes.
        Returns:
            int: 32-bit value read.
        Raises:
            TypeError: if `offset` type is invalid.
            ValueError: if `offset` is out of bounds.
        """
        if not isinstance(offset, int):
            raise TypeError("Invalid offset type, should be integer.")

        offset = self._adjust_offset(offset)
        self._validate_offset(offset, 4)
        return struct.unpack("=L", self.mapping[offset : offset + 4])[0]

    def write32(self, offset: int, value: int) -> None:
        """Write 32-bits to the specified `offset` in bytes, relative to the
        base physical address of the MMIO region.
        Args:
            offset (int): offset from base physical address, in bytes.
            value (int): 32-bit value to write.
        Raises:
            TypeError: if `offset` or `value` type are invalid.
            ValueError: if `offset` or `value` are out of bounds.
        """
        if not isinstance(offset, int):
            raise TypeError("Invalid offset type, should be integer.")
        if not isinstance(value, int):
            raise TypeError("Invalid value type, should be integer.")
        if value < 0 or value > 0xFFFFFFFF:
            raise ValueError("Value out of bounds.")

        offset = self._adjust_offset(offset)
        self._validate_offset(offset, 4)
        self.mapping[offset : offset + 4] = struct.pack("=L", value)

    def close(self) -> None:
        """Unmap the MMIO object's mapped physical memory."""
        if self.mapping is None:
            return

        self.mapping.close()
        self.mapping = None

    # String representation

    def __str__(self) -> str:
        return f"MMIO 0x{self._physaddr:08x} (size={self._size:d})"
