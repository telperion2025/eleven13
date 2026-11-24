"""
RK05 cartridge disk + RK11 controller emulation (spec §7, P0) — the
REFERENCE device implementation for all other drivers (§7).

Registers (octal word offsets from base 177400):
    RKDS  177400  drive status      (read-only, reset 000000)
    RKER  177402  error register    (read clears it)
    RKCS  177404  control/status    (read/write, reset 000200 = DONE)
    RKWC  177406  word count        (read/write, two's-complement)
    RKBA  177410  bus address       (read/write)
    RKDA  177412  disk address      (read/write)
    RKMR  177414  maintenance       (unused, stubbed)

RKCS bit layout (PDP-11 Peripherals Handbook, RK11 section):
    <15>   ERROR  read-only, cleared by GO            0o100000
    <7>    DONE   read-only, 1 when idle/complete      0o000200
    <6>    IE     interrupt enable, read/write        0o000100
    <3:1>  function code, read/write                  0o000016
    <0>    GO     write strobe, reads back 0           0o000001
Function codes: 0 drive reset, 1 pack acknowledge, 2 seek, 3 release,
4 read, 5 read header, 6 write, 7 write check.

Interrupt: vector 000220 at BR5, asserted while DONE & IE — level-based,
exactly as the hardware's request line (§5.5); a handler that leaves IE
set with DONE set will be re-vectored, so real drivers clear IE or issue
a new GO, and so do our tests.

RKDA layout: <15:13> drive select, <12:5> cylinder, <4> surface,
<3:0> sector. RK05 geometry: 2 surfaces x 203 cylinders x 12 sectors of
512 bytes, raw container, no header (§16 — disk_images/container.py;
the 4879-sector container holds 4872 geometry-addressable sectors plus
spare sectors, per the spec's own §16 note).

Transfer model (§5.6, deterministic): a READ/WRITE command moves data one
16-bit word at a time through NPR cycle stealing — dma_request() is
asserted while a transfer is active and tick() moves exactly one word
per call through the DMAHost bus face (one stolen cycle per word, §5.5,
charged by board.service_dma). Whole 512-byte sectors are buffered in
the controller; RKDA auto-increments across sectors until RKWC is
exhausted, then DONE is set and (if IE) vector 220 fires. Writes are
read-modify-write per sector so a short final transfer cannot clobber
the sector tail. SEEK schedules completion through SystemClock after a
cylinder-distance-dependent delay — never a wall-clock read.

Bit-level details the spec does not pin down (full RKDS/RKER bit sets,
GO read-back, write-check verify pass, seek-time constants) follow the
Peripherals Handbook best recollection and are FLAGGED for SIMH
cross-check (§22.4); see docs/acceptance-checklist.md. v1 models drive
unit 0 only; pack-ack/release/read-header complete immediately with no
data path.

References:
    PDP-11 Peripherals Handbook (1976), RK11 section (§21);
    RK11/RK05 engineering drawings EK-RK110-MM-001.
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice, DMAHost
from pdp11.disk_images.container import (
    RK05_CYLINDERS,
    RK05_SECTOR,
    RK05_SECTORS_PER_TRACK,
    RK05_SURFACES,
    RawDiskImage,
    rk05_sector_address,
)
from pdp11.system_clock import SystemClock

_log = logging.getLogger(__name__)

# -- register window (octal byte offsets from base) ---------------------------
RK11_BASE = 0o177400
RKDS = 0o00   # drive status
RKER = 0o02   # error register
RKCS = 0o04   # control/status
RKWC = 0o06   # word count
RKBA = 0o10   # bus address
RKDA = 0o12   # disk address
RKMR = 0o14   # maintenance (stubbed)

# -- RKCS bits ------------------------------------------------------------------
RKCS_ERROR = 0o100000   # <15> any error (read-only)
RKCS_DONE = 0o000200    # <7>  ready / command complete (read-only)
RKCS_IE = 0o000100      # <6>  interrupt enable
RKCS_FCT_MASK = 0o000016  # <3:1> function code
RKCS_FCT_SHIFT = 1
RKCS_GO = 0o000001      # <0>  GO strobe (reads 0)
RKCS_WRITE_MASK = RKCS_IE | RKCS_FCT_MASK | RKCS_GO

# -- function codes (RKCS <3:1>) --------------------------------------------------
FCT_RESET = 0o0
FCT_PACK_ACK = 0o1
FCT_SEEK = 0o2
FCT_RELEASE = 0o3
FCT_READ = 0o4
FCT_READ_HEADER = 0o5
FCT_WRITE = 0o6
FCT_WRITE_CHECK = 0o7

VECTOR = 0o000220   # RK11 interrupt vector
BR_LEVEL = 5        # BR5

# -- RKDS bits (simplified set, flagged §22.4) ------------------------------------
RKDS_DRY = 0o000001   # drive ready: a pack is loaded

# -- RKER bits (simplified set, flagged §22.4) ------------------------------------
RKER_ERROR = 0o100000   # composite: any error occurred
RKER_NO_PACK = 0o000001  # no cartridge loaded / drive select != 0
RKER_BAD_ADDR = 0o000002  # RKDA cylinder/sector out of range

# -- RKDA fields --------------------------------------------------------------------
RKDA_DRIVE_MASK = 0o170000
RKDA_DRIVE_SHIFT = 13
RKDA_CYL_MASK = 0o007760
RKDA_CYL_SHIFT = 5
RKDA_SURF = 0o000020
RKDA_SECT_MASK = 0o000017

WORDS_PER_SECTOR = RK05_SECTOR // 2   # 256 words per 512-byte sector

# Seek time model (§5.6): base overhead + per-cylinder of head travel,
# ~25 ms worst case at 150 ns/bus-cycle — approximation, SIMH cross-check §22.4.
SEEK_BASE_CYCLES = 0o11610     # 5000
SEEK_PER_CYL_CYCLES = 0o1440   # 800


def next_disk_address(da: int) -> int:
    """RKDA auto-increment across sectors, wrapping surface then cylinder
    per the RK05 geometry (v1 keeps the drive-select bits)."""
    cyl = (da & RKDA_CYL_MASK) >> RKDA_CYL_SHIFT
    surf = 1 if da & RKDA_SURF else 0
    sect = (da & RKDA_SECT_MASK) + 1
    if sect >= RK05_SECTORS_PER_TRACK:
        sect = 0
        surf += 1
        if surf >= RK05_SURFACES:
            surf = 0
            cyl += 1
    return (da & RKDA_DRIVE_MASK) | (cyl << RKDA_CYL_SHIFT) | (surf * RKDA_SURF) | sect


class RK05:
    """One RK05 drive unit behind the RK11: geometry, head position, and
    sector I/O against the raw container (§16)."""

    def __init__(self, image: RawDiskImage | None = None) -> None:
        self.image = image
        self.cylinder = 0          # current head position
        self.sectors_read = 0      # debugger statistics (§14)
        self.sectors_written = 0

    @property
    def loaded(self) -> bool:
        """True when a cartridge (container) is loaded in the drive."""
        return self.image is not None

    def seek_delay(self, target_cylinder: int) -> int:
        """Deterministic seek time in bus cycles: base + per-cylinder travel."""
        return SEEK_BASE_CYCLES + abs(target_cylinder - self.cylinder) * SEEK_PER_CYL_CYCLES

    def check_address(self, da: int) -> tuple[int, int, int]:
        """Decode RKDA -> (cylinder, surface, sector); ValueError if the
        address is not a valid RK05 position (or selects another drive —
        v1 models drive unit 0 only)."""
        drive = (da & RKDA_DRIVE_MASK) >> RKDA_DRIVE_SHIFT
        cyl = (da & RKDA_CYL_MASK) >> RKDA_CYL_SHIFT
        surf = 1 if da & RKDA_SURF else 0
        sect = da & RKDA_SECT_MASK
        if drive != 0:
            raise ValueError(f"RKDA selects drive {drive} (v1: drive 0 only)")
        if cyl >= RK05_CYLINDERS or sect >= RK05_SECTORS_PER_TRACK:
            raise ValueError(f"RKDA out of range: cyl={cyl} surf={surf} sect={sect}")
        return cyl, surf, sect

    def read_sector(self, da: int) -> bytes:
        """Read the sector named by RKDA from the pack (512 bytes)."""
        cyl, surf, sect = self.check_address(da)
        self.sectors_read += 1
        assert self.image is not None
        return self.image.read_sector(rk05_sector_address(cyl, surf, sect))

    def write_sector(self, da: int, payload: bytes) -> None:
        """Write one full sector to the pack and flush the container, so a
        host-side crash never loses a written sector (lossless §16)."""
        cyl, surf, sect = self.check_address(da)
        self.sectors_written += 1
        assert self.image is not None
        self.image.write_sector(rk05_sector_address(cyl, surf, sect), payload)
        self.image.flush()


class RK11(BusDevice):
    """RK11 controller: register file (window 177400-177417) plus the
    tick()/scheduled-event transfer state machine (§7 design rule)."""

    window_size = 0o20          # 8 word registers = 16 bytes
    name = "RK11"

    def __init__(self, clock: SystemClock, bus: DMAHost,
                 drive: RK05 | None = None, base: int = RK11_BASE) -> None:
        self._clock = clock
        self._bus = bus             # DMAHost: the bus face transfers go through
        self.base = base
        self.drive = drive if drive is not None else RK05()
        self.rkds = 0
        self.rker = 0
        self.rkcs = RKCS_DONE
        self.rkwc = 0
        self.rkba = 0
        self.rkda = 0
        self._active = False        # a READ/WRITE transfer holds the NPR line
        self._writing = False
        self._buffer = bytearray(RK05_SECTOR)
        self._buf_pos = 0           # words moved within the current sector
        self._remaining = 0         # words left per RKWC
        self._seek_target = 0       # pending seek target (serializable event arg, §15)
        self.reset()

    # -- lifecycle -------------------------------------------------------------
    def reset(self) -> None:
        """Bus init: registers to reset values, any transfer aborted,
        DONE set (the controller is ready)."""
        self.rkcs = RKCS_DONE
        self.rker = 0
        self.rkwc = 0
        self.rkba = 0
        self.rkda = 0
        self.rkds = RKDS_DRY if self.drive.loaded else 0
        self._active = False
        self._writing = False
        self._buffer = bytearray(RK05_SECTOR)
        self._buf_pos = 0
        self._remaining = 0

    # -- register window ---------------------------------------------------------
    def read_word(self, offset: int) -> int:
        reg = offset & 0o177776          # register index = offset >> 1
        if reg == RKDS:
            return self.rkds & 0o177777
        if reg == RKER:
            value = self.rker             # reading RKER clears it (§10.2)
            self.rker = 0
            return value & 0o177777
        if reg == RKCS:
            return self.rkcs & 0o177777   # GO reads back 0 (stored cleared)
        if reg == RKWC:
            return self.rkwc & 0o177777
        if reg == RKBA:
            return self.rkba & 0o177777
        if reg == RKDA:
            return self.rkda & 0o177777
        return 0                          # RKMR maintenance: stubbed

    def write_word(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        v = value & 0o177777
        if reg == RKWC:
            self.rkwc = v
        elif reg == RKBA:
            self.rkba = v
        elif reg == RKDA:
            self.rkda = v
        elif reg == RKCS:
            if v & RKCS_GO:
                # GO strobe: the same word also carries IE + function
                self.rkcs = ((self.rkcs & ~(RKCS_DONE | RKCS_ERROR))
                             | (v & (RKCS_IE | RKCS_FCT_MASK))) & 0o177777
                self._go((v & RKCS_FCT_MASK) >> RKCS_FCT_SHIFT)
            else:
                # no GO: update IE + function only; DONE/ERROR untouched
                self.rkcs = (self.rkcs & ~RKCS_WRITE_MASK & 0o177777) | (v & RKCS_WRITE_MASK)
        # RKDS read-only, RKMR stubbed: writes ignored

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
        """GO strobe: start a command. (DONE/ERROR were already cleared and
        IE/function stored by the caller; here the transfer state resets.)"""
        self._active = False
        _log.debug("RK11 GO function %o", fct)
        if fct == FCT_RESET:
            self.rker = 0
            self.drive.cylinder = 0
            self._complete()
        elif fct == FCT_SEEK:
            self._seek(fct)
        elif fct in (FCT_READ, FCT_WRITE, FCT_WRITE_CHECK):
            self._begin_transfer(fct)
        else:
            # pack ack / release / read header: v1 completes immediately with
            # no data path (flagged §22.4)
            self._complete()

    def _seek(self, fct: int) -> None:
        """SEEK: schedule completion after the deterministic head-travel
        delay; the drive's cylinder register updates on completion."""
        if not self.drive.loaded:
            self._error(RKER_NO_PACK)
            return
        target = (self.rkda & RKDA_CYL_MASK) >> RKDA_CYL_SHIFT
        if target >= RK05_CYLINDERS:
            self._error(RKER_BAD_ADDR)
            return
        delay = self.drive.seek_delay(target)
        self._seek_target = target          # bound method: queue stays serializable (§15)
        self._clock.schedule(delay, self._seek_done)

    def _seek_done(self) -> None:
        self.drive.cylinder = self._seek_target
        self._complete()

    def _begin_transfer(self, fct: int) -> None:
        """READ/WRITE: validate pack + address, set up the word count
        (RKWC is two's complement; 0 means 65536 words), prime the sector
        buffer, and assert the NPR line."""
        if not self.drive.loaded:
            self._error(RKER_NO_PACK)
            return
        try:
            self.drive.check_address(self.rkda)
        except ValueError:
            self._error(RKER_BAD_ADDR)
            return
        self._writing = fct in (FCT_WRITE, FCT_WRITE_CHECK)
        # WRITE_CHECK is modeled as WRITE in v1: the verify pass is not
        # modeled (flagged §22.4).
        n = (0o200000 - self.rkwc) & 0o177777
        self._remaining = n if n else 0o200000     # RKWC=0 -> full 64K words
        self._load_sector()
        self._active = True

    def _load_sector(self) -> None:
        """Prime the controller buffer with the current sector. For writes
        this is a read-modify-write: a short transfer leaves the sector
        tail intact instead of clobbering it."""
        self._buffer = bytearray(self.drive.read_sector(self.rkda))
        self._buf_pos = 0

    def _complete(self) -> None:
        """Command completion: set DONE; the BR5/220 request asserts while
        DONE & IE (level-based, §5.5)."""
        self.rkcs |= RKCS_DONE

    def _error(self, bit: int) -> None:
        """Record an error in RKER, set RKCS ERROR + DONE; the interrupt
        still fires if IE is set."""
        self.rker |= bit | RKER_ERROR
        self.rkcs |= RKCS_DONE | RKCS_ERROR
        _log.info("RK11 error %o (RKER now %o)", bit, self.rker)

    # -- NPR cycle stealing (§5.5/§6.1) ----------------------------------------------
    def dma_request(self) -> bool:
        """Assert the NPR line while a READ/WRITE transfer is active."""
        return self._active

    def tick(self) -> None:
        """Move ONE word through the bus (one stolen cycle, charged by
        board.service_dma): reads push buffer -> memory, writes pull
        memory -> buffer. At a sector boundary the buffer is committed
        (write) and RKDA auto-increments while words remain."""
        if not self._active:
            return
        i = self._buf_pos
        if self._writing:
            w = self._bus.read_word(self.rkba)          # one bus cycle
            self._buffer[2 * i] = w & 0o377
            self._buffer[2 * i + 1] = (w >> 8) & 0o377
        else:
            w = self._buffer[2 * i] | (self._buffer[2 * i + 1] << 8)
            self._bus.write_word(self.rkba, w)           # one bus cycle
        self.rkba = (self.rkba + 2) & 0o177777
        self._buf_pos = i + 1
        self._remaining -= 1
        if self._remaining == 0 or self._buf_pos >= WORDS_PER_SECTOR:
            if self._writing:
                self.drive.write_sector(self.rkda, bytes(self._buffer))
            self.rkda = next_disk_address(self.rkda)   # past the sector just done
            if self._remaining > 0:
                self._load_sector()
            else:
                self._active = False
                self._complete()

    # -- interrupts (§6.1: polled by the bus arbiter) --------------------------------
    def interrupt_pending(self) -> tuple[int, int] | None:
        if (self.rkcs & RKCS_DONE) and (self.rkcs & RKCS_IE):
            return (VECTOR, BR_LEVEL)
        return None

    # -- snapshot contract (§15) --------------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "rkds": self.rkds, "rker": self.rker, "rkcs": self.rkcs,
            "rkwc": self.rkwc, "rkba": self.rkba, "rkda": self.rkda,
            "active": self._active, "writing": self._writing,
            "buf_pos": self._buf_pos, "remaining": self._remaining,
            "buffer": bytes(self._buffer).hex(),
            "drive_cylinder": self.drive.cylinder,
            "sectors_read": self.drive.sectors_read,
            "sectors_written": self.drive.sectors_written,
            "image_path": self.drive.image.path if self.drive.image else None,
            "seek_target": self._seek_target,
        }

    def deserialize(self, state: dict[str, Any]) -> None:
        keys = ("base", "rkds", "rker", "rkcs", "rkwc", "rkba", "rkda", "active",
                "writing", "buf_pos", "remaining", "buffer", "drive_cylinder",
                "sectors_read", "sectors_written", "image_path", "seek_target")
        for key in keys:
            if key not in state:
                raise ValueError(f"RK11 snapshot missing {key!r}")
        self.base = state["base"]
        self.rkds = state["rkds"]
        self.rker = state["rker"]
        self.rkcs = state["rkcs"]
        self.rkwc = state["rkwc"]
        self.rkba = state["rkba"]
        self.rkda = state["rkda"]
        self._active = state["active"]
        self._writing = state["writing"]
        self._buf_pos = state["buf_pos"]
        self._remaining = state["remaining"]
        self._buffer = bytearray(bytes.fromhex(state["buffer"]))
        self.drive.cylinder = state["drive_cylinder"]
        self.drive.sectors_read = state["sectors_read"]
        self.drive.sectors_written = state["sectors_written"]
        self._seek_target = state["seek_target"]
        # the container itself is re-attached by the host (main.py) in M8;
        # v1 restores controller state and records the image path
