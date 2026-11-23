"""
Unibus arbitration tests (spec §6.1) — M2.

Covers: NPR (DMA) outranking BR (interrupt) grants; BR/BG daisy chain
(highest BR level wins, ties to the lowest slot); NPR daisy chain (first
slot wins); cycle stealing at word granularity (§5.5); and the SystemBoard
integration (the CPU asks the bus for grants, never the devices directly).
"""

from __future__ import annotations

from typing import Any

from pdp11.board import SystemBoard
from pdp11.bus.abstract_bus import BusDevice
from pdp11.bus.unibus import Unibus
from pdp11.config.profiles import PROFILES
from pdp11.cpu.registers import Reg
from pdp11.system_clock import SystemClock


class FakeDevice(BusDevice):
    """Minimal BusDevice with configurable interrupt/DMA requests."""

    window_size = 2
    name = "FAKE"

    def __init__(self, vector: int = 0, ipl: int = 0, dma: bool = False,
                 name: str = "FAKE") -> None:
        self._req: tuple[int, int] | None = (vector, ipl) if ipl else None
        self._dma = dma
        self.name = name
        self.reg = 0
        self.ticked = 0

    def read_word(self, offset: int) -> int:
        return self.reg

    def write_word(self, offset: int, value: int) -> None:
        self.reg = value & 0o177777

    def read_byte(self, offset: int) -> int:
        return self.reg & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        self.reg = (self.reg & 0o177700) | (value & 0o377)

    def dma_request(self) -> bool:
        return self._dma

    def interrupt_pending(self) -> tuple[int, int] | None:
        return self._req

    def tick(self) -> None:
        self.ticked += 1

    def serialize(self) -> dict[str, Any]:
        return {"reg": self.reg}

    def deserialize(self, state: dict[str, Any]) -> None:
        if "reg" not in state:
            raise ValueError("missing 'reg'")
        self.reg = state["reg"]


class WordMover(FakeDevice):
    """Fake DMA device: each tick() moves one word onto the bus (one stolen
    bus cycle), via the board reference it was constructed with."""

    def __init__(self, board: SystemBoard, addr: int, value: int, **kw: Any) -> None:
        super().__init__(dma=True, **kw)
        self._board = board
        self._addr = addr
        self._value = value

    def tick(self) -> None:
        super().tick()
        self._board.write_word(self._addr, self._value)   # one bus cycle


def test_daisy_chain_same_level_first_slot_wins():
    bus = Unibus(physical_bits=18)
    a = FakeDevice(vector=0o100, ipl=5, name="A")
    b = FakeDevice(vector=0o104, ipl=5, name="B")
    bus.attach(a)
    bus.attach(b)
    assert bus.grant_interrupt() == (0o100, 5)     # slot order breaks the tie


def test_daisy_chain_highest_br_level_wins():
    bus = Unibus(physical_bits=18)
    bus.attach(FakeDevice(vector=0o100, ipl=4, name="low"))
    bus.attach(FakeDevice(vector=0o104, ipl=7, name="high"))
    bus.attach(FakeDevice(vector=0o110, ipl=6, name="mid"))
    assert bus.grant_interrupt() == (0o104, 7)


def test_npr_outranks_br():
    bus = Unibus(physical_bits=18)
    bus.attach(FakeDevice(vector=0o100, ipl=7, name="intr"))
    dma = FakeDevice(dma=True, name="dma")
    bus.attach(dma)
    assert bus.arbitrate() == ("dma", dma)         # NPR beats BR7 (§6.1)


def test_npr_daisy_chain_first_slot_wins():
    bus = Unibus(physical_bits=18)
    a = FakeDevice(dma=True, name="A")
    b = FakeDevice(dma=True, name="B")
    bus.attach(a)
    bus.attach(b)
    assert bus.grant_dma() is a
    assert bus.arbitrate() == ("dma", a)


def test_no_requests_returns_none():
    bus = Unibus(physical_bits=18)
    bus.attach(FakeDevice())
    assert bus.grant_interrupt() is None
    assert bus.grant_dma() is None
    assert bus.arbitrate() is None


def test_word_cycles_one_per_word():
    assert Unibus.word_cycles(0o100) == 0o100     # 1 bus cycle per 16-bit word


def test_board_uses_unibus_on_1120():
    profile = PROFILES["11/20"]
    board = SystemBoard(profile, SystemClock())
    assert board.unibus is not None
    assert board.unibus.addr_mask == 0o177777      # 11/20: 16-bit physical
    dev = FakeDevice(vector=0o100, ipl=6)
    board.attach_device(0o177400, dev)
    assert board.devices == [dev]                  # slot order
    assert board.poll_interrupt() == (0o100, 6)    # CPU asks the bus
    assert board.service_dma() == 0                # no DMA pending


def test_dma_steals_cycles_at_word_granularity():
    """§5.5: an NPR transfer steals bus cycles mid-instruction; each word
    moved costs one cycle, charged to the current instruction's cost."""
    profile = PROFILES["11/20"]
    clock = SystemClock()
    board = SystemBoard(profile, clock)
    cpu = board.cpu
    cpu.state.R[Reg.PC] = 0o000100
    cpu.state.R[Reg.SP] = 0o017776
    board.ram.write_word(0o000100, 0o000240)       # NOP
    mover = WordMover(board, addr=0o000500, value=0o123456)
    board.attach_device(0o177400, mover)
    cycle_before = clock.cycle
    cpu.step()                                     # NOP + DMA steal + clock drain
    assert board.ram.read_word(0o000500) == 0o123456   # the word moved
    assert clock.cycle > cycle_before              # stolen cycle charged
    # cost = base (0o3) + instruction fetch word (1) + stolen word (1)
    assert clock.cycle - cycle_before == 0o3 + 1 + 1


def test_dma_and_interrupt_in_one_step():
    """NPR outranks BR (§6.1) but both are serviced at the instruction
    boundary: DMA steals the cycle, then the BR vectors if it outranks IPL."""
    profile = PROFILES["11/20"]
    board = SystemBoard(profile, SystemClock())
    cpu = board.cpu
    cpu.state.R[Reg.PC] = 0o000100
    cpu.state.R[Reg.SP] = 0o017776
    board.ram.write_word(0o000100, 0o000240)       # NOP
    board.ram.write_word(0o000100, 0o005000)       # vector 0100 -> PC 0o5000
    board.ram.write_word(0o000102, 0o000340)       #   new PSW: IPL 7
    board.attach_device(0o177400, WordMover(board, 0o000500, 0o000001))
    board.attach_device(0o177402, FakeDevice(vector=0o100, ipl=7))
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o005000         # interrupt vectored
    assert board.ram.read_word(0o000500) == 0o000001  # DMA word moved too
    assert board.devices[0].ticked == 1
