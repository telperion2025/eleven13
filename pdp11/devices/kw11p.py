"""
KW11-P programmable real-time clock (spec §7, P2; 11/70, 11/94).

Registers (octal, KW11-P at 172540) — SIMH pdp11_pclk.c:
    CSR  172540  control/status   (read mask 0o100377, write mask 0o000137)
    CSB  172542  count set buffer (load the interval here)
    CNT  172544  up/down counter   (read-only)
    172546  not used

CSR bits (SIMH pclk_csr_bits): <0> GO (run), <2:1> rate select
(00 = 100 kHz, 01 = 10 kHz, 10 = line frequency, 11 = external/10 Hz),
<3> MODE (1 = repeat, 0 = single shot), <4> UPDN (1 = count up, 0 = down),
<5> FIX (single tick while stopped), <6> IE, <7> DONE, <15> ERR.
Reading CSR clears DONE and ERR and drops the interrupt. Writing CSR with
GO=0 stops the counter (saving CNT); a 0->1 transition of GO, or a rate
change, reloads CNT from CSB and starts. Writing CSB reloads CNT (and
reschedules when running) and clears DONE/ERR.

Interrupt: vector 000104 at BR6, level-based while DONE & IE (§5.5) — the
same vector/level as the KW11-L, matching SIMH.

Deterministic timing (§5.6): the per-count delay is the rate table scaled to
the repo's bus-cycle quantum (the KW11-L line tick is 16_667 cycles, so
line rate = 16_667 cycles/count; 100 kHz = 10, 10 kHz = 547, 10 Hz =
166_670). SIMH's xtim[] microsecond constants are the source of the ratios
— flagged §22.4. While running, CNT is computed from the scheduled due
cycle, so snapshots restore exactly (§15).

References: DEC-11-HPWB-D KW11-P manual; SIMH pdp11_pclk.c (§22.4).
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice
from pdp11.system_clock import SystemClock

KW11P_BASE = 0o172540
CSR = 0o00
CSB = 0o02
CNT = 0o04

# CSR bits
GO = 0o000001
RATE_MASK = 0o000006
RATE_SHIFT = 1
MODE = 0o000010
UPDN = 0o000020
FIX = 0o000040
IE = 0o000100
DONE = 0o000200
ERR = 0o100000
CSR_READ_MASK = 0o100377
CSR_WRITE_MASK = 0o000137

VECTOR = 0o000104
BR_LEVEL = 6

# per-count delay in bus cycles per rate select (§5.6 quantum; SIMH xtim
# ratios 10us / 100us / 16667us / 100000us per count scaled to the repo
# quantum where the 60 Hz line tick is 16_667 cycles — flagged §22.4)
RATE_CYCLES = (10, 547, 16667, 166670)

_log = logging.getLogger(__name__)


class KW11P(BusDevice):
    """Programmable clock: CSR/CSB/CNT register file plus a scheduled
    up/down counter that sets DONE (and interrupts) on zero."""

    window_size = 0o06          # three word registers (172546 unused)
    name = "KW11P"

    def __init__(self, clock: SystemClock, base: int = KW11P_BASE) -> None:
        self._clock = clock
        self.base = base
        self.csr = 0
        self.csb = 0
        self._ctr = 0               # counter value while stopped
        self._due = 0               # absolute due cycle while running
        self._running = False
        self.reset()

    # -- lifecycle -------------------------------------------------------------
    def reset(self) -> None:
        """Bus init: all registers clear, clock stopped (SIMH: the device is
        disabled by default)."""
        self.csr = 0
        self.csb = 0
        self._ctr = 0
        self._due = 0
        self._running = False

    def _counts(self) -> int:
        """Counts to wait: UPDN counts up to 0 (delay = 65536 - CSB),
        down counts down to 0 (delay = CSB); CSB=0 means 65536."""
        v = self.csb & 0o177777
        d = (0o200000 - v) & 0o177777 if (self.csr & UPDN) else v
        return d if d else 0o200000

    def _delay(self) -> int:
        rate = (self.csr & RATE_MASK) >> RATE_SHIFT
        return self._counts() * RATE_CYCLES[rate]

    def _start(self) -> None:
        """GO 0->1 or rate change: reload CNT from CSB and schedule the
        zero-crossing event (deterministic, §5.6)."""
        self._running = True
        delay = self._delay()
        self._due = self._clock.cycle + delay
        self._clock.schedule(delay, self._tick)

    def _tick(self) -> None:
        """Counter reached zero: set DONE (ERR if DONE was already set);
        repeat mode reloads from CSB and reschedules, single-shot stops."""
        if self.csr & DONE:
            self.csr |= ERR | DONE
        else:
            self.csr |= DONE
        _log.debug("KW11-P zero crossing (csr=%o)", self.csr)
        if self.csr & MODE:
            delay = self._delay()
            self._due = self._clock.cycle + delay
            self._clock.schedule(delay, self._tick)
        else:
            self.csb = 0
            self.csr &= ~GO
            self._running = False

    # -- register window ---------------------------------------------------------
    def read_word(self, offset: int) -> int:
        reg = offset & 0o177776
        if reg == CSR:
            value = self.csr & CSR_READ_MASK
            self.csr &= ~(ERR | DONE)          # read clears DONE/ERR + interrupt
            return value
        if reg == CSB:
            return 0                            # buffer reads back 0 (SIMH)
        if reg == CNT:
            if not self._running:
                return self._ctr & 0o177777
            rate = (self.csr & RATE_MASK) >> RATE_SHIFT
            per = RATE_CYCLES[rate]
            counts_left = (self._due - self._clock.cycle) // per if per else 0
            if self.csr & UPDN:
                return (0o200000 - counts_left) & 0o177777
            return counts_left & 0o177777
        return 0

    def write_word(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        v = value & 0o177777
        if reg == CSR:
            old = self.csr
            self.csr = v & CSR_WRITE_MASK
            if not (self.csr & GO):
                self._running = False
                if v & FIX:                       # single tick while stopped
                    self._ctr = ((self._ctr + 1) if (self.csr & UPDN)
                                 else (self._ctr - 1)) & 0o177777
                    if self._ctr == 0:
                        self._tick()
            elif not (old & GO) or ((self.csr & RATE_MASK) != (old & RATE_MASK)):
                self._start()
        elif reg == CSB:
            self.csb = v
            if self._running:
                self._start()                     # reschedule with new interval
            else:
                self._ctr = v                     # SIMH pclk_set_ctr: load while stopped
            self.csr &= ~(ERR | DONE)
        # CNT read-only

    def read_byte(self, offset: int) -> int:
        word = self.read_word(offset & 0o177776)
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        # SIMH: byte writes don't work on the KW11-P — only even-address
        # (low-byte) writes land.
        if offset % 2 == 0:
            reg = offset & 0o177776
            if reg == CSR:
                self.write_word(CSR, value & 0o377)
            elif reg == CSB:
                self.write_word(CSB, value & 0o377)

    # -- interrupts (§6.1: polled by the bus arbiter) ----------------------------
    def interrupt_pending(self) -> tuple[int, int] | None:
        if (self.csr & DONE) and (self.csr & IE):
            return (VECTOR, BR_LEVEL)
        return None

    # -- snapshot contract (§15) ---------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {"base": self.base, "csr": self.csr, "csb": self.csb,
                "ctr": self._ctr, "due": self._due, "running": self._running}

    def deserialize(self, state: dict[str, Any]) -> None:
        for key in ("base", "csr", "csb", "ctr", "due", "running"):
            if key not in state:
                raise ValueError(f"KW11P snapshot missing {key!r}")
        self.base = state["base"]
        self.csr = state["csr"]
        self.csb = state["csb"]
        self._ctr = state["ctr"]
        self._due = state["due"]
        self._running = state["running"]
