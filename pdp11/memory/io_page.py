"""
The I/O page: the top of physical memory where control/status registers
live (spec §6.2).

On every real PDP-11 the last 8 KB of the bus address space (octal 177600–
177777 region) is I/O space. The io_page owns the routing from a physical
address to whichever device owns that window; devices themselves never see
absolute addresses, only offsets into their own window.

Note per §6.2: both word AND byte access pass through intact; per-register
read/write masks are the device's responsibility, never the page's.
"""

from __future__ import annotations

from pdp11.bus.abstract_bus import BusDevice, BusInit


class IOPPage:  # (name kept short; "page" is the DEC term, not VM paging)
    """Routes word/byte reads and writes in I/O space to device windows."""

    def __init__(self, iopage_base: int) -> None:
        """
        iopage_base — octal start of the window from the machine profile
        (0o177600 for Unibus machines; the LSI-11 uses 0o170000 for the
        18-bit-extended window, handled through profile.physical_bits).
        """
        self._base: int = iopage_base
        # owner_ranges: sorted list of (start, size, device) — built at attach
        self._ranges: list[tuple[int, int, BusDevice]] = []
        self._map: dict[int, BusDevice] = {}  # byte offset -> device

    def attach(self, address: int, device: BusDevice) -> None:
        """
        Register `device` as owner of the window starting at octal `address`.
        Overlap checks run immediately: a real backplane short circuit is a
        wiring error, and the emulator should catch it at boot, not in use.
        """
        start_off = address - self._base
        end_off = start_off + device.window_size
        for lo, hi, _ in [(s, s + n, d) for s, n, d in self._ranges]:
            if start_off < hi and end_off > lo:  # interval overlap
                raise ValueError(f"bus window overlap at octal {oct(address)}")
        self._ranges.append((start_off, device.window_size, device))
        self._map[start_off] = device  # simple offset->device routing map

    def _resolve(self, addr: int) -> tuple[BusDevice, int]:
        """Map a physical address to (device, offset-in-window).
        Raises BusInit on a window miss — the machine's bus_error_model
        (§5.5) decides what happens next; this layer only signals."""
        off = addr - self._base
        for start, size, dev in self._ranges:
            if start <= off < start + size:
                return dev, off - start
        raise BusInit(f"unassigned I/O address octal {oct(addr)}")

    # -- bus facade ---------------------------------------------------------
    def read_word(self, addr: int) -> int:
        """Read a 16-bit I/O-space word at physical address `addr`."""
        dev, off = self._resolve(addr)
        return dev.read_word(off) & 0o177777       # hard mask: bus is 16-bit, always

    def write_word(self, addr: int, value: int) -> None:
        """Write a 16-bit I/O-space word. The bus only transports 16 bits."""
        dev, off = self._resolve(addr)
        dev.write_word(off, value & 0o177777)

    def read_byte(self, addr: int) -> int:
        """Read an 8-bit byte — legal on either half of any word (§6.2)."""
        dev, off = self._resolve(addr)
        return dev.read_byte(off) & 0o377          # mask: 8-bit transport

    def write_byte(self, addr: int, value: int) -> None:
        """Write an 8-bit byte, either half-word (DL11 console, RX01...)."""
        dev, off = self._resolve(addr)
        dev.write_byte(off, value & 0o377)
