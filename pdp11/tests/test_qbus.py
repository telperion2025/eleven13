"""
Q-Bus arbitration + LSI-11 bus-error quirk tests (spec §6.1/§5.5) — M4.

BIAK/BDMG grant chains behave like the Unibus BR/BG + NPR lines: highest
request level wins, ties to the first slot, BDMG outranks BIAK, and one bus
cycle is charged per DMA word. The 11/03 board routes through the QBus
arbiter, and its LSI11_TOLERANT bus-error model splits odd-address word
access into two byte transfers instead of trapping (§5.5 quirk), while
nonexistent memory still traps through vector 000004.
"""

from __future__ import annotations

from typing import Any

import pytest

from pdp11.board import SystemBoard
from pdp11.bus.abstract_bus import BusDevice, BusInit
from pdp11.bus.qbus import QBus
from pdp11.config.profiles import PROFILES
from pdp11.cpu.registers import Reg
from pdp11.system_clock import SystemClock


class FakeDevice(BusDevice):
    """Scripted slot device: fixed interrupt request, optional DMA flag."""

    window_size = 2

    def __init__(self, vector: int = 0, ipl: int = 0, dma: bool = False, name: str = "?") -> None:
        self._req = (vector, ipl) if vector else None
        self._dma = dma
        self.name = name
        self.ticked = 0

    def read_word(self, offset: int) -> int:
        return 0

    def write_word(self, offset: int, value: int) -> None:
        pass

    def read_byte(self, offset: int) -> int:
        return 0

    def write_byte(self, offset: int, value: int) -> None:
        pass

    def dma_request(self) -> bool:
        return self._dma

    def interrupt_pending(self) -> tuple[int, int] | None:
        return self._req

    def tick(self) -> None:
        self.ticked += 1

    def serialize(self) -> dict[str, Any]:
        return {}

    def deserialize(self, state: dict[str, Any]) -> None:
        pass


class WordMover(FakeDevice):
    """Fake BDMG device: each tick() moves one word through the board."""

    def __init__(self, board: SystemBoard, addr: int, value: int, **kw: Any) -> None:
        super().__init__(dma=True, **kw)
        self._board = board
        self._addr = addr
        self._value = value

    def tick(self) -> None:
        super().tick()
        self._board.write_word(self._addr, self._value)


def test_biak_highest_level_wins_ties_first_slot():
    bus = QBus(physical_bits=18)
    bus.attach(FakeDevice(vector=0o300, ipl=4, name="a"))
    bus.attach(FakeDevice(vector=0o304, ipl=6, name="b"))
    bus.attach(FakeDevice(vector=0o220, ipl=6, name="c"))
    assert bus.grant_interrupt() == (0o304, 6)       # highest BIRQ; first slot at level 6


def test_bdmg_outranks_biak_and_first_slot_wins():
    bus = QBus(physical_bits=18)
    bus.attach(FakeDevice(vector=0o300, ipl=7, name="intr"))
    a = FakeDevice(dma=True, name="A")
    b = FakeDevice(dma=True, name="B")
    bus.attach(a)
    bus.attach(b)
    assert bus.grant_dma() is a
    assert bus.arbitrate() == ("dma", a)              # BDMG beats BIRQ7 (§6.1)
    bus2 = QBus(physical_bits=18)
    bus2.attach(FakeDevice(vector=0o300, ipl=7, name="intr"))
    assert bus2.arbitrate() == ("intr", (0o300, 7))
    assert bus2.grant_interrupt() == (0o300, 7)
    bus2.attach(FakeDevice(vector=0o300, ipl=3))      # below BIRQ4 range: ignored
    assert bus2.grant_interrupt() == (0o300, 7)


def test_word_cycles_one_per_word():
    assert QBus.word_cycles(0o100) == 0o100


def test_board_uses_qbus_on_1103():
    board = SystemBoard(PROFILES["11/03"], SystemClock())
    assert board.qbus is not None and board.unibus is None
    assert board.qbus.addr_mask == 0o777777           # 18-bit physical
    dev = FakeDevice(vector=0o264, ipl=5)
    board.attach_device(0o177170, dev)
    assert board.devices == [dev]                     # slot order
    assert board.poll_interrupt() == (0o264, 5)       # CPU asks the bus
    assert board.service_dma() == 0


def test_qbus_dma_steals_cycles_on_1103():
    """§5.5: BDMG cycle stealing on the Q-Bus charges one cycle per word."""
    board = SystemBoard(PROFILES["11/03"], SystemClock())
    cpu = board.cpu
    cpu.state.R[Reg.PC] = 0o000100
    cpu.state.R[Reg.SP] = 0o017776
    board.ram.write_word(0o000100, 0o000240)          # NOP
    mover = WordMover(board, addr=0o000500, value=0o123456)
    board.attach_device(0o177400, mover)
    cycle_before = board.clock.cycle
    cpu.step()
    assert board.ram.read_word(0o000500) == 0o123456
    assert board.clock.cycle - cycle_before == 0o3 + 1 + 1   # base + fetch + stolen word


def test_lsi11_tolerant_odd_word_splits_into_bytes():
    """§5.5 LSI11_TOLERANT: the 11/03 tolerates odd-address word access by
    splitting it into two byte transfers — no bus-error trap."""
    board = SystemBoard(PROFILES["11/03"], SystemClock())
    board.ram.write_word(0o100, 0o125715)
    assert board.read_word(0o101) == 0o253            # high byte of word at 0o100, low byte 0
    board.write_word(0o103, 0o041102)
    assert board.read_byte(0o103) == 0o102            # low byte of the value
    assert board.read_byte(0o104) == 0o102            # high byte
    # the 11/20 with UNIBUS_TIMEOUT still traps on odd word access
    board20 = SystemBoard(PROFILES["11/20"], SystemClock())
    with pytest.raises(BusInit):
        board20.read_word(0o101)


def test_lsi11_nonexistent_memory_traps_to_vector_4():
    """§5.5: nonexistent memory on the 11/03 is a clean Q-Bus timeout ->
    vector 000004 (the backplane-dependent variants are flagged §22.1)."""
    board = SystemBoard(PROFILES["11/03"], SystemClock())
    with pytest.raises(BusInit):
        board.read_word(0o200000)                     # beyond RAM, not I/O page
    cpu = board.cpu
    cpu.state.R[Reg.PC] = 0o000100
    cpu.state.R[Reg.SP] = 0o017776
    board.ram.write_word(0o000100, 0o012737)          # MOV #0,@#0o177700
    board.ram.write_word(0o000102, 0o000000)
    board.ram.write_word(0o000104, 0o177700)          # unassigned I/O window -> timeout
    board.ram.write_word(0o000004, 0o005000)          # vector 000004 -> PC 0o5000
    board.ram.write_word(0o000006, 0o000340)          #   new PSW IPL 7
    cpu.step()                                        # -> bus error trap
    assert cpu.state.R[Reg.PC] == 0o005000            # trapped through vector 4
    assert cpu.state.psw.ipl == 7
