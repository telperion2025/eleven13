"""
VT11 framebuffer tests (graphics/framebuffer.py; SIMH pdp11_vt.c +
display/vt11.c, DEC EK-VT11-TM-001 via bitsavers): DPC/MPR/XPR/YPR register
semantics, VS60-only NXM, the display-list engine (point/short vector/long
vector/graphplot/relative point/char/JMPA/DNOP/DSTOP) rendering into the
pixel framebuffer, STOP interrupt at 0320 BR4, byte access, DMA cycle
charging, snapshot round-trip.
"""

from __future__ import annotations

import pytest

from pdp11.board import SystemBoard
from pdp11.bus.abstract_bus import BusInit
from pdp11.config.profiles import PROFILES
from pdp11.graphics.framebuffer import (
    BR_LEVEL,
    CHAR_SPACING_W,
    DNOP,
    DPC,
    DSTOP,
    JMPA,
    MODE_CHAR,
    MODE_GRAPHX,
    MODE_LVECTOR,
    MODE_POINT,
    MODE_RELPOINT,
    MODE_SVECTOR,
    MPR,
    MPR_STOP,
    VECTOR_BASE,
    VT11,
    VT11_BASE,
    XPR,
    YPR,
)
from pdp11.system_clock import SystemClock

PROG = 0o1000


def make(crt="VR17", profile="11/70"):
    clock = SystemClock()
    board = SystemBoard(PROFILES[profile], clock)
    dev = VT11(clock, board.dma_host, crt=crt)
    board.attach_device(VT11_BASE, dev)
    return dev, board, clock


def prog(board, words, addr=PROG):
    for i, w in enumerate(words):
        board.ram.write_word(addr + 2 * i, w & 0o177777)


def run(dev, board, limit=100000):
    n = 0
    while dev.dma_request() and n < limit:
        board.service_dma()
        n += 1
    return n


def start(dev, addr=PROG):
    dev.write_word(DPC, addr)


# control words
def cw(mode, intensity=None):
    w = 0o100000 | (mode << 11)
    if intensity is not None:
        w |= 0o400 | (intensity << 7)
    return w


def test_registers_reset_and_dpc_start():
    dev, board, _clock = make()
    assert dev.read_word(DPC) == 0
    assert dev.read_word(MPR) & 0o170000 == MODE_CHAR << 11
    start(dev)
    assert dev.dma_request()
    assert dev.read_word(DPC) == PROG


def test_vs60_only_registers_nxm():
    dev, _board, _clock = make()
    with pytest.raises(BusInit):
        dev.read_word(0o10)
    with pytest.raises(BusInit):
        dev.write_word(0o20, 0)


def test_point_mode_draws_pixel_and_dstops():
    dev, board, _clock = make()
    prog(board, [cw(MODE_POINT), 0o100, 0o200, DSTOP])
    start(dev)
    run(dev, board)
    assert not dev.dma_request()                       # DSTOP froze it
    assert dev.fb.pixel(0o100 << 2, 0o200 << 2) == 1
    assert dev.read_word(MPR) & MPR_STOP               # internal stop flag
    assert dev.interrupt_pending() == (VECTOR_BASE, BR_LEVEL)


def test_dpc_write_clears_stop_interrupt():
    dev, board, _clock = make()
    prog(board, [DSTOP])
    start(dev)
    run(dev, board)
    assert dev.interrupt_pending() is not None
    start(dev, PROG)                                   # re-write DPC clears it
    assert dev.interrupt_pending() is None


def test_short_vector_draws_line():
    dev, board, _clock = make()
    # intensify=1, dy=0, dx=+8 (5-bit field 0o10)
    prog(board, [cw(MODE_SVECTOR), 0o1000 | 0o10, DSTOP])
    start(dev)
    dev._x = 100 << 2
    dev._y = 100 << 2
    # restart at the data word with position preset
    prog(board, [cw(MODE_SVECTOR), 0o1000 | 0o10, DSTOP], addr=0o2000)
    start(dev, 0o2000)
    dev.write_word(XPR, (100 << 2 >> 2) & 0o1777)      # x = 100
    dev.write_word(YPR, (100 << 2 >> 2) & 0o1777)      # y = 100
    dev._word_no = -1
    dev._pending = []
    run(dev, board)
    assert dev.fb.pixel(100 << 2, 100 << 2) == 1
    assert dev.fb.pixel((100 + 8) << 2, 100 << 2) == 1
    assert dev._x == (108 << 2)


