"""
M6: the 11/94 Q22-CD 22-bit Q-Bus map (spec §5.7/§6) — 16 entries of 256 KB
blocks at the 0o170400 window, always in use for Q-Bus DMA (dma_host), unlike
the 11/70 Unibus map which is MMR3-BME-gated. Plus the J-11 I/D convention
(MMR0 bit 3 DSE) and the DCJ11_STRICT bus-error model (§5.5).
"""

from __future__ import annotations

import pytest

from pdp11.bus.abstract_bus import BusInit
from pdp11.cpu.mmu import MMR0_DSE, MMR0_MME, MMR3_KDS
from pdp11.memory.map_registers import Q22_MAP_WINDOW_BASE, Q22Map
from pdp11.tests.cpu_golden import make_machine


def test_q22map_translate_blocks():
    m = Q22Map()
    m.entries[0] = 0o040000
    assert m.translate(0o000123) == 0o040123
    m.entries[1] = 0o200000
    assert m.translate(0o1000000 + 0o777) == 0o200777       # block 1 (qa 0o1000000+, qa<21:18>)
    assert m.translate(0) == 0o040000                      # entry 0 covers block 0
    m.entries[0] = 0o17777600
    assert m.translate(0o177) == 0o17777777                # wraps at 22 bits


def test_q22map_window_read_write():
    _cpu, board = make_machine("11/94")
    assert isinstance(board.map, Q22Map)
    board.write_word(Q22_MAP_WINDOW_BASE + 0, 0o170000)     # entry 0 low word
    board.write_word(Q22_MAP_WINDOW_BASE + 2, 0o5)          # entry 0 bits <21:16>
    assert board.map.entries[0] == 0o1370000
    assert board.read_word(Q22_MAP_WINDOW_BASE + 0) == 0o170000
    assert board.read_word(Q22_MAP_WINDOW_BASE + 2) == 0o5
    board.write_word(Q22_MAP_WINDOW_BASE + 4, 0o000777)     # entry 1
    assert board.map.entries[1] == 0o000777
    assert board.map.entries[0] == 0o1370000


def test_q22map_always_applied_to_dma_host():
    """Unlike the Unibus map (MMR3 BME), the Q22-CD map is always in use on a
    22-bit Q-Bus — no enable bit."""
    _cpu, board = make_machine("11/94", ram_words=0o200000)
    board.write_word(Q22_MAP_WINDOW_BASE + 0, 0o040000)     # block 0 -> PA 0o40000
    board.write_word(Q22_MAP_WINDOW_BASE + 4, 0o100000)     # block 1 -> PA 0o100000
    board.ram.write_word(0o40000, 0o6060)
    board.ram.write_word(0o100000, 0o7070)
    board.cycles_used = 0
    assert board.dma_host.read_word(0o000000) == 0o6060
    assert board.dma_host.read_word(0o1000000) == 0o7070     # block 1 via map
    assert board.cycles_used == 2                           # one stolen cycle/word


def test_j11_dspace_via_mmr0_bit3():
    """J-11 (11/94): I/D split via MMR0 <3> DSE globally — the spec §5.7
    convention; the 11/70 uses MMR3 UDS/SDS/KDS instead (§22.4)."""
    _cpu, board = make_machine("11/94", ram_words=0o200000)
    assert board.mmu is not None
    board.write_word(0o172340, 0)                           # KIPAR0 identity
    board.write_word(0o172300, 0o077406)                    # KIPDR0 RW
    board.write_word(0o172360, 0o2000)                      # KDPAR0 -> PA 0o200000
    board.write_word(0o172320, 0o077406)                    # KDPDR0
    assert not board.mmu.dspace_enabled(0)                  # DSE clear
    board.write_word(0o177572, MMR0_MME)                    # MME only: still I space
    board.write_word(0o1000, 0o777)
    assert board.ram.read_word(0o1000) == 0o777 and board.ram.read_word(0o201000) == 0
    board.mmu.mmr0 |= MMR0_DSE                              # set DSE directly
    board.write_word(0o1000, 0o777)                         # now D space -> PA 0o201000
    assert board.ram.read_word(0o201000) == 0o777
    assert board.ram.read_word(0o1000) == 0o777             # previous write stayed in I


def test_1170_still_uses_mmr3_not_mmr0_bit3():
    _cpu, board = make_machine("11/70")
    assert board.mmu is not None
    board.mmu.mmr0 = MMR0_MME | MMR0_DSE                   # bit 3 must NOT enable D on 11/70
    assert not board.mmu.dspace_enabled(0)
    board.mmu.mmr3 = MMR3_KDS
    assert board.mmu.dspace_enabled(0)


def test_dcj11_strict_bus_errors():
    """DCJ11_STRICT: nonexistent memory AND odd-address word access both trap
    cleanly through the vector-000004 path (no LSI-11 byte-split quirk).
    (CPU-facing addresses on MMU machines are 16-bit virtual, so the
    nonexistent case uses a VA whose flat PA is past the small RAM.)"""
    _cpu, board = make_machine("11/94")
    with pytest.raises(BusInit):
        board.read_word(0o140000)                           # PA past RAM, below I/O alias
    with pytest.raises(BusInit):
        board.read_word(0o100001)                           # odd-address word
    with pytest.raises(BusInit):
        board.write_word(0o140000, 1)
