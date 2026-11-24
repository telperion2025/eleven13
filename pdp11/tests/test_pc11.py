"""
PC11 paper tape reader/punch tests (devices/pc11.py; SIMH pdp11_pt.c):
reader GO -> frame -> PRB/DONE, read clears DONE, EOF -> ERR, punch into
the in-memory tape (§16 read-only container), vectors 070/074 at BR4,
snapshot round-trip.
"""

from __future__ import annotations

import pytest

from pdp11.devices.pc11 import (
    DONE,
    ERR,
    GO,
    IE,
    PC11,
    PPB,
    PPS,
    PRB,
    PRS,
    PUNCH_CYCLES,
    READER_CYCLES,
)
from pdp11.disk_images.container import PtImage
from pdp11.system_clock import SystemClock


def make(data: bytes | None = b"AB") -> tuple[PC11, SystemClock, PtImage]:
    clock = SystemClock()
    tape = PtImage("/tmp/test_pc11.pt", data=data) if data is not None else \
        PtImage("/tmp/test_pc11_empty.pt", data=b"")
    return PC11(clock, tape), clock, tape


def drain(clock: SystemClock, cycles: int) -> None:
    for fn in clock.advance(cycles):
        fn()


def test_pc11_reader_frame_and_interrupt():
    dev, clock, _tape = make(b"AB")
    dev.write_word(PRS, GO | IE)
    assert dev.read_word(PRS) & DONE == 0
    drain(clock, READER_CYCLES + 1)
    assert dev.read_word(PRS) & DONE
    assert dev.interrupt_pending() == (0o070, 4)
    assert dev.read_word(PRB) == ord("A")         # read clears DONE
    assert dev.read_word(PRS) & DONE == 0
    assert dev.interrupt_pending() is None


def test_pc11_reader_eof_sets_error():
    dev, clock, _tape = make(b"Z")
    dev.write_word(PRS, GO)
    drain(clock, READER_CYCLES + 1)
    assert dev.read_word(PRB) == ord("Z")
    dev.write_word(PRS, GO)                        # next frame: EOF
    drain(clock, READER_CYCLES + 1)
    assert dev.read_word(PRS) & (DONE | ERR)


def test_pc11_no_tape_is_error():
    clock = SystemClock()
    dev = PC11(clock, None)
    assert dev.read_word(PRS) & ERR
    dev.write_word(PRS, GO)
    drain(clock, READER_CYCLES + 1)
    assert dev.read_word(PRS) & (DONE | ERR)


def test_pc11_punch_in_memory_only():
    dev, clock, tape = make(b"")
    assert dev.read_word(PPS) & DONE               # punch ready
    dev.write_word(PPS, IE)
    dev.write_word(PPB, ord("Q"))
    assert dev.read_word(PPS) & DONE == 0
    drain(clock, PUNCH_CYCLES + 1)
    assert dev.read_word(PPS) & DONE
    assert dev.interrupt_pending() == (0o074, 4)
    assert dev.punch_tape() == b"Q"
    assert tape.data == b""                        # container read-only (§16)


def test_pc11_byte_access():
    dev, clock, _tape = make(b"xy")
    dev.write_byte(PRS, GO)
    drain(clock, READER_CYCLES + 1)
    assert dev.read_byte(PRB) == ord("x")


def test_pc11_snapshot_roundtrip():
    dev, clock, _tape = make(b"AB")
    dev.write_word(PRS, GO)
    drain(clock, READER_CYCLES + 1)
    dev.read_word(PRB)
    state = dev.serialize()
    dev2, _c2, _t2 = make(b"AB")
    dev2.deserialize(state)
    assert dev2.prs == dev.prs and dev2._pos == dev._pos
    with pytest.raises(ValueError):
        dev2.deserialize({"base": 0})
