"""
RL01/RL02 disk + RL11 controller (spec §7, P1; 11/03, 11/70, 11/94).

Registers (octal, RL11 at 174400), window 10 bytes — SIMH pdp11_rl.c:
    RLCS   174400  control/status (read/write mask 0o001776)
    RLBA   174402  bus address
    RLDA   174404  disk address (cyl <15:7>, head <6>, sector <5:0>)
    RLMP   174406  multipurpose: word count for data ops, status queue
    RLBAE  174410  bus address extension (RLV12; v1 keeps the MEX bits
                   in RLCS instead)

RLCS bits (SIMH): <0> DRDY (computed on read), <3:1> function
(0 NOP, 1 WCHK, 2 GSTA, 3 SEEK, 4 RHDR, 5 WRITE, 6 READ, 7 RNOHDR),
<5:4> MEX, <9:8> drive, <6> IE, <7> DONE, <11> INCOMP, <12> CRC,
<13> DLT, <14> NXM, <10> DRE, <15> ERR. A command starts when RLCS is
written with the DONE bit CLEAR (there is no GO bit — SIMH rl_wr).

RLDA: cylinder <15:7>, head <6>, sector <5:0>; logical sector =
(cyl*2 + head) * 40 + sector (SIMH GET_DA). Geometry: 256-byte sectors,
40 per surface, 2 surfaces, RL01 = 256 cylinders, RL02 = 512 (§16,
disk_images/container.py).

Interrupt: vector 000160 at BR5, level-based while DONE & IE (§5.5).

Transfer model (§5.6, deterministic): word-at-a-time NPR cycle stealing
like the RK11 — one 256-byte sector buffered, DMA word per tick() through
the DMAHost face; the word count is two's-complement in RLMP (0 = 65536
words). The DA auto-increments sector -> head within the cylinder (the
RL11 never crosses a cylinder mid-transfer). SEEK is RELATIVE: the RLDA
cylinder field is the offset, bit <2> = outward, bit <4> = head select;
completion is scheduled after |Δcyl| * SEEK_PER_CYL_CYCLES. GSTA returns
the drive state in RLMP (RLDA GS bit must be set). WCHK compares memory
against the pack (CRC error on mismatch). RHDR in v1 returns the DA word
in RLMP and completes (header queue simplified — flagged §22.4). v1 models
drive unit 0 only.

Bit-level corners the spec doesn't pin (full RLDS error set, exact seek
timing, header CRC) follow SIMH pdp11_rl.c and are FLAGGED §22.4; see
docs/acceptance-checklist.md.

References: PDP-11 Peripherals Handbook, RL11 section; EK-0RL11-TD-001;
SIMH pdp11_rl.c (§21, §22.4).
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice, DMAHost
from pdp11.disk_images.container import (
    RL_NUMSC,
    RL_NUMSF,
    RL_SECTOR,
    RawDiskImage,
    rl_sector_address,
)
from pdp11.system_clock import SystemClock

RL11_BASE = 0o174400
RLCS = 0o00
RLBA = 0o02
RLDA = 0o04
RLMP = 0o06
RLBAE = 0o10

# RLCS bits
RLCS_DRDY = 0o000001
RLCS_FNC_MASK = 0o000016
RLCS_FNC_SHIFT = 1
RLCS_MEX = 0o000030
RLCS_DRIVE_MASK = 0o000700
RLCS_DRIVE_SHIFT = 8
RLCS_IE = 0o000100
RLCS_DONE = 0o000200
RLCS_INCMP = 0o002000
RLCS_CRC = 0o004000
RLCS_DLT = 0o010000
RLCS_NXM = 0o020000
RLCS_DRE = 0o040000
RLCS_ERR = 0o100000
RLCS_RW = 0o001776
RLCS_ALLERR = RLCS_ERR | RLCS_DRE | RLCS_NXM | RLCS_DLT | RLCS_CRC | RLCS_INCMP

# functions (RLCS <3:1>)
FNC_NOP = 0o0
FNC_WCHK = 0o1
FNC_GSTA = 0o2
FNC_SEEK = 0o3
FNC_RHDR = 0o4
FNC_WRITE = 0o5
FNC_READ = 0o6
FNC_RNOHDR = 0o7

# RLDA bits
RLDA_GS = 0o000002
RLDA_SK_DIR = 0o000004
RLDA_GS_CLR = 0o000010
RLDA_SK_HD = 0o000020
RLDA_SECT_MASK = 0o000077
RLDA_HD = 0o000100
RLDA_TRACK_MASK = 0o177700
RLDA_TRACK_SHIFT = 6
RLDA_CYL_MASK = 0o177600
RLDA_CYL_SHIFT = 7

# RLDS drive-state bits returned by GSTA (SIMH RLDS_*)
RLDS_BHO = 0o000010
RLDS_HDO = 0o000020
RLDS_LOCK = 0o000005       # state field value "lock on"
RLDS_RL02 = 0o000200
RLDS_DSE = 0o000400
RLDS_WLK = 0o020000

VECTOR = 0o000160
BR_LEVEL = 5

WORDS_PER_SECTOR = RL_SECTOR // 2          # 128 words per 256-byte sector

# seek timing (§5.6): RL01 full-stroke ~100 ms; 256 cycles/cylinder at the
# repo quantum ≈ 65 ms full stroke — SIMH rl_swait=10 scaled, flagged §22.4
SEEK_PER_CYL_CYCLES = 0o400                # 256

_log = logging.getLogger(__name__)


def decode_rlda(da: int) -> tuple[int, int, int]:
    """RLDA -> (cylinder, head, sector)."""
    return ((da & RLDA_CYL_MASK) >> RLDA_CYL_SHIFT,
            1 if da & RLDA_HD else 0,
            da & RLDA_SECT_MASK)


def next_rlda(da: int, max_cyl: int) -> int:
    """Auto-increment DA across sectors then heads, never leaving the
    cylinder (the RL11 constraint, SIMH rl.c)."""
    cyl, head, sect = decode_rlda(da)
    sect += 1
    if sect >= RL_NUMSC:
        sect = 0
        head = 1 - head
    return ((cyl << RLDA_CYL_SHIFT) | (head * RLDA_HD) | sect) & 0o177777


class RLDrive:
    """One RL01/RL02 drive unit behind the RL11: geometry, head position,
    sector I/O against the raw container (§16)."""

    def __init__(self, image: RawDiskImage | None = None) -> None:
        self.image = image
        self.cylinder = 0
        self.sectors_read = 0
        self.sectors_written = 0

    @property
    def loaded(self) -> bool:
        return self.image is not None

    @property
    def max_cyl(self) -> int:
        return self.image.sector_count // (RL_NUMSF * RL_NUMSC) if self.image else 0

    @property
    def is_rl02(self) -> bool:
        return self.image is not None and self.max_cyl > 256

    def check_address(self, da: int) -> tuple[int, int, int]:
        """Decode RLDA -> (cylinder, head, sector); ValueError if the
        cylinder is past the pack or the unit is not drive 0 (v1)."""
        cyl, head, sect = decode_rlda(da)
        if cyl >= self.max_cyl or sect >= RL_NUMSC:
            raise ValueError(f"RLDA out of range: cyl={cyl} head={head} sect={sect}")
        return cyl, head, sect

    def read_sector(self, da: int) -> bytes:
        cyl, head, sect = self.check_address(da)
        self.sectors_read += 1
        assert self.image is not None
        return self.image.read_sector(rl_sector_address(cyl, head, sect))

    def write_sector(self, da: int, payload: bytes) -> None:
        cyl, head, sect = self.check_address(da)
        self.sectors_written += 1
        assert self.image is not None
        self.image.write_sector(rl_sector_address(cyl, head, sect), payload)
        self.image.flush()


class RL11(BusDevice):
    """RL11 controller: register file (window 174400-174411) plus the
    tick()/scheduled-event transfer state machine (§7 design rule)."""

    window_size = 0o12          # five word registers
    name = "RL11"

    def __init__(self, clock: SystemClock, bus: DMAHost,
                 drive: RLDrive | None = None, base: int = RL11_BASE) -> None:
        self._clock = clock
        self._bus = bus
        self.base = base
        self.drive = drive if drive is not None else RLDrive()
        self.rlcs = RLCS_DONE
        self.rlba = 0
        self.rlda = 0
        self.rlmp = 0
        self._rlmp1 = 0
        self._rlmp2 = 0
        self.rlbae = 0
        self._active = False        # a data transfer holds the NPR line
        self._writing = False
        self._wchk = False
        self._buffer = bytearray(RL_SECTOR)
        self._buf_pos = 0
        self._remaining = 0
        self._seek_target = 0
        self.reset()

    # -- lifecycle -------------------------------------------------------------
    def reset(self) -> None:
        """Bus init: DONE set, registers clear, no transfer (the RL11 does
        NOT recalibrate on RESET — SIMH rl_reset)."""
        self.rlcs = RLCS_DONE
        self.rlba = 0
        self.rlda = 0
        self.rlmp = 0
        self._rlmp1 = 0
        self._rlmp2 = 0
        self._active = False
        self._writing = False
        self._wchk = False
        self._buffer = bytearray(RL_SECTOR)
        self._buf_pos = 0
        self._remaining = 0

    def _drdy(self) -> bool:
        """DRDY: the selected drive is ready (loaded, not seeking/active)."""
        return self.drive.loaded and not self._active

    # -- register window ---------------------------------------------------------
    def read_word(self, offset: int) -> int:
        reg = offset & 0o177776
        if reg == RLCS:
            self.rlcs = (self.rlcs & ~RLCS_MEX) | ((self.rlbae & 0o3) << 4)
            if self._drdy():
                self.rlcs |= RLCS_DRDY
            else:
                self.rlcs &= ~RLCS_DRDY
            if self.rlcs & RLCS_ALLERR:
                self.rlcs |= RLCS_ERR
            return self.rlcs & 0o177777
        if reg == RLBA:
            return self.rlba & 0o177777
        if reg == RLDA:
            return self.rlda & 0o177777
        if reg == RLMP:
            value = self.rlmp             # ripple the status queue
            self.rlmp = self._rlmp1
            self._rlmp1 = self._rlmp2
            return value & 0o177777
        if reg == RLBAE:
            return self.rlbae & 0o77
        return 0

    def write_word(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        v = value & 0o177777
        if reg == RLCS:
            self.rlbae = (self.rlbae & ~0o3) | ((v >> 4) & 0o3)
            self.rlcs = (self.rlcs & ~RLCS_RW) | (v & RLCS_RW)
            if v & RLCS_DONE:
                return                    # DONE set in the write: no command
            self.rlcs &= ~RLCS_ALLERR
            self._go((self.rlcs & RLCS_FNC_MASK) >> RLCS_FNC_SHIFT)
        elif reg == RLBA:
            self.rlba = v & 0o177776
        elif reg == RLDA:
            self.rlda = v
        elif reg == RLMP:
            self.rlmp = self._rlmp1 = self._rlmp2 = v
        elif reg == RLBAE:
            self.rlbae = v & 0o77
            self.rlcs = (self.rlcs & ~RLCS_MEX) | ((self.rlbae & 0o3) << 4)

    def read_byte(self, offset: int) -> int:
        word = self.read_word(offset & 0o177776)
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        cur = self.read_word(reg)
        if offset % 2 == 0:
            self.write_word(reg, (cur & 0o177400) | (value & 0o377))
        else:
            self.write_word(reg, (cur & 0o000377) | ((value & 0o377) << 8))

    # -- command dispatch ----------------------------------------------------------
    def _go(self, fct: int) -> None:
        """RLCS written with DONE clear: start the function (SIMH rl_wr)."""
        drive = (self.rlcs & RLCS_DRIVE_MASK) >> RLCS_DRIVE_SHIFT
        self.rlcs &= ~RLCS_DONE
        self._active = False
        if drive != 0 or not self.drive.loaded:
            self.rlcs |= RLCS_INCMP | RLCS_DRE
            self._complete()
            return
        if fct == FNC_NOP:
            self._complete()
        elif fct == FNC_SEEK:
            self._seek()
        elif fct == FNC_GSTA:
            self._get_status()
        elif fct == FNC_RHDR:
            self.rlmp = self.rlda & 0o177777   # v1: header = DA word (flagged §22.4)
            self._rlmp1 = 0
            self._rlmp2 = 0
            self._complete()
        else:                                   # WCHK / WRITE / READ / RNOHDR
            self._begin_transfer(fct)

    def _seek(self) -> None:
        """SEEK: relative move; schedule completion after |Δcyl| * per-cyl
        delay (SIMH rl_wr SEEK case)."""
        curr = self.drive.cylinder
        offs = (self.rlda & RLDA_CYL_MASK) >> RLDA_CYL_SHIFT
        if self.rlda & RLDA_SK_DIR:
            target = min(curr + offs, self.drive.max_cyl - 1)
        else:
            target = max(curr - offs, 0)
        self._seek_target = target
        dist = abs(target - curr) or 1
        self._clock.schedule(dist * SEEK_PER_CYL_CYCLES, self._seek_done)

    def _seek_done(self) -> None:
        self.drive.cylinder = self._seek_target
        self._complete()

    def _get_status(self) -> None:
        """GSTA: drive status into the RLMP queue (RLDA GS must be set)."""
        if not (self.rlda & RLDA_GS):
            self.rlcs |= RLCS_ERR | RLCS_INCMP
            self._complete()
            return
        if self.rlda & RLDA_GS_CLR:
            self.rlcs &= ~RLCS_ALLERR
        word = RLDS_LOCK | RLDS_BHO | RLDS_HDO
        if self.drive.is_rl02:
            word |= RLDS_RL02
        if not self.drive.loaded:
            word |= RLDS_DSE
        self.rlmp = self._rlmp1 = self._rlmp2 = word
        self._complete()

    def _begin_transfer(self, fct: int) -> None:
        """Data command: validate address, set up the two's-complement word
        count from RLMP, prime the sector buffer, assert NPR."""
        try:
            self.drive.check_address(self.rlda)
        except ValueError:
            self.rlcs |= RLCS_INCMP | RLCS_DRE
            self._complete()
            return
        self._writing = fct == FNC_WRITE
        self._wchk = fct == FNC_WCHK
        n = (0o200000 - self.rlmp) & 0o177777
        self._remaining = n if n else 0o200000
        self._buffer = bytearray(self.drive.read_sector(self.rlda))
        self._buf_pos = 0
        self._active = True

    def _complete(self) -> None:
        self.rlcs |= RLCS_DONE

    # -- NPR cycle stealing (§5.5/§6.1) ----------------------------------------------
    def dma_request(self) -> bool:
        return self._active

    def tick(self) -> None:
        """Move ONE word through the bus (one stolen cycle). At a sector
        boundary the buffer is committed (write) and RLDA auto-increments
        while words remain."""
        if not self._active:
            return
        i = self._buf_pos
        if self._wchk:
            w_mem = self._bus.read_word(self._xma())
            w_dsk = self._buffer[2 * i] | (self._buffer[2 * i + 1] << 8)
            if w_mem != w_dsk:
                self.rlcs |= RLCS_ERR | RLCS_CRC
        elif self._writing:
            w = self._bus.read_word(self._xma())
            self._buffer[2 * i] = w & 0o377
            self._buffer[2 * i + 1] = (w >> 8) & 0o377
        else:
            w = self._buffer[2 * i] | (self._buffer[2 * i + 1] << 8)
            self._bus.write_word(self._xma(), w)
        self.rlba = (self.rlba + 2) & 0o177777
        self._buf_pos = i + 1
        self._remaining -= 1
        if self._remaining == 0 or self._buf_pos >= WORDS_PER_SECTOR:
            if self._writing:
                self.drive.write_sector(self.rlda, bytes(self._buffer))
            self.rlmp = (self.rlmp + self._buf_pos) & 0o177777
            self.rlda = next_rlda(self.rlda, self.drive.max_cyl)
            if self._remaining > 0:
                self._buffer = bytearray(self.drive.read_sector(self.rlda))
                self._buf_pos = 0
            else:
                self._active = False
                self._complete()

    def _xma(self) -> int:
        """Extended bus address: RLCS MEX bits <5:4> -> <17:16> + RLBA."""
        return ((((self.rlcs & RLCS_MEX) >> 4) << 16) | self.rlba) & 0o777777

    # -- interrupts (§6.1) ------------------------------------------------------------
    def interrupt_pending(self) -> tuple[int, int] | None:
        if (self.rlcs & RLCS_DONE) and (self.rlcs & RLCS_IE):
            return (VECTOR, BR_LEVEL)
        return None

    # -- snapshot contract (§15) -------------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {"base": self.base, "rlcs": self.rlcs, "rlba": self.rlba,
                "rlda": self.rlda, "rlmp": self.rlmp, "rlmp1": self._rlmp1,
                "rlmp2": self._rlmp2, "rlbae": self.rlbae,
                "active": self._active, "writing": self._writing, "wchk": self._wchk,
                "buf_pos": self._buf_pos, "remaining": self._remaining,
                "buffer": bytes(self._buffer).hex(),
                "drive_cylinder": self.drive.cylinder,
                "sectors_read": self.drive.sectors_read,
                "sectors_written": self.drive.sectors_written,
                "image_path": self.drive.image.path if self.drive.image else None,
                "seek_target": self._seek_target}

    def deserialize(self, state: dict[str, Any]) -> None:
        keys = ("base", "rlcs", "rlba", "rlda", "rlmp", "rlmp1", "rlmp2",
                "rlbae", "active", "writing", "wchk", "buf_pos", "remaining",
                "buffer", "drive_cylinder", "sectors_read", "sectors_written",
                "image_path", "seek_target")
        for key in keys:
            if key not in state:
                raise ValueError(f"RL11 snapshot missing {key!r}")
        self.base = state["base"]
        self.rlcs = state["rlcs"]
        self.rlba = state["rlba"]
        self.rlda = state["rlda"]
        self.rlmp = state["rlmp"]
        self._rlmp1 = state["rlmp1"]
        self._rlmp2 = state["rlmp2"]
        self.rlbae = state["rlbae"]
        self._active = state["active"]
        self._writing = state["writing"]
        self._wchk = state["wchk"]
        self._buf_pos = state["buf_pos"]
        self._remaining = state["remaining"]
        self._buffer = bytearray(bytes.fromhex(state["buffer"]))
        self.drive.cylinder = state["drive_cylinder"]
        self.drive.sectors_read = state["sectors_read"]
        self.drive.sectors_written = state["sectors_written"]
        self._seek_target = state["seek_target"]
