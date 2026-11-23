"""
Bus map registers (spec §3 layout): the 11/70 Unibus Map — 22-bit physical
addressing for 18-bit Unibus DMA devices (§5.7/§13) — and (M6) the 11/94
Q22-CD map registers.

The 11/70 Unibus map (SIMH pdp11_io.c Map_Addr / pdp11_cpumod.c ubm_rd/wr,
verified): 31 usable entries, one per 8 KB Unibus page (page = ua<17:13>,
offset = ua<12:0>). Each entry is a 22-bit physical BYTE base:
    pa = (entry[page] + off) & 0o17777777        for pages 0..30
    pa = 0o17760000 + off                          for page 31 (the I/O page,
                                                   fixed — not mapped)
The map is enabled for DMA by MMR3 <5> BME (cpu_bme in SIMH); when disabled,
Unibus addresses pass through as 18-bit physical.

Register window (real 11/70: Unibus 772200-772276, each entry in TWO Unibus
words: even word = entry<15:0>, odd word = entry<21:16>; SIMH stores them as
longwords at 4-byte stride). In this emulator's 16-bit I/O-page convention
the window sits at 0o172400-0o172477 (see pdp11/cpu/mmu.py docstring and
docs/acceptance-checklist.md — on the real machine 772200 aliases the
supervisor APRs in 18-bit space).

The 11/94 Q22-CD map (M6): 16 entries, each the 22-bit base of a 256 KB
block of Q-Bus address space (block = qa<21:18>):
    pa = (entry[block] + (qa & 0o3777777)) & 0o17777777
The Q22-CD map is always in use on a 22-bit Q-Bus (no enable bit, unlike
the 11/70's MMR3 BME); registers sit at Q-Bus 0o170400-0o170477 (22-bit
mirror 0o17770400), 4-byte stride per entry (even word = entry<15:0>,
odd word = entry<21:16>) — same storage format as the Unibus map. In this
emulator's 16-bit I/O-page convention the window is at 0o170400.

References: PDP-11/70 Processor Handbook Unibus Map chapter
(EK-KB11C-TM-001); Q22-CD / J-11 documentation; SIMH pdp11_io.c/pdp11_cpumod.c (§21).
"""

from __future__ import annotations

from typing import Any

from pdp11.bus.abstract_bus import BusDevice

MAP_ENTRIES = 32          # 32 slots; entries 0..30 map, 31 = fixed I/O page
IOPAGE_BASE_22 = 0o17760000   # top 8 KB of the 22-bit physical space (§6.2)
MAP_PAGE_SIZE = 0o20000       # 8 KB Unibus page
MAP_PAGE_MASK = MAP_PAGE_SIZE - 1
MAP_PAGE_SHIFT = 13           # ua<17:13> = map page number
UNIBUS_MASK_18 = 0o777777     # 18-bit Unibus address space
PHYS_MASK_22 = 0o17777777     # 22-bit physical

# This emulator's 16-bit-convention window for the map registers.
MAP_WINDOW_BASE = 0o172400
MAP_WINDOW_SIZE = 0o100       # 32 entries x 4 bytes

Q22_MAP_ENTRIES = 16          # 16 blocks of 256 KB = 4 MB (22-bit Q-Bus)
Q22_BLOCK_SHIFT = 18          # qa<21:18> = map block number
Q22_BLOCK_MASK = 0o777777     # offset within a 256 KB block (qa<17:0>)
Q22_MAP_WINDOW_BASE = 0o170400
Q22_MAP_WINDOW_SIZE = 0o100   # 16 entries x 4 bytes


