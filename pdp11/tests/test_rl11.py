"""
RL11/RL01 tests (devices/rl11.py; SIMH pdp11_rl.c): register file, GSTA,
relative SEEK, WRITE/READ sector round-trip via NPR word stealing, WCHK
mismatch -> CRC, vector 000160 at BR5, snapshot round-trip.
"""

from __future__ import annotations

import pytest

from pdp11.board import SystemBoard
from pdp11.config.profiles import PROFILES
from pdp11.devices.rl11 import (
    FNC_GSTA,
    FNC_READ,
    FNC_SEEK,
    FNC_WCHK,
    FNC_WRITE,
    RL11,
    RL11_BASE,
    RLBA,
    RLCS,
    RLCS_CRC,
    RLCS_DONE,
    RLCS_DRDY,
    RLCS_IE,
    RLDA,
    RLDA_GS,
    RLDA_SK_DIR,
    RLDS_BHO,
    RLDS_HDO,
    RLDS_LOCK,
    RLMP,
    SEEK_PER_CYL_CYCLES,
)
from pdp11.disk_images.container import open_rl
from pdp11.system_clock import SystemClock


def make(tmp_path, image=None) -> tuple[RL11, SystemBoard, SystemClock]:
    clock = SystemClock()
    board = SystemBoard(PROFILES["11/20"], clock)
    if image is None:
        image = open_rl(str(tmp_path / "test.rl01"))
    dev = RL11(clock, board.dma_host)
    dev.drive.image = image
    board.attach_device(RL11_BASE, dev)
    return dev, board, clock


def drain(clock: SystemClock, cycles: int) -> None:
    for fn in clock.advance(cycles):
        fn()


def run_dma(board: SystemBoard, dev: RL11, limit: int = 100000) -> int:
    n = 0
    while dev.dma_request() and n < limit:
        board.service_dma()
        n += 1
    return n


def _go(dev: RL11, fct: int) -> None:
    """RLCS write with DONE clear starts a command (no GO bit, SIMH rl_wr)."""
    dev.write_word(RLCS, (fct << 1) | RLCS_IE)


def test_rl11_reset_and_drdy(tmp_path):
    dev, _b, _c = make(tmp_path)
    assert dev.read_word(RLCS) & RLCS_DONE
    assert dev.read_word(RLCS) & RLCS_DRDY           # pack loaded, ready
    assert dev.interrupt_pending() is None


def test_rl11_get_status(tmp_path):
    dev, _b, _c = make(tmp_path)
    dev.write_word(RLDA, RLDA_GS)
    _go(dev, FNC_GSTA)
    assert dev.read_word(RLCS) & RLCS_DONE
    assert dev.read_word(RLMP) & (RLDS_LOCK | RLDS_BHO | RLDS_HDO)


def test_rl11_seek_relative(tmp_path):
    dev, _b, clock = make(tmp_path)
    dev.write_word(RLDA, (10 << 7) | RLDA_SK_DIR)     # +10 cylinders outward
    _go(dev, FNC_SEEK)
    assert dev.read_word(RLCS) & RLCS_DONE == 0        # seek pending
    drain(clock, 10 * SEEK_PER_CYL_CYCLES + 1)
    assert dev.read_word(RLCS) & RLCS_DONE
    assert dev.drive.cylinder == 10
    assert dev.interrupt_pending() == (0o160, 5)


def test_rl11_write_then_read_sector(tmp_path):
    dev, board, clock = make(tmp_path)
    for i in range(128):
        board.ram.write_word(0o1000 + 2 * i, 0o100000 + i)
    dev.write_word(RLBA, 0o1000)
    dev.write_word(RLDA, 0)                              # cyl 0 head 0 sect 0
    dev.write_word(RLMP, (-128) & 0o177777)              # 128 words = 1 sector
    _go(dev, FNC_WRITE)
    n = run_dma(board, dev)
    assert n == 128
    assert dev.read_word(RLCS) & RLCS_DONE
    assert dev.drive.sectors_written == 1
    # read it back into a different buffer
    dev.write_word(RLBA, 0o2000)
    dev.write_word(RLDA, 0)
    dev.write_word(RLMP, (-128) & 0o177777)
    _go(dev, FNC_READ)
    run_dma(board, dev)
    for i in range(128):
        assert board.ram.read_word(0o2000 + 2 * i) == 0o100000 + i
    assert dev.interrupt_pending() == (0o160, 5)


def test_rl11_write_check_mismatch_sets_crc(tmp_path):
    dev, board, clock = make(tmp_path)
    dev.write_word(RLBA, 0o1000)
    dev.write_word(RLDA, 0)
    dev.write_word(RLMP, (-4) & 0o177777)
    _go(dev, FNC_WRITE)
    run_dma(board, dev)
    board.ram.write_word(0o1000, 0o123456)               # corrupt memory
    dev.write_word(RLBA, 0o1000)
    dev.write_word(RLDA, 0)
    dev.write_word(RLMP, (-4) & 0o177777)
    _go(dev, FNC_WCHK)
    run_dma(board, dev)
    assert dev.read_word(RLCS) & RLCS_CRC


def test_rl11_snapshot_roundtrip(tmp_path):
    dev, board, clock = make(tmp_path)
    dev.write_word(RLBA, 0o1000)
    dev.write_word(RLDA, (3 << 7))
    dev.write_word(RLMP, (-128) & 0o177777)
    _go(dev, FNC_WRITE)
    run_dma(board, dev)
    state = dev.serialize()
    dev2, board2, _c2 = make(tmp_path)
    dev2.deserialize(state)
    assert dev2.rlcs == dev.rlcs and dev2.rlda == dev.rlda
    with pytest.raises(ValueError):
        dev2.deserialize({"base": 0})
