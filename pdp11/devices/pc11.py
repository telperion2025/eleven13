"""
PC11 paper tape reader/punch (spec §7, P0 on the 11/20 default set).

Registers (octal, PC11 at 177550), window 8 bytes — SIMH pdp11_pt.c:
    PRS  177550  reader control/status   (<15> ERR, <11> BUSY, <7> DONE,
                                           <6> IE, <0> GO; only IE read/write)
    PRB  177552  reader buffer           (read clears DONE + interrupt)
    PPS  177554  punch control/status    (<15> ERR, <7> DONE, <6> IE)
    PPB  177556  punch buffer            (write starts a punch)

Interrupts: reader vector 000070, punch vector 000074, both BR4,
level-based while DONE & IE (§5.5).

Reader model (SIMH ptr_svc): a GO strobe clears DONE, sets BUSY, and
schedules the next frame; when the frame lands the byte goes to PRB and
DONE is set (ERR set instead at end-of-tape or with no tape attached).
Reading PRB returns the byte and clears DONE. Deterministic timing (§5.6):
READER_CYCLES per frame (PC05 ≈ 1000 cps ≈ 1 ms ≈ 1000 cycles at the repo
quantum — SIMH SERIAL_IN_WAIT, flagged §22.4).

Punch: v1 is read-only per §16 — PPB writes are accepted and the bytes
accumulate in an in-memory punch buffer (exposed via serialize() and
punch_tape()); the .pt container itself is never modified. PPS reads DONE
(ready); writing PPB clears DONE, schedules PUNCH_CYCLES later, then sets
DONE again.

Container: SIMH .pt format = a raw byte stream (§16, disk_images/
container.py PtImage).

References: PDP-11 Peripherals Handbook, PC11 section (§21); SIMH
pdp11_pt.c (§22.4).
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice
from pdp11.disk_images.container import PtImage
from pdp11.system_clock import SystemClock

PC11_BASE = 0o177550
PRS = 0o00   # reader status
PRB = 0o02   # reader buffer
PPS = 0o04   # punch status
PPB = 0o06   # punch buffer

# CSR bits
ERR = 0o100000
BUSY = 0o010000     # reader only
DONE = 0o000200
IE = 0o000100
GO = 0o000001
CSR_RW = IE

VECTOR_RX = 0o000070
VECTOR_TX = 0o000074
BR_LEVEL = 4

# per-frame delay in bus cycles (§5.6; PC05 ~1000 cps -> ~1 ms -> ~1000
# cycles at the repo quantum — flagged §22.4)
READER_CYCLES = 0o1750      # 1000
PUNCH_CYCLES = 0o1750

_log = logging.getLogger(__name__)


class PC11(BusDevice):
    """PC11 controller: reader (PRS/PRB) + punch (PPS/PPB) register file,
    scheduled frame service, level-based BR4 interrupts."""

    window_size = 0o10          # four word registers
    name = "PC11"

    def __init__(self, clock: SystemClock, tape: PtImage | None = None,
                 base: int = PC11_BASE) -> None:
        self._clock = clock
        self.base = base
        self.tape = tape
        self.prs = 0
        self.prb = 0
        self.pps = DONE
        self.ppb = 0
        self._pos = 0               # reader cursor into tape.data
        self._punch: bytearray = bytearray()
        self.reset()

    # -- lifecycle -------------------------------------------------------------
    def reset(self) -> None:
        """Bus init: reader idle (ERR if no tape attached), punch ready
        (DONE, ERR if no output medium — v1 punch is in-memory, so ready)."""
        self.prs = 0 if self.tape is not None else ERR
        self.prb = 0
        self.pps = DONE
        self._pos = 0

    @property
    def attached(self) -> bool:
        return self.tape is not None

    def punch_tape(self) -> bytes:
        """The bytes punched so far (v1: in-memory only, §16 read-only)."""
        return bytes(self._punch)

    # -- register window ---------------------------------------------------------
    def read_word(self, offset: int) -> int:
        reg = offset & 0o177776
        if reg == PRS:
            return self.prs & (ERR | BUSY | DONE | IE)
        if reg == PRB:
            self.prs &= ~DONE               # reading PRB clears DONE + int
            return self.prb & 0o377
        if reg == PPS:
            return self.pps & (ERR | DONE | IE)
        if reg == PPB:
            return self.ppb & 0o377
        return 0

    def write_word(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        v = value & 0o177777
        if reg == PRS:
            if v & GO:
                self.prs = (self.prs & ~DONE) | BUSY
                self._clock.schedule(READER_CYCLES, self._read_frame)
            self.prs = (self.prs & ~CSR_RW) | (v & CSR_RW)
        elif reg == PRB:
            pass                            # reader buffer is read-only
        elif reg == PPS:
            self.pps = (self.pps & ~CSR_RW) | (v & CSR_RW)
        elif reg == PPB:
            self.ppb = v & 0o377
            self.pps &= ~DONE
            self._clock.schedule(PUNCH_CYCLES, self._punch_frame)

    def read_byte(self, offset: int) -> int:
        if offset & 0o177776 == PRB or offset & 0o177776 == PPB:
            return self.read_word(offset & 0o177776) & 0o377
        word = self.read_word(offset & 0o177776)
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        if reg in (PRB, PPB):
            self.write_word(reg, value & 0o377)
            return
        cur = self.read_word(reg)
        if offset % 2 == 0:
            self.write_word(reg, (cur & 0o177400) | (value & 0o377))
        else:
            self.write_word(reg, (cur & 0o000377) | ((value & 0o377) << 8))

    # -- scheduled frame service (§5.6) --------------------------------------------
    def _read_frame(self) -> None:
        """One reader frame: next tape byte into PRB with DONE, or ERR at
        end-of-tape / no tape (SIMH ptr_svc)."""
        self.prs &= ~BUSY
        if self.tape is None or self._pos >= len(self.tape.data):
            self.prs |= ERR | DONE          # out of tape
            _log.info("PC11 reader out of tape")
            return
        self.prb = self.tape.data[self._pos] & 0o377
        self._pos += 1
        self.prs = (self.prs | DONE) & ~ERR

    def _punch_frame(self) -> None:
        """One punch frame: append PPB to the in-memory punch tape (§16
        read-only: the container is not written)."""
        self._punch.append(self.ppb & 0o377)
        self.pps |= DONE

    # -- interrupts (§6.1) -----------------------------------------------------------
    def interrupt_pending(self) -> tuple[int, int] | None:
        if (self.prs & DONE) and (self.prs & IE):
            return (VECTOR_RX, BR_LEVEL)
        if (self.pps & DONE) and (self.pps & IE):
            return (VECTOR_TX, BR_LEVEL)
        return None

    # -- snapshot contract (§15) ------------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {"base": self.base, "prs": self.prs, "prb": self.prb,
                "pps": self.pps, "ppb": self.ppb, "pos": self._pos,
                "punch": bytes(self._punch).hex(),
                "image_path": self.tape.path if self.tape else None}

    def deserialize(self, state: dict[str, Any]) -> None:
        for key in ("base", "prs", "prb", "pps", "ppb", "pos", "punch", "image_path"):
            if key not in state:
                raise ValueError(f"PC11 snapshot missing {key!r}")
        self.base = state["base"]
        self.prs = state["prs"]
        self.prb = state["prb"]
        self.pps = state["pps"]
        self.ppb = state["ppb"]
        self._pos = state["pos"]
        self._punch = bytearray(bytes.fromhex(state["punch"]))
