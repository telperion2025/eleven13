"""
KW11-L line-frequency clock (spec §7, P0, ALL four machines — RT-11/RSTS
depend on it, §5.6).

Register (octal): LKS 177546 — bit 7 = DONE, bit 6 = IE; reading LKS
clears DONE. Interrupts at vector 000104 on BR6.

Deterministic timing (§5.6): the clock schedules itself every 16_667 bus
cycles (60 Hz at the 150 ns quantum) via SystemClock.schedule — never a
wall-clock read. On each tick DONE is set and, if IE, the interrupt fires.

References: PDP-11 Peripherals Handbook, KW11-L section (§21).
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice
from pdp11.system_clock import SystemClock

LKS = 0o177546        # line clock status register
DONE = 0o000200       # bit 7
IE = 0o000100         # bit 6
VECTOR = 0o000104     # KW11-L interrupt vector
BR_LEVEL = 6          # BR6
TICK_CYCLES = 16667   # 60 Hz at 150 ns/bus-cycle (§5.6)

_log = logging.getLogger(__name__)


class KW11L(BusDevice):
    """Line-frequency clock: self-scheduling tick + LKS register."""

    window_size = 2
    name = "KW11L"

    def __init__(self, clock: SystemClock, base: int = LKS) -> None:
        self._clock = clock
        self.base = base
        self.done = False
        self.ie = False
        self.ticks = 0
        self.reset()

    def reset(self) -> None:
        """Bus init: DONE clear, IE clear; start the 60 Hz tick schedule."""
        self.done = False
        self.ie = False
        self._clock.schedule(TICK_CYCLES, self._tick)

    def _tick(self) -> None:
        """One line-frequency tick: set DONE; reschedule (§5.6)."""
        self.ticks += 1
        self.done = True
        _log.debug("KW11-L tick #%d", self.ticks)
        self._clock.schedule(TICK_CYCLES, self._tick)

    # -- register window ------------------------------------------------------
    def read_word(self, offset: int) -> int:
        word = (DONE if self.done else 0) | (IE if self.ie else 0)
        if offset == 0:
            self.done = False                 # reading LKS clears DONE
        return word & 0o177777

    def write_word(self, offset: int, value: int) -> None:
        if offset == 0:
            self.ie = bool(value & IE)        # only IE is writable

    def read_byte(self, offset: int) -> int:
        word = (DONE if self.done else 0) | (IE if self.ie else 0)
        if offset == 0:
            self.done = False
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        if offset % 2 == 0:
            self.ie = bool(value & IE)

    def interrupt_pending(self) -> tuple[int, int] | None:
        if self.done and self.ie:
            return (VECTOR, BR_LEVEL)
        return None

    # -- snapshot contract (§15) ----------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {"done": self.done, "ie": self.ie, "ticks": self.ticks, "base": self.base}

    def deserialize(self, state: dict[str, Any]) -> None:
        for key in ("done", "ie", "ticks", "base"):
            if key not in state:
                raise ValueError(f"KW11L snapshot missing {key!r}")
        self.done = state["done"]
        self.ie = state["ie"]
        self.ticks = state["ticks"]
        self.base = state["base"]
