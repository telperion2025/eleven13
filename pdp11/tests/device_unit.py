"""
Device register/state-machine unit tests (spec §3 layout), PRNG-seeded
where randomness is useful.

M2: DL11 console tests — register reset values, transmit path (XMTBUF write
-> scheduled completion -> DONE -> TX interrupt vector 064 at BR4), receive
path (terminal input -> scheduled pump -> RCVSTAT DONE -> RCVBUF read ->
clears DONE -> RX interrupt vector 060), byte access to either register
half (§6.2), and the §15 serialize/deserialize round-trip.

M3: KW11-L line clock (self-scheduling 16_667-cycle tick, LKS read clears
DONE, vector 000104 at BR6) and the RK11/RK05 reference driver (§7):
register file, seek scheduling, NPR word-at-a-time DMA transfers against a
real container, multi-sector auto-increment, error paths, and snapshots.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from pdp11.board import SystemBoard
from pdp11.bus.abstract_bus import BusDevice
from pdp11.config.profiles import PROFILES
from pdp11.devices import dz11, kw11l, rk11_rk05, rx11
from pdp11.devices.dl11 import BR_LEVEL, DL11, DONE, IE, VECTOR_RX, VECTOR_TX
from pdp11.disk_images.container import RK05_SECTOR, open_rk05, open_rx01, rk05_sector_address
from pdp11.graphics.render_backend import RenderBackend
from pdp11.graphics.terminal import ConsoleTerminal
from pdp11.system_clock import SystemClock


class FakeBackend(RenderBackend):
    """Scripted host backend for tests: queued input bytes, captured output."""

    def __init__(self, input_bytes: bytes = b"") -> None:
        self.input = bytearray(input_bytes)
        self.output: list[int] = []

    def getch(self) -> int | None:
        return self.input.pop(0) if self.input else None

    def putch(self, byte: int) -> None:
        self.output.append(byte)

    def flush(self) -> None:
        pass


def make_dl11(input_bytes: bytes = b"") -> tuple[DL11, SystemClock, FakeBackend]:
    clock = SystemClock()
    backend = FakeBackend(input_bytes)
    term = ConsoleTerminal(backend)
    return DL11(clock, term), clock, backend


def drain(clock: SystemClock, cycles: int) -> None:
    for fn in clock.advance(cycles):
        fn()


def test_dl11_reset_state():
    dl, _clock, _b = make_dl11()
    assert dl.read_word(0) == 0                          # RCVSTAT: no DONE, no IE
    assert dl.read_word(4) == DONE                       # XMTSTAT: transmitter ready
    assert dl.interrupt_pending() is None


def test_dl11_transmit_path_and_interrupt():
    dl, clock, backend = make_dl11()
    dl.write_word(4, IE)                                 # XMTSTAT: enable TX interrupt
    dl.write_word(6, ord("A"))                           # XMTBUF <- 'A': starts transmit
    assert dl.read_word(4) & DONE == 0                   # busy while transmitting
    assert dl.interrupt_pending() is None                # no interrupt yet
    drain(clock, 0o1000 + 1)                             # TX_CYCLES later...
    assert backend.output == [ord("A")]                  # ...byte emitted...
    assert dl.read_word(4) & DONE                        # ...DONE set...
    assert dl.interrupt_pending() == (VECTOR_TX, BR_LEVEL)   # ...and BR4/064 pending


def test_dl11_receive_path_and_interrupt():
    dl, clock, _b = make_dl11(b"B")
    dl.write_word(0, IE)                                 # RCVSTAT: enable RX interrupt
    drain(clock, 0o310 + 1)                              # one input pump period
    assert dl.read_word(0) & DONE                        # DONE set by pump
    assert dl.interrupt_pending() == (VECTOR_RX, BR_LEVEL)
    assert dl.read_word(2) == ord("B")                   # RCVBUF returns the byte
    assert dl.read_word(0) & DONE == 0                   # reading RCVBUF clears DONE


def test_dl11_byte_access_either_half():
    dl, clock, backend = make_dl11()
    dl.write_byte(6, ord("Z"))                           # XMTBUF via byte write
    drain(clock, 0o1000 + 1)
    assert backend.output == [ord("Z")]
    dl2, clock2, _ = make_dl11(b"Q")
    drain(clock2, 0o310 + 1)
    assert dl2.read_byte(2) == ord("Q")                  # RCVBUF via byte read
    assert dl2.read_byte(3) == 0                         # high half of RCVBUF


def test_dl11_register_window_and_serialize():
    dl, _clock, _b = make_dl11()
    assert isinstance(dl, BusDevice) and dl.window_size == 8
    dl.write_word(4, IE)
    state: dict[str, Any] = dl.serialize()
    dl2, _c2, _b2 = make_dl11()
    dl2.deserialize(state)
    assert dl2.tx_ie is True and dl2.tx_done is True
    with pytest.raises(ValueError):
        dl2.deserialize({"rxbuf": 0})                    # schema mismatch -> ValueError


def test_dl11_prng_seeded_transfer_loop():
    """PRNG-seeded randomized transmit/receive interleaving (spec §3)."""
    rng = random.Random(0o13)                            # 13 octal = 11 decimal
    dl, clock, backend = make_dl11()
    sent = [rng.randrange(0o40, 0o177) for _ in range(16)]
    received = [rng.randrange(0o40, 0o177) for _ in range(16)]
    backend.input.extend(received)
    for b in sent:
        dl.write_word(6, b)
        drain(clock, 0o1000 + 1)
    assert backend.output == sent
    got = []
    for _b in received:
        drain(clock, 0o310 + 1)                    # pump period: load next byte
        got.append(dl.read_word(2))                # RCVBUF read clears DONE
    assert got == received


# -- M3: KW11-L line-frequency clock (§5.6/§7) -----------------------------------


def make_kw11l() -> tuple[kw11l.KW11L, SystemClock]:
    clock = SystemClock()
    return kw11l.KW11L(clock), clock


def test_kw11l_tick_sets_done_and_read_clears_it():
    kw, clock = make_kw11l()
    assert kw.read_word(0) == 0                              # LKS: no DONE, no IE
    assert kw.interrupt_pending() is None
    drain(clock, kw11l.TICK_CYCLES + 1)                      # one 60 Hz tick fires
    assert kw.ticks == 1
    assert kw.read_word(0) & kw11l.DONE                      # DONE set...
    assert kw.read_word(0) & kw11l.DONE == 0                 # ...reading LKS clears it
    drain(clock, kw11l.TICK_CYCLES)
    assert kw.ticks == 2
    drain(clock, kw11l.TICK_CYCLES)
    assert kw.ticks == 3                                     # self-rescheduling


def test_kw11l_interrupt_vector_104_br6():
    kw, clock = make_kw11l()
    kw.write_word(0, kw11l.IE)                               # set IE in LKS
    assert kw.interrupt_pending() is None                    # DONE still clear
    drain(clock, kw11l.TICK_CYCLES + 1)
    assert kw.interrupt_pending() == (0o000104, 6)           # vector 104, BR6
    kw.read_word(0)                                          # clear DONE
    assert kw.interrupt_pending() is None                    # request drops


def test_kw11l_byte_access_and_serialize():
    kw, clock = make_kw11l()
    kw.write_byte(0, kw11l.IE)                               # byte write to LKS
    assert kw.ie
    drain(clock, kw11l.TICK_CYCLES + 1)
    state: dict[str, Any] = kw.serialize()
    kw2, _c2 = make_kw11l()
    kw2.deserialize(state)
    assert kw2.ie and kw2.done and kw2.ticks == 1
    with pytest.raises(ValueError):
        kw2.deserialize({"done": False})                     # schema mismatch


# -- M3: RK11/RK05 reference driver (§7) ------------------------------------------


def make_rk11(tmp_path, image=None) -> tuple[rk11_rk05.RK11, SystemBoard, SystemClock]:
    """11/20 board + RK11 at 177400 with an RK05 pack, mirroring the
    main.assemble wiring (a blank image when none is given)."""
    clock = SystemClock()
    board = SystemBoard(PROFILES["11/20"], clock)
    if image is None:
        image = open_rk05(str(tmp_path / "test.rk05"))
    rk = rk11_rk05.RK11(clock, board, rk11_rk05.RK05(image))
    board.attach_device(rk11_rk05.RK11_BASE, rk)
    return rk, board, clock


def sector_words(seed: int = 0o100000) -> list[int]:
    """A full 512-byte sector as 256 recognizable words."""
    return [(seed + i) & 0o177777 for i in range(RK05_SECTOR // 2)]


def pack_sector(image, n: int, words: list[int]) -> None:
    data = bytearray(RK05_SECTOR)
    for i, w in enumerate(words):
        data[2 * i] = w & 0o377
        data[2 * i + 1] = (w >> 8) & 0o377
    image.write_sector(n, bytes(data))


def unpack_sector(data: bytes) -> list[int]:
    return [data[2 * i] | (data[2 * i + 1] << 8) for i in range(len(data) // 2)]


def run_dma(board: SystemBoard, rk: rk11_rk05.RK11, limit: int = 100000) -> int:
    """Drive NPR cycle stealing until the transfer completes (one word per
    service_dma call, exactly as cpu.step does mid-instruction)."""
    n = 0
    while rk.dma_request() and n < limit:
        board.service_dma()
        n += 1
    return n


def test_rk11_reset_and_register_file(tmp_path):
    rk, board, _clock = make_rk11(tmp_path)
    assert rk.window_size == 0o20 and isinstance(rk, BusDevice)
    assert board.read_word(0o177404) & rk11_rk05.RKCS_DONE    # RKCS: DONE at reset
    assert board.read_word(0o177400) & rk11_rk05.RKDS_DRY     # RKDS: pack loaded
    board.write_word(0o177406, 0o177400)                      # RKWC = -256
    board.write_word(0o177410, 0o004000)                      # RKBA
    board.write_word(0o177412, 0o000120)                      # RKDA: cyl 5
    assert board.read_word(0o177406) == 0o177400
    assert board.read_word(0o177410) == 0o004000
    assert board.read_word(0o177412) == 0o000120
    assert board.read_byte(0o177413) == 0o000                 # RKDA high byte
    board.write_byte(0o177412, 0o000040)                      # RKDA low byte write
    assert board.read_word(0o177412) == 0o000040              # high byte of 0o120 is 0
    assert rk.interrupt_pending() is None                     # IE clear


def test_rk11_seek_schedules_completion(tmp_path):
    rk, board, clock = make_rk11(tmp_path)
    board.write_word(0o177412, 5 << rk11_rk05.RKDA_CYL_SHIFT)  # seek to cyl 5
    board.write_word(0o177404, (rk11_rk05.FCT_SEEK << 1) | rk11_rk05.RKCS_IE | rk11_rk05.RKCS_GO)
    assert not (board.read_word(0o177404) & rk11_rk05.RKCS_DONE)   # busy while seeking
    assert rk.dma_request() is False
    delay = rk.drive.seek_delay(5)
    drain(clock, delay - 1)
    assert rk.drive.cylinder == 0                          # not yet
    drain(clock, 2)
    assert rk.drive.cylinder == 5                          # seek completed
    assert board.read_word(0o177404) & rk11_rk05.RKCS_DONE
    assert rk.interrupt_pending() == (rk11_rk05.VECTOR, rk11_rk05.BR_LEVEL)


def test_rk11_read_sector_via_npr_cycle_stealing(tmp_path):
    rk, board, _clock = make_rk11(tmp_path)
    words = sector_words()
    pack_sector(rk.drive.image, 0, words)                  # type: ignore[union-attr]
    board.write_word(0o177406, 0o177400)                   # RKWC = -256 (one sector)
    board.write_word(0o177410, 0o004000)                   # RKBA = 0o4000
    board.write_word(0o177412, 0)                          # RKDA = cyl 0 surf 0 sect 0
    board.write_word(0o177404, (rk11_rk05.FCT_READ << 1) | rk11_rk05.RKCS_IE | rk11_rk05.RKCS_GO)
    assert rk.dma_request()                                # NPR asserted
    moved = run_dma(board, rk)
    assert moved == 256                                    # one word per stolen cycle
    assert not rk.dma_request()
    for i, w in enumerate(words):
        assert board.ram.read_word(0o4000 + 2 * i) == w    # sector landed in RAM
    assert board.read_word(0o177404) & rk11_rk05.RKCS_DONE
    assert board.read_word(0o177410) == 0o4000 + 0o1000   # RKBA: 256 words = 512 bytes
    assert board.read_word(0o177412) == 1                 # RKDA past the sector
    assert rk.interrupt_pending() == (0o000220, 5)         # vector 220 at BR5


def test_rk11_write_sector_roundtrips_to_container(tmp_path):
    rk, board, _clock = make_rk11(tmp_path)
    words = sector_words(0o052525)
    for i, w in enumerate(words):
        board.ram.write_word(0o4000 + 2 * i, w)
    da = 1 << rk11_rk05.RKDA_CYL_SHIFT                     # cyl 1, surf 0, sect 0
    board.write_word(0o177406, 0o177400)                   # RKWC = -256
    board.write_word(0o177410, 0o004000)                   # RKBA
    board.write_word(0o177412, da)                         # RKDA
    board.write_word(0o177404, (rk11_rk05.FCT_WRITE << 1) | rk11_rk05.RKCS_GO)
    run_dma(board, rk)
    assert board.read_word(0o177404) & rk11_rk05.RKCS_DONE
    assert unpack_sector(rk.drive.image.read_sector(       # type: ignore[union-attr]
        rk05_sector_address(1, 0, 0))) == words


def test_rk11_multisector_auto_increment_and_partial(tmp_path):
    rk, board, _clock = make_rk11(tmp_path)
    pack_sector(rk.drive.image, 0, sector_words(0o100000))   # type: ignore[union-attr]
    pack_sector(rk.drive.image, 1, sector_words(0o140000))   # type: ignore[union-attr]
    # two full sectors: RKWC = -512
    board.write_word(0o177406, 0o177000)
    board.write_word(0o177410, 0o4000)
    board.write_word(0o177412, 0)
    board.write_word(0o177404, (rk11_rk05.FCT_READ << 1) | rk11_rk05.RKCS_GO)
    run_dma(board, rk)
    assert board.read_word(0o177412) == 2                  # RKDA auto-incremented
    assert board.ram.read_word(0o4000) == 0o100000
    assert board.ram.read_word(0o4000 + 0o1000) == 0o140000  # second sector (512 bytes on)
    # partial transfer: RKWC = -10 moves only 10 words
    rk2, board2, _ = make_rk11(tmp_path)
    pack_sector(rk2.drive.image, 0, sector_words())          # type: ignore[union-attr]
    board2.write_word(0o177406, (0o200000 - 10) & 0o177777)
    board2.write_word(0o177410, 0o5000)
    board2.write_word(0o177412, 0)
    board2.write_word(0o177404, (rk11_rk05.FCT_READ << 1) | rk11_rk05.RKCS_GO)
    moved = run_dma(board2, rk2)
    assert moved == 10
    for i in range(10):
        assert board2.ram.read_word(0o5000 + 2 * i) == (0o100000 + i) & 0o177777
    assert board2.ram.read_word(0o5000 + 20) == 0          # untouched


def test_rk11_error_paths(tmp_path):
    # no pack loaded: data command -> RKER NO_PACK, RKCS ERROR|DONE
    clock = SystemClock()
    board = SystemBoard(PROFILES["11/20"], clock)
    rk = rk11_rk05.RK11(clock, board, rk11_rk05.RK05(None))
    board.attach_device(rk11_rk05.RK11_BASE, rk)
    assert board.read_word(0o177400) & rk11_rk05.RKDS_DRY == 0   # no DRY
    board.write_word(0o177412, 0)
    board.write_word(0o177404, (rk11_rk05.FCT_READ << 1) | rk11_rk05.RKCS_GO)
    assert board.read_word(0o177404) & rk11_rk05.RKCS_ERROR
    assert board.read_word(0o177402) & rk11_rk05.RKER_NO_PACK
    assert board.read_word(0o177402) == 0                  # RKER read cleared it
    # bad address: cylinder out of range
    rk2, board2, _ = make_rk11(tmp_path)
    board2.write_word(0o177412, 0o77760)                   # cyl 0o777
    board2.write_word(0o177404, (rk11_rk05.FCT_READ << 1) | rk11_rk05.RKCS_GO)
    assert board2.read_word(0o177404) & rk11_rk05.RKCS_ERROR
    assert board2.read_word(0o177402) & rk11_rk05.RKER_BAD_ADDR


def test_rk11_serialize_roundtrip(tmp_path):
    rk, board, _clock = make_rk11(tmp_path)
    board.write_word(0o177406, 0o177400)
    board.write_word(0o177410, 0o004000)
    state: dict[str, Any] = rk.serialize()
    rk2, _b2, _c2 = make_rk11(tmp_path)
    rk2.deserialize(state)
    assert rk2.rkwc == 0o177400 and rk2.rkba == 0o4000
    assert rk2.rkcs & rk11_rk05.RKCS_DONE
    with pytest.raises(ValueError):
        rk2.deserialize({"rkcs": 0})                       # schema mismatch


# -- M4: DZ11 8-line multiplexer (SIMH pdp11_dz.c model; vectors 300/304) --------


def make_dz11(input_bytes: bytes = b"", br_level: int = dz11.BR_LEVEL_QBUS):
    clock = SystemClock()
    backend = FakeBackend(input_bytes)
    term = ConsoleTerminal(backend)
    return dz11.DZ11(clock, term, br_level=br_level), clock, backend


def test_dz11_reset_and_csr():
    dz, _clock, _b = make_dz11()
    assert dz.window_size == 0o10 and isinstance(dz, BusDevice)
    assert dz.read_word(0) & dz11.CSR_TRDY                  # transmitter ready at reset
    assert dz.interrupt_pending() is None
    dz.write_word(0, dz11.CSR_MSE | dz11.CSR_RIE | dz11.CSR_TIE)
    assert dz.read_word(0) & (dz11.CSR_MSE | dz11.CSR_RIE | dz11.CSR_TIE)
    assert dz.read_word(0) & dz11.CSR_TRDY                  # still ready


def test_dz11_receive_path_and_rx_vector():
    dz, clock, _b = make_dz11(b"A")
    dz.write_word(0, dz11.CSR_MSE | dz11.CSR_RIE)           # scan on, rx int enb
    drain(clock, dz11.INPUT_POLL_CYCLES + 1)                # one scan tick
    assert dz.read_word(0) & dz11.CSR_RDONE                 # char in silo
    assert dz.interrupt_pending() == (dz11.VECTOR_RX, dz11.BR_LEVEL_QBUS)   # 0o300 BIRQ4
    word = dz.read_word(2)                                  # RBUF: pop silo
    assert word & dz11.RBUF_CHAR == ord("A")
    assert word & dz11.RBUF_VALID
    assert dz.read_word(0) & dz11.CSR_RDONE == 0            # silo empty -> RDONE clear
    assert dz.interrupt_pending() is None


def test_dz11_transmit_path_and_tx_vector():
    dz, clock, backend = make_dz11()
    dz.write_word(0, dz11.CSR_MSE | dz11.CSR_TIE)           # scan on, tx int enb, line 0
    assert dz.read_word(0) & dz11.CSR_TRDY
    dz.write_word(6, ord("Z"))                              # TDR <- 'Z' on line 0
    assert dz.read_word(0) & dz11.CSR_TRDY == 0             # busy while transmitting
    assert dz.interrupt_pending() is None
    drain(clock, dz11.TX_CYCLES + 1)
    assert backend.output == [ord("Z")]                    # emitted on line 0
    assert dz.read_word(0) & dz11.CSR_TRDY
    assert dz.interrupt_pending() == (dz11.VECTOR_TX, dz11.BR_LEVEL_QBUS)   # 0o304


def test_dz11_byte_access_and_serialize():
    dz, clock, backend = make_dz11()
    dz.write_word(0, dz11.CSR_MSE | dz11.CSR_TIE)
    dz.write_byte(6, ord("Q"))                              # TDR via byte write
    drain(clock, dz11.TX_CYCLES + 1)
    assert backend.output == [ord("Q")]
    state: dict[str, Any] = dz.serialize()
    dz2, _c2, _b2 = make_dz11()
    dz2.deserialize(state)
    assert dz2.tcr == dz.tcr and dz2.csr & dz11.CSR_TRDY
    with pytest.raises(ValueError):
        dz2.deserialize({"csr": 0})


# -- M4: RX11/RX01 floppy (SIMH pdp11_rx.c model; vector 264 BR5) -----------------


def make_rx11(tmp_path, image=None) -> tuple[rx11.RX11, SystemClock]:
    clock = SystemClock()
    if image is None:
        image = open_rx01(str(tmp_path / "floppy.rx01"))
    return rx11.RX11(clock, image), clock


def test_rx11_init_sets_done_and_drdy(tmp_path):
    rx, clock = make_rx11(tmp_path)
    assert rx.window_size == 0o04
    drain(clock, rx11.SEEK_PER_TRACK + 1)                   # implicit init completes
    assert rx.read_word(0) & rx11.RXCS_DONE
    assert not rx.read_word(0) & rx11.RXCS_ERR
    assert rx.esr & rx11.RXES_DRDY and rx.esr & rx11.RXES_ID
    assert rx.interrupt_pending() is None                   # IE clear


def test_rx11_read_sector_via_tr_handshake(tmp_path):
    """The RT-11 RX boot path shape (§11.2): READ sector, then EMPTY the
    buffer byte-at-a-time through RXDB with the TR handshake."""
    image_sector = bytes((i * 3) & 0xFF for i in range(rx11.SECTOR_BYTES))
    rx, clock = make_rx11(tmp_path)
    rx.image.write_sector(0, image_sector)                  # track 0, sector 1
    rx.image.flush()
    drain(clock, rx11.SEEK_PER_TRACK + 1)                   # finish init
    rx.write_word(0, (rx11.FCT_READ << 1) | rx11.RXCS_IE | rx11.RXCS_GO)   # 0o107
    assert rx.read_word(0) & rx11.RXCS_TR                   # TR set for sector
    rx.write_word(2, 1)                                     # RXDB <- sector 1
    drain(clock, rx11.TR_CYCLES + 1)                        # RWDS svc -> TR
    assert rx.read_word(0) & rx11.RXCS_TR
    rx.write_word(2, 0)                                     # RXDB <- track 0
    drain(clock, rx11.TR_CYCLES + 1)                        # RWDT svc -> schedules seek
    drain(clock, rx11.SEEK_PER_TRACK + rx11.CMD_CYCLES + 2)  # RWXFR svc -> DONE
    assert rx.read_word(0) & rx11.RXCS_DONE                 # sector in buffer
    assert rx.interrupt_pending() == (rx11.VECTOR, rx11.BR_LEVEL)         # 0o264 BR5
    # empty the buffer into a list
    rx.write_word(0, (rx11.FCT_EMPTY << 1) | rx11.RXCS_GO)  # EMPTY|GO
    got = bytearray()
    for _ in range(rx11.SECTOR_BYTES):
        while not rx.read_word(0) & rx11.RXCS_TR:
            drain(clock, rx11.TR_CYCLES + 1)
        got.append(rx.read_word(2) & 0xFF)
        drain(clock, rx11.TR_CYCLES + 1)
    assert bytes(got) == image_sector


def test_rx11_write_sector_roundtrips_to_container(tmp_path):
    rx, clock = make_rx11(tmp_path)
    drain(clock, rx11.SEEK_PER_TRACK + 1)
    payload = bytes((0x80 + i) & 0xFF for i in range(rx11.SECTOR_BYTES))
    rx.write_word(0, (rx11.FCT_FILL << 1) | rx11.RXCS_GO)   # FILL
    for _i, b in enumerate(payload):
        assert rx.read_word(0) & rx11.RXCS_TR
        rx.write_word(2, b)
        drain(clock, rx11.TR_CYCLES + 1)
    rx.write_word(0, (rx11.FCT_WRITE << 1) | rx11.RXCS_GO)  # WRITE, TR for sector
    assert rx.read_word(0) & rx11.RXCS_TR
    rx.write_word(2, 5)                                     # sector 5
    drain(clock, rx11.TR_CYCLES + 1)                        # RWDS svc
    assert rx.read_word(0) & rx11.RXCS_TR
    rx.write_word(2, 2)                                     # track 2
    drain(clock, rx11.TR_CYCLES + 1)                        # RWDT svc -> seek
    drain(clock, rx11.SEEK_PER_TRACK + rx11.CMD_CYCLES + 2)
    assert rx.read_word(0) & rx11.RXCS_DONE
    assert rx.image.read_sector(2 * 26 + 4) == payload      # track 2 sector 5 -> idx 2*26+4


def test_rx11_errors_and_serialize(tmp_path):
    # no diskette: reset -> error 0o10
    clock = SystemClock()
    rx = rx11.RX11(clock, None)
    assert rx.read_word(0) & rx11.RXCS_ERR
    assert rx.ecode == rx11.ECODE_NO_DISK
    # bad sector number
    rx2, clock2 = make_rx11(tmp_path)
    drain(clock2, rx11.SEEK_PER_TRACK + 1)
    rx2.write_word(0, (rx11.FCT_READ << 1) | rx11.RXCS_GO)
    rx2.write_word(2, 0)                                    # sector 0 invalid (1-26)
    drain(clock2, rx11.TR_CYCLES + 1)                       # RWDS svc
    rx2.write_word(2, 0)                                    # track 0
    drain(clock2, rx11.TR_CYCLES + 1)                       # RWDT svc
    drain(clock2, rx11.SEEK_PER_TRACK + rx11.CMD_CYCLES + 2)  # RWXFR -> error
    assert rx2.read_word(0) & rx11.RXCS_ERR
    assert rx2.ecode == rx11.ECODE_BAD_SECTOR
    # serialize round-trip
    state: dict[str, Any] = rx2.serialize()
    rx3, _c3 = make_rx11(tmp_path)
    rx3.deserialize(state)
    assert rx3.ecode == rx11.ECODE_BAD_SECTOR and rx3.csr == rx2.csr
    with pytest.raises(ValueError):
        rx3.deserialize({"csr": 0})