class UnibusMap(BusDevice):
    """The 11/70 Unibus map: 32 22-bit entries as an I/O-page register window
    plus ua18 -> pa22 translation for Unibus DMA (§5.7/§6.1)."""

    window_size = MAP_WINDOW_SIZE
    name = "UBM"

    def __init__(self) -> None:
        self.entries = [0] * MAP_ENTRIES

    def translate(self, ua: int) -> int:
        """Translate an 18-bit Unibus address to a 22-bit physical address.
        Page 31 (760000-777777) is the fixed I/O page at 17760000+."""
        ua &= UNIBUS_MASK_18
        page = (ua >> MAP_PAGE_SHIFT) & 0o37
        off = ua & MAP_PAGE_MASK
        if page == 0o37:                       # I/O page: fixed mapping
            return (IOPAGE_BASE_22 + off) & PHYS_MASK_22
        return (self.entries[page] + off) & PHYS_MASK_22

    # -- register window (4-byte stride per entry: low word, high 6 bits) ----
    def read_word(self, offset: int) -> int:
        entry = (offset >> 2) & 0o37
        val = self.entries[entry]
        if offset & 2:                         # odd word: bits <21:16>
            return (val >> 16) & 0o77
        return val & 0o177777                  # even word: bits <15:0>

    def write_word(self, offset: int, value: int) -> None:
        entry = (offset >> 2) & 0o37
        v = value & 0o177777
        if offset & 2:
            self.entries[entry] = (self.entries[entry] & 0o177777) | ((v & 0o77) << 16)
        else:
            self.entries[entry] = (self.entries[entry] & 0o17760000) | v
        self.entries[entry] &= PHYS_MASK_22

    def read_byte(self, offset: int) -> int:
        word = self.read_word(offset & 0o177774)
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        reg = offset & 0o177774
        cur = self.read_word(reg)
        if offset % 2 == 0:
            self.write_word(reg, (cur & 0o177400) | (value & 0o377))
        else:
            self.write_word(reg, (cur & 0o000377) | ((value & 0o377) << 8))

    def reset(self) -> None:
        """Bus init clears the map (all entries -> physical 0)."""
        self.entries = [0] * MAP_ENTRIES

    def serialize(self) -> dict[str, Any]:
        return {"entries": list(self.entries)}

    def deserialize(self, state: dict[str, Any]) -> None:
        if "entries" not in state:
            raise ValueError("UnibusMap snapshot missing 'entries'")
        if len(state["entries"]) != MAP_ENTRIES:
            raise ValueError("UnibusMap snapshot has wrong entry count")
        self.entries = [e & PHYS_MASK_22 for e in state["entries"]]


class Q22Map(BusDevice):
    """The 11/94 Q22-CD map: 16 22-bit entries as an I/O-page register window
    plus qa22 -> pa22 translation for Q-Bus DMA (§5.7/§6.1). Always in use on
    a 22-bit Q-Bus."""

    window_size = Q22_MAP_WINDOW_SIZE
    name = "Q22MAP"

    def __init__(self) -> None:
        self.entries = [0] * Q22_MAP_ENTRIES

    def translate(self, qa: int) -> int:
        """Translate a 22-bit Q-Bus address to a 22-bit physical address."""
        qa &= PHYS_MASK_22
        block = (qa >> Q22_BLOCK_SHIFT) & 0o17
        off = qa & Q22_BLOCK_MASK
        return (self.entries[block] + off) & PHYS_MASK_22

    def read_word(self, offset: int) -> int:
        entry = (offset >> 2) & 0o17
        val = self.entries[entry]
        if offset & 2:
            return (val >> 16) & 0o77
        return val & 0o177777

    def write_word(self, offset: int, value: int) -> None:
        entry = (offset >> 2) & 0o17
        v = value & 0o177777
        if offset & 2:
            self.entries[entry] = (self.entries[entry] & 0o177777) | ((v & 0o77) << 16)
        else:
            self.entries[entry] = (self.entries[entry] & 0o17760000) | v
        self.entries[entry] &= PHYS_MASK_22

    def read_byte(self, offset: int) -> int:
        word = self.read_word(offset & 0o177774)
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        reg = offset & 0o177774
        cur = self.read_word(reg)
        if offset % 2 == 0:
            self.write_word(reg, (cur & 0o177400) | (value & 0o377))
        else:
            self.write_word(reg, (cur & 0o000377) | ((value & 0o377) << 8))

    def reset(self) -> None:
        self.entries = [0] * Q22_MAP_ENTRIES

    def serialize(self) -> dict[str, Any]:
        return {"entries": list(self.entries)}

    def deserialize(self, state: dict[str, Any]) -> None:
        if "entries" not in state:
            raise ValueError("Q22Map snapshot missing 'entries'")
        if len(state["entries"]) != Q22_MAP_ENTRIES:
            raise ValueError("Q22Map snapshot has wrong entry count")
        self.entries = [e & PHYS_MASK_22 for e in state["entries"]]
