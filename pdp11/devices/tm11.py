"""
TU10 magnetic tape + TM11 controller (spec §7, P0; 11/70 default set).

Registers (octal, TM11 at 172520), window 12 bytes — SIMH pdp11_tm.c:
    MTS    172520  status          (read-only; dynamic drive bits merged in)
    MTC    172522  command/control (read/write)
    MTBRC  172524  byte/record count (read/write, two's complement)
    MTCMA  172526  memory address   (read/write; EMA bits extend to 18 bits)
    MTD    172530  data buffer      (read/write, byte)
    MTRD   172532  read lines       (read-only maintenance)

MTC bits (SIMH tm.c): <15> ERR, <14:13> density, <12> INIT, <11> parity,
<10:8> unit, <7> DONE, <6> IE, <5:4> EMA (memory extension), <3:1> function,
<0> GO. Functions: 0 UNLOAD, 1 READ, 2 WRITE, 3 WREOF (tape mark),
4 SPACEF, 5 SPACER, 6 WREXT, 7 REWIND. Read/write mask = density|parity|
unit|IE|EMA|function.

MTS bits (SIMH STA_*): <15> ILL, <14> EOF, <13> CRC, <12> PAR, <11> DLT,
<10> EOT, <9> RLE, <7> BAD, <6> NXM, <3> ONL, <5> BOT, <4> 7TK, <3> SDN,
<2> WLK, <1> REW, <0> TUR. ERR in MTC is set when any error bit is set.

Interrupt: vector 000224 at BR5, level-based while DONE & IE (§5.5).

Container: SIMH magtape .tap — 32-bit little-endian record header, raw
record bytes, trailing 32-bit length; a bare zero is a tape mark (EOF),
physical end of file is EOM (§16, hard requirement: byte-compatible with
SIMH-created .tap; disk_images/container.py TapImage — round-trip tested in
M8, device-level read/write here). Register names/addresses follow SIMH
pdp11_tm.c (MTS/MTC/MTBRC/MTCMA/MTD/MTRD); the M1 stub docstring's
MTTC/MTDS/MTER/MTCA/MTBA/MTCN/MTSR naming was approximate — recorded in
docs/acceptance-checklist.md.

Transfer model (§5.6, deterministic): byte-granular NPR cycle stealing —
the TM11 moves one BYTE per stolen cycle through the DMAHost bus face
(the Unibus supports byte transfers; abstract_bus.DMAHost carries
read_byte/write_byte for exactly this). A READ/WRITE first waits
RECORD_CYCLES for the record to come up, then streams min(record, MTBRC)
bytes; MTBRC/MTCMA are updated by the number of bytes moved and a record
longer than MTBRC sets RLE. SPACEF/SPACER skip MTBRC records one at a
time; WREOF writes a tape mark; REWIND repositions to BOT after
REWIND_CYCLES. Timing constants follow SIMH's tm_time/swait ratios scaled
to the repo quantum — flagged §22.4. v1 models unit 0 only; a non-zero
unit select gets ILL.

References: PDP-11 Peripherals Handbook, TM11 section; SIMH pdp11_tm.c (§21,
§22.4); EK-TM11-TD-001.
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice, DMAHost
from pdp11.disk_images.container import TapImage
from pdp11.system_clock import SystemClock

TM11_BASE = 0o172520
MTS = 0o00
MTC = 0o02
MTBRC = 0o04
MTCMA = 0o06
MTD = 0o10
MTRD = 0o12

# MTC bits
MTC_ERR = 0o100000
MTC_DEN = 0o060000
MTC_INIT = 0o010000
MTC_LPAR = 0o004000
MTC_UNIT = 0o000700
MTC_DONE = 0o000200
MTC_IE = 0o000100
MTC_EMA = 0o000030
MTC_FNC = 0o000016
MTC_GO = 0o000001
MTC_RW = MTC_DEN | MTC_LPAR | MTC_UNIT | MTC_IE | MTC_EMA | MTC_FNC

# functions (MTC <3:1>)
FNC_UNLOAD = 0o0
FNC_READ = 0o1
FNC_WRITE = 0o2
FNC_WREOF = 0o3
FNC_SPACEF = 0o4
FNC_SPACER = 0o5
FNC_WREXT = 0o6
FNC_REWIND = 0o7

# MTS status bits
STA_ILL = 0o100000
STA_EOF = 0o040000
STA_CRC = 0o020000
STA_PAR = 0o010000
STA_DLT = 0o004000
STA_EOT = 0o002000
STA_RLE = 0o001000
STA_BAD = 0o000400
STA_NXM = 0o000200
STA_ONL = 0o000100
STA_BOT = 0o000040
STA_7TK = 0o000020
STA_SDN = 0o000010
STA_WLK = 0o000004
STA_REW = 0o000002
STA_TUR = 0o000001
STA_ERRS = STA_ILL | STA_EOF | STA_CRC | STA_PAR | STA_DLT | STA_EOT \
    | STA_RLE | STA_BAD | STA_NXM

VECTOR = 0o000224
BR_LEVEL = 5

# timing in bus cycles (§5.6; SIMH tm_time=10 record latency, rewind ~2 s —
# scaled to the repo quantum, flagged §22.4)
RECORD_CYCLES = 0o2000          # 1024 cycles record latency
REWIND_CYCLES = 0o7464100       # 2,000,000 cycles ≈ 2 s tape rewind

# transfer states
IDLE, READ_STREAM, WRITE_STREAM = range(3)

_log = logging.getLogger(__name__)


class TM11(BusDevice):
    """TM11 controller + TU10 drive: register file plus the scheduled
    tape-motion state machine; data streams one byte per stolen NPR cycle
    through the DMAHost face (§5.5/§5.6)."""

    window_size = 0o14          # six word registers
    name = "TM11"

    def __init__(self, clock: SystemClock, bus: DMAHost,
                 tape: TapImage | None = None, base: int = TM11_BASE,
                 writable: bool = True) -> None:
        self._clock = clock
        self._bus = bus
        self.base = base
        self.tape = tape
        self.writable = writable
        self.mts = 0
        self.mtc = MTC_DONE
        self.mtbrc = 0
        self.mtcma = 0
        self.mtd = 0
        self.mtrd = 0
        self._state = IDLE
        self._buf = bytearray()
        self._buf_pos = 0
        self._remaining = 0         # bytes left to stream
        self._xma = 0               # extended memory address (18-bit)
        self._space_left = 0        # records left to space
        self._space_rev = False
        self.reset()

    # -- lifecycle -------------------------------------------------------------
    def reset(self) -> None:
        """INIT: registers to reset values (DONE set), transfer aborted."""
        self.mts = 0
        self.mtc = MTC_DONE
        self.mtbrc = 0
        self.mtcma = 0
        self.mtd = 0
        self._state = IDLE
        self._buf = bytearray()
        self._buf_pos = 0
        self._remaining = 0
        self._space_left = 0
        self._update_status()

    def _update_status(self) -> None:
        """Merge the dynamic drive bits into MTS and set/clear MTC ERR
        (SIMH tm_updcsta: STA_DYN bits come from the drive state)."""
        dyn_mask = STA_EOF | STA_EOT | STA_ONL | STA_BOT | STA_WLK | STA_REW | STA_TUR
        dyn = 0
        if self.tape is not None:
            dyn |= STA_ONL | STA_TUR
            if self.tape.bot:
                dyn |= STA_BOT
            if not self.writable:
                dyn |= STA_WLK
        if self._state != IDLE:
            dyn &= ~STA_TUR
        # EOF/EOT/REW persist from the last operation until the next GO
        dyn |= self.mts & (STA_EOF | STA_EOT | STA_REW)
        self.mts = (self.mts & ~dyn_mask) | dyn
        if self.mts & STA_ERRS:
            self.mtc |= MTC_ERR
        else:
            self.mtc &= ~MTC_ERR

    # -- register window ---------------------------------------------------------
    def read_word(self, offset: int) -> int:
        reg = offset & 0o177776
        if reg == MTS:
            self._update_status()
            return self.mts & 0o177777
        if reg == MTC:
            self._update_status()
            return self.mtc & 0o177777
        if reg == MTBRC:
            return self.mtbrc & 0o177777
        if reg == MTCMA:
            return self.mtcma & 0o177777
        if reg == MTD:
            return self.mtd & 0o377
        if reg == MTRD:
            return self.mtrd & 0o177777
        return 0

    def write_word(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        v = value & 0o177777
        if reg == MTS:
            return                              # status read-only
        if reg == MTC:
            if not (self.mtc & MTC_DONE) and not (v & MTC_INIT):
                self.mts |= STA_ILL             # command while busy
                self._done()
                return
            if v & MTC_INIT:
                self.reset()
                return
            self.mtc = (self.mtc & ~MTC_RW) | (v & MTC_RW)
            if v & MTC_GO:
                self._go((v & MTC_FNC) >> 1)
            self._update_status()
            return
        if reg == MTBRC:
            self.mtbrc = v
        elif reg == MTCMA:
            self.mtcma = v
        elif reg == MTD:
            self.mtd = v & 0o377
        # MTRD read-only

    def read_byte(self, offset: int) -> int:
        if offset & 0o177776 == MTD:
            return self.mtd & 0o377
        word = self.read_word(offset & 0o177776)
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        if reg == MTD:
            self.mtd = value & 0o377
            return
        cur = self.read_word(reg)
        if offset % 2 == 0:
            self.write_word(reg, (cur & 0o177400) | (value & 0o377))
        else:
            self.write_word(reg, (cur & 0o000377) | ((value & 0o377) << 8))

    # -- command dispatch ----------------------------------------------------------
    def _go(self, fct: int) -> None:
        """GO strobe: validate and start the function (SIMH tm_go)."""
        unit = (self.mtc & MTC_UNIT) >> 8
        self.mtc &= ~MTC_DONE
        self.mts &= ~STA_ERRS
        if self.tape is None or unit != 0:
            self.mts |= STA_ILL
            self._done()
            return
        if fct in (FNC_WRITE, FNC_WREOF, FNC_WREXT) and not self.writable:
            self.mts |= STA_ILL
            self._done()
            return
        self._xma = (((self.mtc & MTC_EMA) << 12) | self.mtcma) & 0o777777
        if fct == FNC_UNLOAD:
            self.tape = None
            self._done()
        elif fct == FNC_REWIND:
            self.mts |= STA_REW
            self._clock.schedule(REWIND_CYCLES, self._rewind_done)
        elif fct in (FNC_READ, FNC_WRITE, FNC_WREXT):
            self._clock.schedule(RECORD_CYCLES, self._record_ready)
        elif fct == FNC_WREOF:
            self._clock.schedule(RECORD_CYCLES, self._write_eof)
        elif fct in (FNC_SPACEF, FNC_SPACER):
            self._space_left = (0o200000 - self.mtbrc) & 0o177777
            if self._space_left == 0:
                self._space_left = 0o200000
            self._space_rev = fct == FNC_SPACER
            self._clock.schedule(RECORD_CYCLES, self._space_step)
        else:
            self._done()
        self._update_status()

    def _record_ready(self) -> None:
        """READ/WRITE/WREXT: the record is up — set up byte streaming."""
        fct = (self.mtc & MTC_FNC) >> 1
        if fct in (FNC_WRITE, FNC_WREXT):
            n = (0o200000 - self.mtbrc) & 0o177777
            self._remaining = n if n else 0o200000
            self._buf = bytearray()
            self._state = WRITE_STREAM
            return                              # dma_request() now true
        # READ: fetch the record from the tape
        assert self.tape is not None
        rec = self.tape.read_record()
        if rec == -1:                           # EOM
            self.mts |= STA_BAD | STA_EOT
            self._done()
            return
        if rec is None:                         # tape mark
            self.mts |= STA_EOF
            self._done()
            return
        cbc = (0o200000 - self.mtbrc) & 0o177777
        if cbc == 0:
            cbc = 0o200000
        n = min(len(rec), cbc)
        if len(rec) > cbc:
            self.mts |= STA_RLE
        self._buf = bytearray(rec[:n])
        self._buf_pos = 0
        self._remaining = n
        self._state = READ_STREAM

    def _write_eof(self) -> None:
        """WREOF: write a tape mark at the current position."""
        assert self.tape is not None
        self.tape.write_mark()
        self._done()

    def _rewind_done(self) -> None:
        """REWIND complete: back at BOT, online, DONE."""
        if self.tape is not None:
            self.tape.rewind()
        self.mts = (self.mts & ~STA_REW) | STA_ONL | STA_BOT
        if not self.writable and self.tape is not None:
            self.mts |= STA_WLK
        self._done()

    def _space_step(self) -> None:
        """SPACEF/SPACER: skip one record per scheduled step; stop at a
        tape mark (EOF), BOT, or EOM (SIMH tm_svc space loop)."""
        assert self.tape is not None
        if self._space_rev:
            rec = self.tape.space_back()
        else:
            rec = self.tape.read_record()
        self.mtbrc = (self.mtbrc + 1) & 0o177777
        self._space_left -= 1
        if rec == -1:                           # EOM / BOT
            if self._space_rev:
                self.mts |= STA_BOT
            else:
                self.mts |= STA_EOT | STA_BAD
            self._done()
            return
        if rec is None:                         # tape mark
            self.mts |= STA_EOF
            self._done()
            return
        if self._space_left > 0:
            self._clock.schedule(RECORD_CYCLES, self._space_step)
        else:
            self._done()

    def _finish_stream(self) -> None:
        """READ/WRITE stream complete: update MTBRC/MTCMA by the bytes
        moved, commit writes to the container, set DONE."""
        moved = len(self._buf)
        if self._state == WRITE_STREAM:
            assert self.tape is not None
            self.tape.write_record(bytes(self._buf))
        self.mtbrc = (self.mtbrc + moved) & 0o177777
        self.mtcma = self._xma & 0o177777
        self.mtc = (self.mtc & ~MTC_EMA) | ((self._xma >> 12) & MTC_EMA)
        self._state = IDLE
        self._done()

    def _done(self) -> None:
        """Command complete: DONE set; BR5/224 asserts while DONE & IE."""
        self._state = IDLE
        self.mtc |= MTC_DONE
        self._update_status()

    # -- NPR byte cycle stealing (§5.5/§6.1) -----------------------------------------
    def dma_request(self) -> bool:
        """Assert NPR while a READ/WRITE stream is active."""
        return self._state in (READ_STREAM, WRITE_STREAM)

    def tick(self) -> None:
        """Move ONE byte through the bus (one stolen cycle): reads push
        tape -> memory, writes pull memory -> tape."""
        if self._state == READ_STREAM:
            self._bus.write_byte(self._xma, self._buf[self._buf_pos])
            self._buf_pos += 1
        elif self._state == WRITE_STREAM:
            self._buf.append(self._bus.read_byte(self._xma))
        else:
            return
        self._xma = (self._xma + 1) & 0o777777
        self._remaining -= 1
        if self._remaining <= 0:
            self._finish_stream()

    # -- interrupts (§6.1) ------------------------------------------------------------
    def interrupt_pending(self) -> tuple[int, int] | None:
        if (self.mtc & MTC_DONE) and (self.mtc & MTC_IE):
            return (VECTOR, BR_LEVEL)
        return None

    # -- snapshot contract (§15) ------------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {"base": self.base, "mts": self.mts, "mtc": self.mtc,
                "mtbrc": self.mtbrc, "mtcma": self.mtcma, "mtd": self.mtd,
                "mtrd": self.mtrd, "state": self._state,
                "buf": bytes(self._buf).hex(), "buf_pos": self._buf_pos,
                "remaining": self._remaining, "xma": self._xma,
                "space_left": self._space_left, "space_rev": self._space_rev,
                "writable": self.writable,
                "tape_path": self.tape.path if self.tape else None,
                "tape_pos": self.tape.pos if self.tape else 0}

    def deserialize(self, state: dict[str, Any]) -> None:
        keys = ("base", "mts", "mtc", "mtbrc", "mtcma", "mtd", "mtrd", "state",
                "buf", "buf_pos", "remaining", "xma", "space_left", "space_rev",
                "writable", "tape_path", "tape_pos")
        for key in keys:
            if key not in state:
                raise ValueError(f"TM11 snapshot missing {key!r}")
        self.base = state["base"]
        self.mts = state["mts"]
        self.mtc = state["mtc"]
        self.mtbrc = state["mtbrc"]
        self.mtcma = state["mtcma"]
        self.mtd = state["mtd"]
        self.mtrd = state["mtrd"]
        self._state = state["state"]
        self._buf = bytearray(bytes.fromhex(state["buf"]))
        self._buf_pos = state["buf_pos"]
        self._remaining = state["remaining"]
        self._xma = state["xma"]
        self._space_left = state["space_left"]
        self._space_rev = state["space_rev"]
        self.writable = state["writable"]
        # the container itself is re-attached by the host (main.py); restore
        # only the cursor position
        if self.tape is not None:
            self.tape.pos = state["tape_pos"]