def test_long_vector_two_words():
    dev, board, _clock = make()
    prog(board, [cw(MODE_LVECTOR), 0o40, -0o20 & 0o1777, DSTOP], addr=0o2000)
    start(dev, 0o2000)
    dev.write_word(XPR, 0o100)
    dev.write_word(YPR, 0o100)
    dev._word_no = -1
    dev._pending = []
    run(dev, board)
    assert dev.fb.pixel((0o100 + 0o40) << 2, (0o100 - 0o20) << 2) == 1
    assert dev._x == (0o140) << 2 and dev._y == (0o60) << 2


def test_graphplot_x_sets_position_and_step():
    dev, board, _clock = make()
    prog(board, [cw(MODE_GRAPHX), (0o12 << 10) | 0o300, DSTOP], addr=0o2000)
    start(dev, 0o2000)
    run(dev, board)
    assert dev.fb.pixel(0o300 << 2, 0) == 1
    xpr = dev.read_word(XPR)
    assert (xpr >> 10) & 0o77 == 0o12                   # graphplot step
    assert xpr & 0o1777 == 0o300                        # x position


def test_relative_point():
    dev, board, _clock = make()
    prog(board, [cw(MODE_RELPOINT), 0o20, 0o10, DSTOP], addr=0o2000)
    start(dev, 0o2000)
    dev.write_word(XPR, 0o100)
    dev.write_word(YPR, 0o100)
    dev._word_no = -1
    dev._pending = []
    run(dev, board)
    assert dev.fb.pixel((0o120) << 2, (0o110) << 2) == 1


def test_char_mode_advances_beam_no_stroke():
    dev, board, _clock = make()
    prog(board, [cw(MODE_CHAR), ord("7"), DSTOP], addr=0o2000)
    start(dev, 0o2000)
    run(dev, board)
    assert dev._x == CHAR_SPACING_W
    assert (dev.read_word(YPR) >> 10) & 0o77 == ord("7")


def test_jmpa_jumps():
    dev, board, _clock = make()
    prog(board, [JMPA | (0o2000 >> 1)], addr=PROG)      # jump to 0o2000
    prog(board, [cw(MODE_POINT), 0o50, 0o60, DSTOP], addr=0o2000)
    start(dev)
    run(dev, board)
    assert dev.fb.pixel(0o50 << 2, 0o60 << 2) == 1


def test_dnop_advances():
    dev, board, _clock = make()
    prog(board, [DNOP, DNOP, DSTOP])
    start(dev)
    run(dev, board)
    assert dev.read_word(DPC) == PROG + 3 * 2


def test_dma_cycles_charged():
    dev, board, _clock = make()
    prog(board, [DNOP, DSTOP])
    before = board.cycles_used
    start(dev)
    run(dev, board)
    assert board.cycles_used > before


def test_byte_register_access():
    dev, board, _clock = make()
    prog(board, [DSTOP])
    start(dev)
    run(dev, board)
    stop_word = dev.read_word(MPR)
    assert dev.read_byte(MPR + 1) == (stop_word >> 8) & 0xFF
    assert dev.read_byte(MPR) == stop_word & 0xFF
    dev.write_byte(DPC + 1, 0)                          # byte write to DPC restarts
    assert dev.dma_request()


def test_vr14_height():
    dev, _board, _clock = make(crt="VR14")
    assert dev.fb.height == 768 and dev.fb.width == 1024


def test_snapshot_roundtrip():
    dev, board, _clock = make()
    prog(board, [cw(MODE_POINT), 0o100, 0o200, DSTOP])
    start(dev)
    run(dev, board)
    state = dev.serialize()
    dev2, board2, _clock2 = make()
    dev2.deserialize(state)
    assert dev2._dpc == dev._dpc and dev2._x == dev._x and dev2._y == dev._y
    assert dev2.fb.bits == dev.fb.bits
    assert dev2.interrupt_pending() == (VECTOR_BASE, BR_LEVEL)
    with pytest.raises(ValueError):
        dev2.deserialize({"base": 0})
