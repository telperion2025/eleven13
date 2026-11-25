"""
TQK11 MSCP tape tests (devices/tqk11.py; SIMH pdp11_tq.c / pdp11_uqssp.h /
pdp11_mscp.h): IP/SA register semantics, the 4-step UQSSP init handshake,
command/response rings, MSCP GUS/SCC/READ/WRITE/WTM/POS against a SIMH
.tap container, tape-mark/EOM/offline/write-protect error paths, the
ring interrupt, byte register access, snapshot round-trip.
"""

from __future__ import annotations

import pytest

from pdp11.board import SystemBoard
from pdp11.config.profiles import PROFILES
from pdp11.devices.tqk11 import (
    BR_LEVEL,
    CMD_MOD,
    CMD_OPC,
    CMD_REFH,
    CMD_REFL,
    CMD_UN,
    COMM_RI,
    EF_SXC,
    GUS_LNT_T,
    I_OPCD,
    IP,
    MD_RWD,
    ONL_UIDD,
    OP_ABO,
    OP_GUS,
    OP_POS,
    OP_RD,
    OP_SCC,
    OP_WR,
    OP_WTM,
    POS_LNT,
    RECORD_CYCLES,
    RSP_OPF,
    RSP_STS,
    RW_BAH,
    RW_BAL,
    RW_BCH,
    RW_BCL,
    RW_LNT_T,
    RW_RSZL,
    SA,
    SA_S1,
    SA_S1H_IE,
    SA_S1H_VL,
    SA_S2,
    SA_S3,
    SA_S4,
    SA_S4H_GO,
    SB_OFL_NV,
    SB_WPR_HW,
    SCC_LNT,
    ST_CMD,
    ST_OFL,
    ST_SUC,
    ST_TMK,
    ST_WPR,
    TQK11,
    TQK11_BASE,
    UQ_ADDR,
    UQ_CID_TMSCP,
    UQ_DESC_F,
    UQ_DESC_OWN,
    UQ_HLNT,
    WTM_LNT,
)
from pdp11.disk_images.container import TapImage
from pdp11.system_clock import SystemClock

COMM = 0o10000
VEC = 0o670
CMD_ADDR = 0o20000
RSP_ADDR = 0o30000
BUF = 0o40000


def make(records, profile="11/94", writable=True, with_tape=True):
    clock = SystemClock()
    board = SystemBoard(PROFILES[profile], clock)
    tape = TapImage("/tmp/test_tqk11.tap", records=list(records)) if with_tape else None
    dev = TQK11(clock, board.dma_host, tape, writable=writable)
    board.attach_device(TQK11_BASE, dev)
    return dev, board, clock, tape


def drain(clock: SystemClock, cycles: int) -> None:
    for fn in clock.advance(cycles):
        fn()


def run_dma(board: SystemBoard, dev: TQK11, limit: int = 200000) -> int:
    n = 0
    while dev.dma_request() and n < limit:
        board.service_dma()
        n += 1
    return n


def put_desc(board: SystemBoard, addr: int, desc: int) -> None:
    board.ram.write_word(addr, desc & 0xFFFF)
    board.ram.write_word(addr + 2, (desc >> 16) & 0xFFFF)


def desc_at(board: SystemBoard, addr: int) -> int:
    return board.ram.read_word(addr) | (board.ram.read_word(addr + 2) << 16)


def put_cmd(board: SystemBoard, addr: int, ref: int, unit: int, opc: int,
            mod: int = 0, bc: int = 0, ba: int = 0, lnt: int = RW_LNT_T) -> None:
    """Write a UQSSP header + MSCP command packet at `addr` (buffer word w
    lives at addr-4+2w, per SIMH d[])."""
    board.ram.write_word(addr - 4, lnt)                  # UQ_HLNT
    board.ram.write_word(addr - 2, UQ_CID_TMSCP << 8)     # UQ_HCTC: SEQ, TMSCP
    words = {CMD_REFL: ref & 0xFFFF, CMD_REFH: (ref >> 16) & 0xFFFF,
             CMD_UN: unit, CMD_OPC: opc, CMD_MOD: mod,
             RW_BCL: bc & 0xFFFF, RW_BCH: (bc >> 16) & 0xFFFF,
             RW_BAL: ba & 0xFFFF, RW_BAH: (ba >> 16) & 0xFFFF}
    for w, v in words.items():
        board.ram.write_word(addr - 4 + 2 * w, v)


