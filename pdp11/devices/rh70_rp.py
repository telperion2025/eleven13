"""
RH70 Massbus controller + RP04/RP06/RP07 (and RM03) disk drives (spec §7,
P2; 11/70 only). RSTS/E and RSX-11M boot from an RP06 image through this
path on the 11/70 profile (§11.4).

The RH70 translates Unibus program I/O into Massbus register space
(bus/massbus.py carries the register map, bit fields, and function codes,
verified against SIMH pdp11_rh.c); drives attach beneath it. The data path
is 22-bit physical DIRECT (RH70 BAE:BA) — on the 11/70 the RH70 does NOT go
through the Unibus map (SIMH: RH11-only Map_Addr) — so transfers run
through the board's massbus_host face.

Registers (octal word offsets from base 176700): RHCS1 176700, RHWC 176702,
RHBA 176704, RHCS2 176710, RHDB 176722, RHBAE 176750, RHCS3 176752; drive
registers via the PROM map: RPDS 176706, RPDA 176724, RPER1 176726,
RPDC 176734, RPCC 176736. RPDA fields: <9:5> surface, <4:0> sector (22
sectors); the cylinder is the drive's current cylinder (RPCC, set by SEEK
from RPDC).

Transfer model (§5.6, deterministic): like the RK11 reference driver, a
READ/WRITE moves one 16-bit word per NPR cycle-stealing step (dma_request()
while active, tick() moves one word through massbus_host, one stolen cycle
per word charged by board.service_dma). Whole 512-byte sectors are buffered
in the controller; BA/BAE auto-increment across words; WC is two's
complement (0 = 65536 words); on exhaustion DONE is set and (if IE) vector
000254 fires at BR5. SEEK schedules completion through SystemClock after a
cylinder-distance delay — never a wall-clock read. WRITE_CHECK is modeled
as WRITE in v1 (flagged §22.4).

Interrupt: vector 000254 at BR5. The real RH70's CSTB INTR flop (edge on
DONE, SIMH warning) is approximated level-based here: pending while
DONE & IE, plus a latched flop set when DONE goes 0->1 with IE; both clear
when the handler clears IE or issues a new GO (§22.4).

v1 models drive unit 0 only (CS2 unit select must be 0); containers are
raw 512-byte sector streams (§16, disk_images/container.py).

References: PDP-11/70 Processor Handbook Massbus chapter (EK-KB11C-TM-001);
RH70/RP06 manuals; SIMH pdp11_rh.c/pdp11_rp.c (§21).
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice, BusInit, DMAHost
from pdp11.bus.massbus import (
    BR_LEVEL,
    CS1_DONE,
    CS1_FNC_MASK,
    CS1_FNC_SHIFT,
    CS1_GO,
    CS1_IE,
    CS1_SC,
    CS1_TRE,
    CS1_UAE_MASK,
    CS1_UAE_SHIFT,
    CS2_CLR,
    CS2_ERR,
    CS2_IR,
    CS2_NED,
    CS2_NEM,
    CS2_OR,
    CS2_PGE,
    CS2_RW,
    CS2_UNIT_MASK,
    CS3_ERR,
    FNC_GET_STATUS,
    FNC_NOOP,
    FNC_OFFSET,
    FNC_PACK_ACK,
    FNC_READ,
    FNC_READ_HEADER,
    FNC_RECAL,
    FNC_RELEASE,
    FNC_SEEK,
    FNC_WRITE,
    FNC_WRITE_CHECK,
    FNC_XFER,
    RH70_BASE,
    RH70_WINDOW,
    RHBA,
    RHBAE,
    RHCS1,
    RHCS2,
    RHCS3,
    RHDB,
    RHWC,
    RPCC,
    RPDA,
    RPDA_SECT_MASK,
    RPDA_SURF_MASK,
    RPDA_SURF_SHIFT,
    RPDC,
    RPDS,
    RPER1,
    SEEK_BASE_CYCLES,
    SEEK_PER_CYL_CYCLES,
    VECTOR_RP,
)
from pdp11.disk_images.container import (
    RP06_CYLINDERS,
    RP06_SECTORS_PER_TRACK,
    RP06_SURFACES,
    RP_SECTOR,
    RawDiskImage,
    rp_sector_address,
)
from pdp11.system_clock import SystemClock

_log = logging.getLogger(__name__)

WORDS_PER_SECTOR = RP_SECTOR // 2     # 256 words per 512-byte sector

# RPDS bits (simplified set, flagged §22.4)
DS_DREADY = 0o000200     # drive ready
DS_ATA = 0o100000        # attention


class RPDrive:
    """One RP06/RP07 drive unit behind the RH70: geometry, head position,
    and sector I/O against the raw container (§16)."""

    def __init__(self, image: RawDiskImage | None = None, *,
                 cylinders: int = RP06_CYLINDERS, surfaces: int = RP06_SURFACES,
                 sectors_per_track: int = RP06_SECTORS_PER_TRACK) -> None:
        self.image = image
        self.cylinders = cylinders
        self.surfaces = surfaces
        self.sectors_per_track = sectors_per_track
        self.cylinder = 0          # current head position (RPCC)
        self.sectors_read = 0      # debugger statistics (§14)
        self.sectors_written = 0

    @property
    def loaded(self) -> bool:
        return self.image is not None

    def seek_delay(self, target: int) -> int:
        """Deterministic seek time in bus cycles: base + per-cylinder travel."""
        return SEEK_BASE_CYCLES + abs(target - self.cylinder) * SEEK_PER_CYL_CYCLES

    def disk_address(self, rpda: int) -> tuple[int, int, int]:
        """(cylinder, surface, sector) for an RPDA value at the current head
        position; ValueError when out of range."""
        surf = (rpda >> RPDA_SURF_SHIFT) & RPDA_SURF_MASK
        sect = rpda & RPDA_SECT_MASK
        if surf >= self.surfaces or sect >= self.sectors_per_track:
            raise ValueError(f"RPDA out of range: surf={surf} sect={sect}")
        return self.cylinder, surf, sect

    def sector_number(self, rpda: int) -> int:
        cyl, surf, sect = self.disk_address(rpda)
        return rp_sector_address(cyl, surf, sect, cylinders=self.cylinders,
                                 surfaces=self.surfaces,
                                 sectors_per_track=self.sectors_per_track)

    def next_rpda(self, rpda: int) -> int | None:
        """RPDA of the sector following `rpda` in a sequential transfer:
        wraps sector -> surface within the current cylinder; None past the
        last surface (a real drive needs a seek there — the caller errors)."""
        _cyl, surf, sect = self.disk_address(rpda)
        sect += 1
        if sect >= self.sectors_per_track:
            sect = 0
            surf += 1
            if surf >= self.surfaces:
                return None
        return (surf << RPDA_SURF_SHIFT) | sect

    def read_sector(self, rpda: int) -> bytes:
        n = self.sector_number(rpda)
        self.sectors_read += 1
        assert self.image is not None
        return self.image.read_sector(n)

    def write_sector(self, rpda: int, payload: bytes) -> None:
        n = self.sector_number(rpda)
        self.sectors_written += 1
        assert self.image is not None
        self.image.write_sector(n, payload)
        self.image.flush()


class RH70(BusDevice):
    """RH70 Massbus controller: register file (window 176700, 0o54 bytes)
    plus the tick()/scheduled-event transfer state machine (§7 design rule)."""

    window_size = RH70_WINDOW
    name = "RH70"

    def __init__(self, clock: SystemClock, dma: DMAHost,
                 drive: RPDrive | None = None, base: int = RH70_BASE) -> None:
        self._clock = clock
        self._dma = dma               # massbus_host: 22-bit physical direct
        self.base = base
        self.drive = drive if drive is not None else RPDrive()
        self.cs1 = CS1_DONE
        self.wc = 0
        self.ba = 0
        self.cs2 = CS2_IR | CS2_OR
        self.db = 0
        self.bae = 0
        self.cs3 = 0
        self.iff = False              # CSTB INTR flop approximation (§22.4)
        self.rpds = 0
        self.rpda = 0
        self.rper1 = 0
        self.rpdc = 0
        self._active = False
        self._writing = False
        self._buffer = bytearray(RP_SECTOR)
        self._buf_pos = 0
        self._remaining = 0
        self.reset()

    # -- lifecycle -------------------------------------------------------------
    def reset(self) -> None:
        """Bus init / CS2 CLR: registers to reset values, transfer aborted."""
        self.cs1 = CS1_DONE
        self.wc = 0
        self.ba = 0
        self.cs2 = CS2_IR | CS2_OR      # CLR also clears the error bits
        self.db = 0
        self.bae = 0
        self.cs3 = 0
        self.iff = False
        self.rpds = DS_DREADY if self.drive.loaded else 0
        self.rpdc = 0
        self._active = False
        self._writing = False
        self._buffer = bytearray(RP_SECTOR)
        self._buf_pos = 0
        self._remaining = 0

    # -- register window ---------------------------------------------------------
    def read_word(self, offset: int) -> int:
        reg = offset & 0o177776
        if reg == RHCS1:
            self._update_cs1()
            return self.cs1 & 0o177777
        if reg == RHWC:
            return self.wc & 0o177777
        if reg == RHBA:
            return self.ba & 0o177777
        if reg == RHCS2:
            self.cs2 = (self.cs2 & ~0o177777 & 0) | (self.cs2 & ~0o100077 & 0o177777) \
                | CS2_IR | CS2_OR
            return self.cs2 & 0o177777
        if reg == RHDB:
            return self.db & 0o177777
        if reg == RHBAE:
            return self.bae & 0o77
        if reg == RHCS3:
            self.cs3 = (self.cs3 & ~(CS1_IE | 0o1760)) | (self.cs1 & CS1_IE)
            return self.cs3 & 0o177777
        if reg == RPDS:
            return self.rpds & 0o177777
        if reg == RPDA:
            return self.rpda & 0o177777
        if reg == RPER1:
            return self.rper1 & 0o177777
        if reg == RPDC:
            return self.rpdc & 0o177777
        if reg == RPCC:
            return self.drive.cylinder & 0o177777
        return 0

    def write_word(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        v = value & 0o177777
        if reg == RHCS1:
            if v & CS1_TRE:                       # error clear strobe
                self.cs1 &= ~CS1_TRE
                self.cs2 &= ~CS2_ERR
                self.cs3 &= ~CS3_ERR
            if v & CS1_DONE and v & CS1_IE:       # write DONE+IE: set INTR flop
                self.iff = True
            self.cs1 = (self.cs1 & ~CS1_IE) | (v & CS1_IE)
            if v & CS1_GO:
                fnc = (v & CS1_FNC_MASK) >> CS1_FNC_SHIFT
                if fnc >= FNC_XFER and not (self.cs1 & CS1_DONE):
                    self.cs2 |= CS2_PGE            # data xfer while busy: program error
                else:
                    self.cs1 &= ~(CS1_TRE | CS1_SC | CS1_DONE)
                    self.cs2 &= ~CS2_ERR
                    self.cs3 &= ~CS3_ERR
                    self._go(fnc)
            self.cs3 = (self.cs3 & ~CS1_IE) | (self.cs1 & CS1_IE)
            self.bae = (self.bae & ~0o3) | ((self.cs1 >> CS1_UAE_SHIFT) & 0o3)
        elif reg == RHWC:
            self.wc = v
        elif reg == RHBA:
            self.ba = v & 0o177777
        elif reg == RHCS2:
            if v & CS2_CLR:
                self.reset()
            else:
                self.cs2 = (self.cs2 & ~CS2_RW) | (v & CS2_RW) | CS2_IR | CS2_OR
        elif reg == RHDB:
            self.db = v
        elif reg == RHBAE:
            self.bae = v & 0o77
            self.cs1 = (self.cs1 & ~CS1_UAE_MASK) | ((self.bae << CS1_UAE_SHIFT) & CS1_UAE_MASK)
        elif reg == RHCS3:
            self.cs3 = (self.cs3 & ~0o17) | (v & 0o17)
            self.cs1 = (self.cs1 & ~CS1_IE) | (self.cs3 & CS1_IE)
        elif reg == RPDA:
            self.rpda = v
        elif reg == RPDC:
            self.rpdc = v & 0o1777
        elif reg == RPER1:
            self.rper1 = 0                        # write clears error register
        # RPDS/RPCC read-only: writes ignored

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
    def _go(self, fnc: int) -> None:
        self._active = False
        unit = self.cs2 & CS2_UNIT_MASK
        if unit != 0:
            self._error(CS2_NED)                  # v1: drive unit 0 only
            return
        if not self.drive.loaded:
            self._error(CS2_NED)
            return
        if fnc in (FNC_READ, FNC_WRITE, FNC_WRITE_CHECK):
            self._begin_transfer(fnc)
        elif fnc == FNC_SEEK:
            self._seek()
        elif fnc == FNC_RECAL:
            self.drive.cylinder = 0
            self._complete()
        elif fnc in (FNC_NOOP, FNC_PACK_ACK, FNC_RELEASE, FNC_OFFSET, FNC_GET_STATUS):
            if fnc == FNC_PACK_ACK:
                self.rpds |= DS_DREADY
            self._complete()
        elif fnc == FNC_READ_HEADER:
            self._complete()                      # v1: no header data path (§22.4)
        else:
            self._error(CS2_PGE)

    def _seek(self) -> None:
        target = self.rpdc & 0o1777
        if target >= self.drive.cylinders:
            self._error(CS2_NED)
            return
        delay = self.drive.seek_delay(target)
        self._clock.schedule(delay, lambda t=target: self._seek_done(t))

    def _seek_done(self, target: int) -> None:
        self.drive.cylinder = target
        self._complete()

    def _begin_transfer(self, fnc: int) -> None:
        try:
            self.drive.sector_number(self.rpda)
        except ValueError:
            self._error(CS2_NED)
            return
        self._writing = fnc in (FNC_WRITE, FNC_WRITE_CHECK)
        n = (0o200000 - self.wc) & 0o177777
        self._remaining = n if n else 0o200000
        self._load_sector()
        self._active = True

    def _load_sector(self) -> None:
        self._buffer = bytearray(self.drive.read_sector(self.rpda))
        self._buf_pos = 0

    def _complete(self) -> None:
        """DONE 0->1 latches the interrupt flop when IE is set (CSTB INTR)."""
        if not (self.cs1 & CS1_DONE) and (self.cs1 & CS1_IE):
            self.iff = True
        self.cs1 |= CS1_DONE
        self._update_cs1()

    def _error(self, bit: int) -> None:
        self.cs2 |= bit
        self.cs1 |= CS1_TRE | CS1_SC | CS1_DONE
        _log.info("RH70 error %o (CS2 now %o)", bit, self.cs2)

    def _update_cs1(self) -> None:
        """CS1<15:14> reflect errors: SC set when CS2 has any error (§massbus)."""
        if self.cs2 & CS2_ERR:
            self.cs1 |= CS1_TRE | CS1_SC
        elif self.cs1 & CS1_TRE:
            self.cs1 |= CS1_SC

    # -- NPR cycle stealing (§5.5/§6.1) ----------------------------------------------
    def dma_request(self) -> bool:
        return self._active

    def tick(self) -> None:
        """Move ONE word through the massbus_host face (22-bit physical
        (BAE<<16)|BA), one stolen cycle charged by board.service_dma."""
        if not self._active:
            return
        addr = (self.bae << 16) | self.ba
        i = self._buf_pos
        try:
            if self._writing:
                w = self._dma.read_word(addr)
                self._buffer[2 * i] = w & 0o377
                self._buffer[2 * i + 1] = (w >> 8) & 0o377
            else:
                w = self._buffer[2 * i] | (self._buffer[2 * i + 1] << 8)
                self._dma.write_word(addr, w)
        except BusInit:
            self._active = False
            self._error(CS2_NEM)                  # nonexistent memory (§5.5)
            return
        # advance BA/BAE (22-bit), mirror into CS1 UAE bits
        addr = (addr + 2) & 0o17777777
        self.ba = addr & 0o177777
        self.bae = (addr >> 16) & 0o77
        self.cs1 = (self.cs1 & ~CS1_UAE_MASK) | ((self.bae << CS1_UAE_SHIFT) & CS1_UAE_MASK)
        self.wc = (self.wc + 1) & 0o177777
        self._buf_pos = i + 1
        self._remaining -= 1
        if self._remaining == 0 or self._buf_pos >= WORDS_PER_SECTOR:
            if self._writing:
                self.drive.write_sector(self.rpda, bytes(self._buffer))
            if self._remaining > 0:
                # next sector: RPDA auto-increments, wrapping sector->surface
                nxt = self.drive.next_rpda(self.rpda)
                if nxt is None:
                    self._active = False
                    self._error(CS2_NED)          # past cylinder end: needs a seek
                    return
                self.rpda = nxt
                try:
                    self._load_sector()
                except ValueError:
                    self._active = False
                    self._error(CS2_NED)
                    return
            else:
                self._active = False
                self._complete()

    # -- interrupts (§6.1: polled by the bus arbiter) --------------------------------
    def interrupt_pending(self) -> tuple[int, int] | None:
        """Vector 254 at BR5 while the flop is latched or DONE & IE (the
        level-based approximation of the RH70's CSTB INTR, §22.4)."""
        if self.iff or ((self.cs1 & CS1_DONE) and (self.cs1 & CS1_IE)):
            return (VECTOR_RP, BR_LEVEL)
        if (self.cs1 & CS1_SC) and (self.cs1 & CS1_DONE) and (self.cs1 & CS1_IE):
            return (VECTOR_RP, BR_LEVEL)
        return None

    # -- snapshot contract (§15) --------------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {
            "base": self.base, "cs1": self.cs1, "wc": self.wc, "ba": self.ba,
            "cs2": self.cs2, "db": self.db, "bae": self.bae, "cs3": self.cs3,
            "iff": self.iff, "rpds": self.rpds, "rpda": self.rpda,
            "rper1": self.rper1, "rpdc": self.rpdc,
            "active": self._active, "writing": self._writing,
            "buf_pos": self._buf_pos, "remaining": self._remaining,
            "buffer": bytes(self._buffer).hex(),
            "cylinder": self.drive.cylinder,
            "sectors_read": self.drive.sectors_read,
            "sectors_written": self.drive.sectors_written,
            "image_path": self.drive.image.path if self.drive.image else None,
        }

    def deserialize(self, state: dict[str, Any]) -> None:
        keys = ("base", "cs1", "wc", "ba", "cs2", "db", "bae", "cs3", "iff",
                "rpds", "rpda", "rper1", "rpdc", "active", "writing", "buf_pos",
                "remaining", "buffer", "cylinder", "sectors_read",
                "sectors_written", "image_path")
        for key in keys:
            if key not in state:
                raise ValueError(f"RH70 snapshot missing {key!r}")
        self.base = state["base"]
        self.cs1 = state["cs1"]
        self.wc = state["wc"]
        self.ba = state["ba"]
        self.cs2 = state["cs2"]
        self.db = state["db"]
        self.bae = state["bae"]
        self.cs3 = state["cs3"]
        self.iff = state["iff"]
        self.rpds = state["rpds"]
        self.rpda = state["rpda"]
        self.rper1 = state["rper1"]
        self.rpdc = state["rpdc"]
        self._active = state["active"]
        self._writing = state["writing"]
        self._buf_pos = state["buf_pos"]
        self._remaining = state["remaining"]
        self._buffer = bytearray(bytes.fromhex(state["buffer"]))
        self.drive.cylinder = state["cylinder"]
        self.drive.sectors_read = state["sectors_read"]
        self.drive.sectors_written = state["sectors_written"]
        # the container itself is re-attached by the host (main.py) in M8


class RP06(RPDrive):
    """An RP06 drive: 815 cylinders x 19 surfaces x 22 sectors of 512 bytes."""

    def __init__(self, image: RawDiskImage | None = None) -> None:
        super().__init__(image, cylinders=RP06_CYLINDERS, surfaces=RP06_SURFACES,
                         sectors_per_track=RP06_SECTORS_PER_TRACK)


class RP07(RPDrive):
    """An RP07 drive: 630 cylinders x 32 surfaces x 43 sectors of 512 bytes."""

    def __init__(self, image: RawDiskImage | None = None, *,
                 cylinders: int = 630, surfaces: int = 32,
                 sectors_per_track: int = 43) -> None:
        super().__init__(image, cylinders=cylinders, surfaces=surfaces,
                         sectors_per_track=sectors_per_track)

