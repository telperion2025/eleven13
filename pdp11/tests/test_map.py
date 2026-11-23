"""
M5: the 11/70 Unibus map (spec §5.7/§6.1) — 31 mapping entries plus the fixed
I/O page, the register window at 0o172400 (4-byte stride, low word + high
6 bits), and the two DMA faces: dma_host applies the map when MMR3 BME is
set, massbus_host is direct 22-bit physical (RH70 BAE:BA).
"""

from __future__ import annotations

from pdp11.cpu.mmu import MAP_WINDOW_BASE, MMR3_BME
from pdp11.memory.map_registers import UnibusMap
from pdp11.tests.cpu_golden import make_machine


def test_map_translate_pages():
    m = UnibusMap()
    m.entries[0] = 0o200000
    assert m.translate(0o000123) == 0o200123
    m.entries[0] = 0o37777000                       # 22-bit wraparound
    assert m.translate(0o017777) == (0o37777000 + 0o17777) & 0o37777777
    m.entries[0] = 0
    # page 31 (760000-777777) is the fixed I/O page at 17760000+
    assert m.translate(0o760123) == 0o17760123
    assert m.translate(0o777777) == 0o17777777


def test_map_window_read_write():
    _cpu, board = make_machine("11/70")
    assert board.map is not None
    board.write_word(MAP_WINDOW_BASE + 0, 0o170000)         # entry 0 low word
    board.write_word(MAP_WINDOW_BASE + 2, 0o5)              # entry 0 bits <21:16>
    assert board.map.entries[0] == 0o1370000
    assert board.read_word(MAP_WINDOW_BASE + 0) == 0o170000
    assert board.read_word(MAP_WINDOW_BASE + 2) == 0o5
    board.write_word(MAP_WINDOW_BASE + 4, 0o000777)         # entry 1 low word
    assert board.map.entries[1] == 0o000777
    assert board.map.entries[0] == 0o1370000                # entry 0 preserved


def test_dma_host_uses_map_only_with_bme():
    _cpu, board = make_machine("11/70", ram_words=0o200000)
    board.ram.write_word(0o200000, 0o5150)
    board.map.entries[0] = 0o200000
    # BME clear: Unibus address passes through direct (18-bit window)
    assert board.dma_host.read_word(0o200000) == 0o5150
    board.write_word(0o172516, MMR3_BME)                    # MMR3 BME set
    assert board.mmu.mmr3 & MMR3_BME
    assert board.dma_host.read_word(0o000000) == 0o5150     # mapped through entry 0
    # massbus face is always direct 22-bit physical, map ignored
    assert board.massbus_host.read_word(0o200000) == 0o5150
    board.write_word(0o172516, 0)
    assert board.massbus_host.read_word(0o000000) == board.ram.read_word(0)