def init(dev: TQK11, board: SystemBoard, comm: int = COMM, vec: int = VEC,
         cq_log: int = 2, rq_log: int = 2) -> None:
    """Drive the 4-step UQSSP init handshake; offer one rsp ring entry."""
    dev.write_word(IP, 0)                                 # re-init -> step 1
    assert dev.read_word(SA) & SA_S1
    dev.write_word(SA, SA_S1H_VL | SA_S1H_IE | (cq_log << 11) | (rq_log << 8) | (vec >> 2))
    assert dev.read_word(SA) & SA_S2
    dev.write_word(SA, comm & 0xFFFE)                     # step 2: comm lo
    assert dev.read_word(SA) & SA_S3
    dev.write_word(SA, (comm >> 16) & 0x7FFF)             # step 3: comm hi
    assert dev.read_word(SA) & SA_S4
    run_dma(board, dev)                                   # zero the comm region
    dev.write_word(SA, SA_S4H_GO)                         # step 4: go
    assert dev.read_word(SA) == 0
    dev._int_pending = False                              # consume the init interrupt
    for i in range(4):                                    # offer all rsp entries
        put_desc(board, comm + 4 * i, UQ_DESC_OWN | (RSP_ADDR + 0o40 * i))


def submit(board: SystemBoard, dev: TQK11, ref: int, unit: int, opc: int, **kw) -> None:
    put_cmd(board, CMD_ADDR, ref, unit, opc, **kw)
    put_desc(board, dev._cq["ba"] + dev._cq["idx"], UQ_DESC_OWN | CMD_ADDR)


def poll(board: SystemBoard, dev: TQK11, clock: SystemClock) -> None:
    """IP read (poll) -> DMA fetch -> motion latency -> DMA exec/response."""
    dev.read_word(IP)
    run_dma(board, dev)
    drain(clock, RECORD_CYCLES + 1)
    run_dma(board, dev)


