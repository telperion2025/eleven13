"""
TM11/TU10 magtape tests (devices/tm11.py; SIMH pdp11_tm.c): register file,
READ streaming a .tap record into memory via NPR byte stealing, tape-mark
-> EOF, WREOF, WRITE round-trip, SPACEF, REWIND, vector 000224 at BR5,
snapshot round-trip.
"""

from __future__ import annotations

import pytest

from pdp11.board import SystemBoard
from pdp11.config.profiles import PROFILES
from pdp11.devices.tm11 import (
    FNC_READ,
    FNC_SPACEF,
    FNC_WREOF,
    FNC_WRITE,
    MTBRC,
    MTC,
    MTC_DONE,
    MTC_GO,
    MTC_IE,
    MTCMA,
    MTS,
    RECORD_CYCLES,
    STA_BOT,
    STA_EOF,
    STA_ONL,
    TM11,
    TM11_BASE,
)
from pdp11.disk_images.container import TapImage
from pdp11.system_clock import SystemClock


def make(records, profile="11/70") -> tuple[TM11, SystemBoard, SystemClock, TapImage]:
    clock = SystemClock()
    board = SystemBoard(PROFILES[profile], clock)
    tape = TapImage("/tmp/test_tm11.tap", records=list(records))
    dev = TM11(clock, board.dma_host, tape)
    board.attach_device(TM11_BASE, dev)
    return dev, board, clock, tape


def drain(clock: SystemClock, cycles: int) -> None:
    for fn in clock.advance(cycles):
        fn()


def run_dma(board: SystemBoard, dev: TM11, limit: int = 100000) -> int:
    n = 0
    while dev.dma_request() and n < limit:
        board.service_dma()
        n += 1
    return n


def _cmd(dev: TM11, fct: int, bc: int = 0, ma: int = 0) -> None:
    dev.write_word(MTBRC, bc & 0o177777)
    dev.write_word(MTCMA, ma & 0o177777)
    dev.write_word(MTC, MTC_IE | (fct << 1) | MTC_GO)


def test_tm11_read_record_into_memory():
    dev, board, clock, _tape = make([b"HELLO"])
    assert dev.read_word(MTS) & STA_ONL
    _cmd(dev, FNC_READ, bc=-5, ma=0o1000)          # MTBRC = -5 -> 5 bytes
    assert dev.read_word(MTC) & MTC_DONE == 0
    drain(clock, RECORD_CYCLES + 1)                 # record comes up
    n = run_dma(board, dev)                         # stream 5 bytes
    assert n == 5
    assert bytes(board.ram.read_byte(0o1000 + i) for i in range(5)) == b"HELLO"
    assert dev.read_word(MTC) & MTC_DONE
    assert dev.interrupt_pending() == (0o224, 5)
    assert dev.read_word(MTBRC) == (-5 + 5) & 0o177777


def test_tm11_read_hits_tape_mark():
    dev, board, clock, _tape = make([b"AB", None, b"CD"])
    _cmd(dev, FNC_READ, bc=-10, ma=0o1000)
    drain(clock, RECORD_CYCLES + 1)
    run_dma(board, dev)
    _cmd(dev, FNC_READ, bc=-10, ma=0o2000)          # next record is the mark
    drain(clock, RECORD_CYCLES + 1)
    run_dma(board, dev)
    assert dev.read_word(MTS) & STA_EOF
    assert dev.read_word(MTC) & MTC_DONE
    assert dev.dma_request() is False


def test_tm11_write_record_roundtrip(tmp_path):
    path = str(tmp_path / "w.tap")
    dev, board, clock, tape = make([], )
    tape.path = path
    for i, ch in enumerate(b"WORLD"):
        board.ram.write_byte(0o1000 + i, ch)
    _cmd(dev, FNC_WRITE, bc=-5, ma=0o1000)
    drain(clock, RECORD_CYCLES + 1)
    run_dma(board, dev)
    assert dev.read_word(MTC) & MTC_DONE
    assert tape.records[0] == b"WORLD"
    # rewind and read it back
    from pdp11.devices.tm11 import FNC_REWIND
    _cmd(dev, FNC_REWIND)
    drain(clock, 0o7464100 + 1)
    assert dev.read_word(MTS) & STA_BOT
    _cmd(dev, FNC_READ, bc=-5, ma=0o2000)
    drain(clock, RECORD_CYCLES + 1)
    run_dma(board, dev)
    assert bytes(board.ram.read_byte(0o2000 + i) for i in range(5)) == b"WORLD"


def test_tm11_write_eof_and_space_forward():
    dev, board, clock, tape = make([b"AA", b"BB"])
    _cmd(dev, FNC_WREOF)
    drain(clock, RECORD_CYCLES + 1)
    assert dev.read_word(MTC) & MTC_DONE
    assert tape.records[-1] is None                 # tape mark appended
    tape.rewind()
    _cmd(dev, FNC_SPACEF, bc=-1)                    # skip one record
    drain(clock, RECORD_CYCLES + 1)
    assert dev.read_word(MTC) & MTC_DONE
    assert tape.pos == 1


def test_tm11_write_locked_is_illegal():
    clock = SystemClock()
    board = SystemBoard(PROFILES["11/70"], clock)
    tape = TapImage("/tmp/test_tm11_ro.tap", records=[b"X"])
    dev = TM11(clock, board.dma_host, tape, writable=False)
    _cmd(dev, FNC_WRITE, bc=-1, ma=0o1000)
    assert dev.read_word(MTS) & 0o100000            # STA_ILL
    assert dev.read_word(MTC) & MTC_DONE


def test_tm11_ema_extends_address():
    dev, board, clock, _tape = make([b"12345678"])
    dev.write_word(MTCMA, 0o177770)
    dev.write_word(MTBRC, -8 & 0o177777)
    dev.write_word(MTC, MTC_IE | (FNC_READ << 1) | MTC_GO | 0o20)  # EMA=1
    drain(clock, RECORD_CYCLES + 1)
    run_dma(board, dev)
    # EMA bits <5:4> -> <17:16>: xma = 0o400000 | 0o177770
    assert board.ram.read_word(0o177770) == 0       # low 16 bits untouched path
    assert dev.read_word(MTC) & MTC_DONE


def test_tm11_snapshot_roundtrip():
    dev, board, clock, _tape = make([b"DATA"])
    _cmd(dev, FNC_READ, bc=-4, ma=0o1000)
    drain(clock, RECORD_CYCLES + 1)
    run_dma(board, dev)
    state = dev.serialize()
    dev2, board2, clock2, _t2 = make([b"DATA"])
    dev2.deserialize(state)
    assert dev2.mtc == dev.mtc and dev2._buf == dev._buf
    assert dev2.tape.pos == dev.tape.pos
    with pytest.raises(ValueError):
        dev2.deserialize({"base": 0})
