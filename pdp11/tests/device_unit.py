"""
Device register/state-machine unit tests (spec §3 layout), PRNG-seeded
where randomness is useful.

M2: DL11 console tests — register reset values, transmit path (XMTBUF write
-> scheduled completion -> DONE -> TX interrupt vector 064 at BR4), receive
path (terminal input -> scheduled pump -> RCVSTAT DONE -> RCVBUF read ->
clears DONE -> RX interrupt vector 060), byte access to either register
half (§6.2), and the §15 serialize/deserialize round-trip.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from pdp11.bus.abstract_bus import BusDevice
from pdp11.devices.dl11 import BR_LEVEL, DL11, DONE, IE, VECTOR_RX, VECTOR_TX
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