def get_rsp(board: SystemBoard, dev: TQK11) -> tuple[int, list[int]]:
    """Read the most recently used response ring entry."""
    entry = dev._rq["ba"] + ((dev._rq["idx"] - 4) & (dev._rq["lnt"] - 1))
    desc = desc_at(board, entry)
    addr = desc & UQ_ADDR
    lnt = board.ram.read_word(addr - 4)
    pkt = [board.ram.read_word(addr - 4 + 2 * i) for i in range((lnt + 4) // 2)]
    return desc, pkt


def test_tqk11_init_handshake_and_vector():
    dev, board, clock, _tape = make([b"X"])
    init(dev, board)
    assert dev._vector == VEC
    assert dev._csta == 8                                 # CST_UP
    assert dev._rq["ba"] == COMM and dev._rq["lnt"] == 16
    assert dev._cq["ba"] == COMM + 16 and dev._cq["lnt"] == 16
    # comm region was zeroed by step 4 (except the rsp desc the host wrote after)
    assert board.ram.read_word(COMM + COMM_RI) == 0       # RI flag word untouched


def test_tqk11_ip_read_zero_and_reset():
    dev, board, clock, _tape = make([b"X"])
    init(dev, board)
    assert dev.read_word(IP) == 0
    dev.write_word(IP, 0)                                 # re-init
    assert dev.read_word(SA) & SA_S1
    assert dev._vector == 0


def test_tqk11_byte_register_access():
    dev, board, clock, _tape = make([b"X"])
    dev.write_word(IP, 0)
    assert dev.read_byte(SA + 1) == ((SA_S1 | 0x0200 | 0x0040) >> 8) & 0xFF
    assert dev.read_byte(SA) == (SA_S1 | 0x0200 | 0x0040) & 0xFF
    dev.write_byte(IP + 1, 0)                             # byte write to IP = re-init
    assert dev.read_word(SA) & SA_S1


def test_tqk11_empty_ring_poll_is_quiet():
    dev, board, clock, _tape = make([b"X"])
    init(dev, board)
    poll(board, dev, clock)
    assert desc_at(board, dev._cq["ba"] + dev._cq["idx"]) == 0  # nothing released
    assert desc_at(board, COMM) & UQ_DESC_OWN             # rsp entry still offered
    assert dev.interrupt_pending() is None


def test_tqk11_gus_response_and_ring_interrupt():
    dev, board, clock, _tape = make([b"X"])
    init(dev, board)
    submit(board, dev, 0x1234, 0, OP_GUS)
    poll(board, dev, clock)
    desc, pkt = get_rsp(board, dev)
    assert not (desc & UQ_DESC_OWN) and (desc & UQ_DESC_F)     # released by controller
    assert desc & UQ_ADDR == RSP_ADDR
    assert pkt[UQ_HLNT] == GUS_LNT_T
    assert pkt[RSP_OPF] & 0xFF == OP_GUS | 0x80
    assert pkt[RSP_STS] == ST_SUC
    assert pkt[CMD_REFL] == 0x1234
    assert pkt[ONL_UIDD] == (3 << 8) | 8                        # tape class, TU81
    assert board.ram.read_word(COMM + COMM_RI) == 1             # rsp ring int flag
    assert dev.interrupt_pending() == (VEC, BR_LEVEL)


def test_tqk11_scc():
    dev, board, clock, _tape = make([b"X"])
    init(dev, board)
    submit(board, dev, 1, 0, OP_SCC, lnt=SCC_LNT)
    poll(board, dev, clock)
    _desc, pkt = get_rsp(board, dev)
    assert pkt[UQ_HLNT] == SCC_LNT
    assert pkt[RSP_STS] == ST_SUC


def test_tqk11_read_record_into_memory():
    dev, board, clock, _tape = make([b"HELLO"])
    init(dev, board)
    submit(board, dev, 7, 0, OP_RD, bc=10, ba=BUF)
    poll(board, dev, clock)
    assert bytes(board.ram.read_byte(BUF + i) for i in range(5)) == b"HELLO"
    _desc, pkt = get_rsp(board, dev)
    assert pkt[RSP_STS] == ST_SUC
    assert pkt[RW_BCL] == 5                                   # bytes transferred
    assert pkt[RW_RSZL] == 5                                  # record size


def test_tqk11_read_tape_mark_is_serious_exception():
    dev, board, clock, _tape = make([b"AB", None, b"CD"])
    init(dev, board)
    submit(board, dev, 1, 0, OP_RD, bc=10, ba=BUF)
    poll(board, dev, clock)
    submit(board, dev, 2, 0, OP_RD, bc=10, ba=BUF + 0o100)
    poll(board, dev, clock)
    _desc, pkt = get_rsp(board, dev)
    assert pkt[RSP_STS] == ST_TMK
    assert (pkt[RSP_OPF] >> 8) & EF_SXC


def test_tqk11_read_past_eom_sets_eot_flag():
    dev, board, clock, _tape = make([b"AB"])
    init(dev, board)
    for ref in (1, 2):
        submit(board, dev, ref, 0, OP_RD, bc=10, ba=BUF)
        poll(board, dev, clock)
    _desc, pkt = get_rsp(board, dev)
    assert pkt[RSP_STS] == ST_SUC
    assert (pkt[RSP_OPF] >> 8) & 0x08                         # EF_EOT
    assert pkt[RW_RSZL] == 0


def test_tqk11_write_record_roundtrip(tmp_path):
    dev, board, clock, tape = make([])
    tape.path = str(tmp_path / "w.tap")
    init(dev, board)
    for i, ch in enumerate(b"WORLD"):
        board.ram.write_byte(BUF + i, ch)
    submit(board, dev, 9, 0, OP_WR, bc=5, ba=BUF)
    poll(board, dev, clock)
    assert tape.records[0] == b"WORLD"
    _desc, pkt = get_rsp(board, dev)
    assert pkt[RSP_STS] == ST_SUC
    assert pkt[RW_RSZL] == 5


def test_tqk11_write_tape_mark():
    dev, board, clock, tape = make([b"AA"])
    init(dev, board)
    submit(board, dev, 3, 0, OP_WTM)
    poll(board, dev, clock)
    assert tape.records[-1] is None
    _desc, pkt = get_rsp(board, dev)
    assert pkt[UQ_HLNT] == WTM_LNT
    assert pkt[RSP_STS] == ST_SUC


def test_tqk11_reposition_rewind():
    dev, board, clock, tape = make([b"AA", b"BB"])
    init(dev, board)
    submit(board, dev, 1, 0, OP_RD, bc=2, ba=BUF)
    poll(board, dev, clock)
    submit(board, dev, 2, 0, OP_POS, mod=MD_RWD, lnt=POS_LNT)
    poll(board, dev, clock)
    assert tape.pos == 0
    _desc, pkt = get_rsp(board, dev)
    assert pkt[UQ_HLNT] == POS_LNT
    assert pkt[RSP_STS] == ST_SUC


def test_tqk11_offline_unit():
    dev, board, clock, _tape = make([], with_tape=False)
    init(dev, board)
    submit(board, dev, 1, 0, OP_RD, bc=4, ba=BUF)
    poll(board, dev, clock)
    _desc, pkt = get_rsp(board, dev)
    assert pkt[RSP_STS] == ST_OFL | SB_OFL_NV


def test_tqk11_write_protected():
    dev, board, clock, _tape = make([b"X"], writable=False)
    init(dev, board)
    submit(board, dev, 1, 0, OP_WR, bc=1, ba=BUF)
    poll(board, dev, clock)
    _desc, pkt = get_rsp(board, dev)
    assert pkt[RSP_STS] == ST_WPR | SB_WPR_HW


def test_tqk11_invalid_opcode():
    dev, board, clock, _tape = make([b"X"])
    init(dev, board)
    submit(board, dev, 1, 0, 0x77)
    poll(board, dev, clock)
    _desc, pkt = get_rsp(board, dev)
    assert pkt[RSP_STS] == ST_CMD | I_OPCD


def test_tqk11_snapshot_roundtrip():
    dev, board, clock, _tape = make([b"DATA"])
    init(dev, board)
    submit(board, dev, 5, 0, OP_ABO, lnt=16)
    poll(board, dev, clock)
    state = dev.serialize()
    dev2, board2, clock2, _t2 = make([b"DATA"])
    dev2.deserialize(state)
    assert dev2._csta == dev._csta and dev2._vector == dev._vector
    assert dev2._cq == dev._cq and dev2._rq == dev._rq
    assert dev2.tape.pos == dev.tape.pos
    with pytest.raises(ValueError):
        dev2.deserialize({"base": 0})
