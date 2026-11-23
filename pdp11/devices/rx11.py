"""
RX01 floppy + RX11 controller (spec §7, P0; RX02 double-density is out of
scope for v1, §16). RT-11 boots from an RX01 image on the 11/03 and 11/94
profiles (§11.2).

Registers (octal, RX11 at 177170), window 4 bytes — SIMH pdp11_rx.c
IOLN_RX = 004:
    RXCS  177170  command/status
    RXDB  177172  data buffer (byte; doubles as sector/track/ESR/ECODE
                  depending on the function in progress)

RXCS bits (SIMH rx.c): <15> ERR, <14> INIT, <7> TR xfer request, <6> IE,
<5> DONE, <4> DRV drive select, <3:1> function. Functions: 0 FILL buffer,
1 EMPTY buffer, 2 WRITE sector, 3 READ sector, 5 read status (RXES),
6 WRITE deleted, 7 read error code. <0> GO. Only IE is read/write.
Interrupt: vector 000264 at BR5 (MAINDEC DZRXB-E default params), asserted
while DONE & IE (level-based, §5.5).

Transfer model (§5.6, deterministic): the RX11 is programmed I/O — one
byte at a time through RXDB with the TR handshake (no DMA). State machine
IDLE -> (GO) -> FILL/EMPTY/RWDS... -> RWXFR, with seek time scheduled as
SEEK_PER_TRACK cycles per track of travel and command/TR delays via
SystemClock — never wall clock. RXES bits: CRC 0o1, PAR 0o2, ID 0o4,
WLK 0o10, DD 0o100, DRDY 0o200. Error codes: 0o10 no disk, 0o20 drive 1
missing, 0o40 bad track, 0o70 bad sector, 0o100 write locked, 0o110
unattached.

RX01 container: raw 128-byte sectors, 77 tracks x 26 sectors (§16,
disk_images/container.py); RX01 sectors are numbered 1-26, so container
sector = track*26 + (sector-1).

Bit layout cross-checked against SIMH pdp11_rx.c (§22.4); timing constants
are SIMH's rx_cwait/rx_swait/rx_xwait scaled into bus cycles (flagged).

References: PDP-11 Peripherals Handbook, RX11 section (§21); EK-RX01-OP-001;
SIMH pdp11_rx.c.
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice
from pdp11.disk_images.container import RX01_SECTORS_PER_TRACK, RX01_TRACKS, RawDiskImage
from pdp11.system_clock import SystemClock

RX11_BASE = 0o177170
RXCS = 0o00
RXDB = 0o02

# RXCS bits
RXCS_GO = 0o000001
RXCS_FUNC_MASK = 0o000016
RXCS_FUNC_SHIFT = 1
RXCS_DRV = 0o000020
RXCS_DONE = 0o000040
RXCS_IE = 0o000100
RXCS_TR = 0o000200
RXCS_INIT = 0o020000
RXCS_ERR = 0o100000
RXCS_ROUT = RXCS_ERR | RXCS_TR | RXCS_IE | RXCS_DONE
RXCS_RW = RXCS_IE

# functions (RXCS <3:1>)
FCT_FILL = 0o0
FCT_EMPTY = 0o1
FCT_WRITE = 0o2
FCT_READ = 0o3
FCT_RXES = 0o5
FCT_WRDEL = 0o6
FCT_ECODE = 0o7

# RXES bits
RXES_CRC = 0o001
RXES_PAR = 0o002
RXES_ID = 0o004
RXES_WLK = 0o010
RXES_DD = 0o100
RXES_DRDY = 0o200

# error codes
ECODE_NO_DISK = 0o010
ECODE_DRV1_MISSING = 0o020
ECODE_BAD_TRACK = 0o040
ECODE_BAD_SECTOR = 0o070
ECODE_WLOCK = 0o100
ECODE_UNATTACHED = 0o110

VECTOR = 0o000264
BR_LEVEL = 5

SECTOR_BYTES = 128

# timing in bus cycles (SIMH rx_cwait=100, rx_swait=10/track, rx_xwait=1)
CMD_CYCLES = 0o144
SEEK_PER_TRACK = 0o12
TR_CYCLES = 1

# states
IDLE, RWDS, RWDT, RWXFR, FILL, EMPTY, CMD_COMPLETE, INIT_COMPLETE = range(8)

_log = logging.getLogger(__name__)


class RX11(BusDevice):
    """RX11 controller + RX01 drive: RXCS/RXDB register file plus the
    TR-handshake sector state machine (§7 design rule)."""

    window_size = 0o04           # two word registers = 4 bytes
    name = "RX11"

    def __init__(self, clock: SystemClock, image: RawDiskImage | None = None,
                 base: int = RX11_BASE, writable: bool = True) -> None:
        self._clock = clock
        self.base = base
        self.image = image
        self.writable = writable
        self.track = 0               # current head track
        self.csr = 0
        self.dbr = 0
        self.esr = 0
        self.ecode = 0
        self.state = IDLE
        self.buf = bytearray(SECTOR_BYTES)
        self.bptr = 0
        self._track = 0
        self._sector = 0
        self.reset()

    # -- lifecycle -------------------------------------------------------------
    def reset(self) -> None:
        """INIT: registers clear; with a diskette loaded the controller runs
        an implicit init (reads track 1 sector 1 into the buffer, sets
        RXES_ID | RXES_DRDY, DONE). Without one: error 0o10."""
        self.csr = 0
        self.dbr = 0
        self.esr = 0
        self.ecode = 0
        self.state = IDLE
        self.bptr = 0
        self.buf = bytearray(SECTOR_BYTES)
        self._track = 0
        self._sector = 0
        if self.image is not None:
            self.state = INIT_COMPLETE
            self._clock.schedule(SEEK_PER_TRACK, self._svc)
        else:
            self._done(0, ECODE_NO_DISK)

    # -- register window ---------------------------------------------------------
    def read_word(self, offset: int) -> int:
        reg = offset & 0o177776
        if reg == RXCS:
            return self.csr & RXCS_ROUT
        if reg == RXDB:
            if self.state == EMPTY and self.csr & RXCS_TR:
                self.csr &= ~RXCS_TR                 # hand the byte over
                self._clock.schedule(TR_CYCLES, self._svc)
            return self.dbr & 0o377
        return 0

    def write_word(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        v = value & 0o177777
        if reg == RXCS:
            if v & RXCS_INIT:
                self.reset()
                return
            if (v & RXCS_GO) and self.state == IDLE:
                self.csr = v & (RXCS_IE | RXCS_DRV | RXCS_FUNC_MASK)
                self.bptr = 0
                fct = (v & RXCS_FUNC_MASK) >> RXCS_FUNC_SHIFT
                if fct == FCT_FILL:
                    self.state = FILL
                    self.csr |= RXCS_TR
                elif fct == FCT_EMPTY:
                    self.state = EMPTY
                    self._clock.schedule(TR_CYCLES, self._svc)
                elif fct in (FCT_READ, FCT_WRITE, FCT_WRDEL):
                    self.state = RWDS
                    self.csr |= RXCS_TR
                    self.esr &= RXES_ID               # clear errors
                else:                                 # RXES / ECODE
                    self.state = CMD_COMPLETE
                    self._clock.schedule(CMD_CYCLES, self._svc)
                return
            self.csr = (self.csr & ~RXCS_RW) | (v & RXCS_RW)   # IE only
        elif reg == RXDB:
            self.dbr = v & 0o377
            if self.state not in (IDLE, EMPTY):
                self.csr &= ~RXCS_TR
                self._clock.schedule(TR_CYCLES, self._svc)

    def read_byte(self, offset: int) -> int:
        if offset & 0o177776 == RXDB:
            return self.read_word(RXDB) & 0o377
        word = self.read_word(offset & 0o177776)
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        if offset & 0o177776 == RXDB:
            self.write_word(RXDB, value & 0o377)
            return
        reg = offset & 0o177776
        cur = self.read_word(reg)
        if offset % 2 == 0:
            self.write_word(reg, (cur & 0o177400) | (value & 0o377))
        else:
            self.write_word(reg, (cur & 0o000377) | ((value & 0o377) << 8))

    # -- state machine (scheduled events, §5.6) -----------------------------------
    def _svc(self) -> None:
        """One scheduled step of the transfer state machine (SIMH rx_svc)."""
        fct = (self.csr & RXCS_FUNC_MASK) >> RXCS_FUNC_SHIFT
        if self.state == FILL:
            self.buf[self.bptr] = self.dbr & 0xFF
            self.bptr += 1
            if self.bptr < SECTOR_BYTES:
                self.csr |= RXCS_TR
            else:
                self._done(0, 0)
        elif self.state == EMPTY:
            if self.bptr >= SECTOR_BYTES:
                self._done(0, 0)
            else:
                self.dbr = self.buf[self.bptr]
                self.bptr += 1
                self.csr |= RXCS_TR
        elif self.state == RWDS:
            self._sector = self.dbr & 0o177
            self.csr |= RXCS_TR
            self.state = RWDT
        elif self.state == RWDT:
            self._track = self.dbr & 0o377
            self.state = RWXFR
            self._clock.schedule(SEEK_PER_TRACK * abs(self._track - self.track), self._svc)
        elif self.state == RWXFR:
            self._rw_sector(fct)
        elif self.state == CMD_COMPLETE:
            if fct == FCT_ECODE:
                self.dbr = self.ecode
                self._done(0, -1)                     # don't update ecode
            else:
                self._done(0, 0)
        elif self.state == INIT_COMPLETE:
            self.track = 1
            self._read_container_sector(1, 1)
            self._done(RXES_ID, 0)

    def _rw_sector(self, fct: int) -> None:
        """RWXFR: validate and perform the sector read/write on the pack."""
        if self.image is None:
            self._done(0, ECODE_UNATTACHED)
            return
        if self._track >= RX01_TRACKS:
            self._done(0, ECODE_BAD_TRACK)
            return
        if self._sector == 0 or self._sector > RX01_SECTORS_PER_TRACK:
            self._done(0, ECODE_BAD_SECTOR)
            return
        self.track = self._track
        if fct == FCT_WRDEL:
            self.esr |= RXES_DD
        if fct == FCT_READ:
            self._read_container_sector(self._track, self._sector)
        else:                                          # WRITE / WRDEL
            if not self.writable:
                self._done(RXES_WLK, ECODE_WLOCK)
                return
            self._write_container_sector(self._track, self._sector)
        self._done(0, 0)

    def _container_sector(self, track: int, sector: int) -> int:
        """RX01 sectors are 1-26; the raw container stream is 0-based."""
        return track * RX01_SECTORS_PER_TRACK + (sector - 1)

    def _read_container_sector(self, track: int, sector: int) -> None:
        assert self.image is not None
        self.buf = bytearray(self.image.read_sector(self._container_sector(track, sector)))

    def _write_container_sector(self, track: int, sector: int) -> None:
        assert self.image is not None
        self.image.write_sector(self._container_sector(track, sector), bytes(self.buf))
        self.image.flush()

    def _done(self, esr_flags: int, new_ecode: int) -> None:
        """Command complete: DONE set (BR5/264 request asserts while DONE & IE),
        RXDB <- RXES, back to IDLE."""
        self.state = IDLE
        self.csr |= RXCS_DONE
        self.esr = (self.esr | esr_flags) & ~RXES_DRDY
        if self.image is not None:
            self.esr |= RXES_DRDY
        if new_ecode > 0:
            self.csr |= RXCS_ERR
        if new_ecode >= 0:
            self.ecode = new_ecode
        self.dbr = self.esr & 0o377

    # -- interrupts (§6.1: polled by the bus arbiter) --------------------------------
    def interrupt_pending(self) -> tuple[int, int] | None:
        if (self.csr & RXCS_DONE) and (self.csr & RXCS_IE):
            return (VECTOR, BR_LEVEL)
        return None

    # -- snapshot contract (§15) ---------------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {"base": self.base, "csr": self.csr, "dbr": self.dbr, "esr": self.esr,
                "ecode": self.ecode, "state": self.state, "bptr": self.bptr,
                "track": self.track, "buf": bytes(self.buf).hex(),
                "writable": self.writable,
                "image_path": self.image.path if self.image else None}

    def deserialize(self, state: dict[str, Any]) -> None:
        keys = ("base", "csr", "dbr", "esr", "ecode", "state", "bptr", "track",
                "buf", "writable", "image_path")
        for key in keys:
            if key not in state:
                raise ValueError(f"RX11 snapshot missing {key!r}")
        self.base = state["base"]
        self.csr = state["csr"]
        self.dbr = state["dbr"]
        self.esr = state["esr"]
        self.ecode = state["ecode"]
        self.state = state["state"]
        self.bptr = state["bptr"]
        self.track = state["track"]
        self.buf = bytearray(bytes.fromhex(state["buf"]))
        self.writable = state["writable"]
