"""
Disk/tape container formats, read-and-written losslessly (spec §16) — no
internal conversion to an emulator-private format: if SIMH can read it,
this emulator can too.

M3 implements the raw disk containers:

| Format      | Bytes/sector | Layout                                        |
|-------------|--------------|-----------------------------------------------|
| RK05 (SIMH) | 512          | raw sector stream, 4879 sectors, no header    |
| RX01        | 128          | raw, 77 tracks x 26 sectors                   |

(SIMH .tap magtape and PC11 .pt land with their device milestones M8;
see simh_compat.py for legacy importers.)

M5 adds the Massbus RP06/RP07 containers (RH70, §16): raw 512-byte sector
streams, no header — geometry per the RH70/RP06 handbook and SIMH
pdp11_rp.c (verified):
    RP06: 815 cylinders x 19 surfaces x 22 sectors  = 340670 sectors (~166 MB)
    RP07: 630 cylinders x 32 surfaces x 43 sectors  = 866880 sectors (~424 MB)
The ".hp"-style header mentioned in §16 is a VAX/SIMH-hp artifact, not used
by SIMH's PDP-11 RP containers — flagged in docs/acceptance-checklist.md.

RK05 geometry (spec §16): 2 surfaces x 203 cylinders x 12 sectors of 512
bytes, raw stream, 4879 sectors total.
"""

from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)

# Geometry constants — decimal byte/sector counts are off-metal (§10.1),
# sector counts are octal where the spec states them octal.
RK05_SECTOR = 512
RK05_SECTORS = 4879                 # 2 surfaces x 203 cylinders x 12 sectors (spec §16)
RK05_SURFACES = 2
RK05_CYLINDERS = 203
RK05_SECTORS_PER_TRACK = 12

RX01_SECTOR = 128
RX01_TRACKS = 77
RX01_SECTORS_PER_TRACK = 26
RX01_SECTORS = RX01_TRACKS * RX01_SECTORS_PER_TRACK

# -- RP06/RP07 Massbus containers (RH70, M5; SIMH pdp11_rp.c geometry) --------
RP_SECTOR = 512
RP06_CYLINDERS = 815
RP06_SURFACES = 19
RP06_SECTORS_PER_TRACK = 22
RP06_SECTORS = RP06_CYLINDERS * RP06_SURFACES * RP06_SECTORS_PER_TRACK   # 340670
RP07_CYLINDERS = 630
RP07_SURFACES = 32
RP07_SECTORS_PER_TRACK = 43
RP07_SECTORS = RP07_CYLINDERS * RP07_SURFACES * RP07_SECTORS_PER_TRACK   # 866880


class RawDiskImage:
    """A raw fixed-sector disk image file, read/written losslessly (§16)."""

    def __init__(self, path: str, sector_size: int, sector_count: int) -> None:
        self.path = path
        self.sector_size = sector_size
        self.sector_count = sector_count
        self.data = bytearray()
        if os.path.exists(path):
            with open(path, "rb") as fh:
                self.data = bytearray(fh.read())
            expected = sector_size * sector_count
            if len(self.data) != expected:
                raise ValueError(
                    f"{path}: expected {expected} bytes ({sector_count} x {sector_size}), "
                    f"got {len(self.data)} — wrong container format? (§16)")
        else:
            self.data = bytearray(sector_size * sector_count)   # blank pack
            self.flush()
        _log.info("container %s: %d sectors x %d bytes", path, sector_count, sector_size)

    def read_sector(self, n: int) -> bytes:
        """Read sector n (0-based) as bytes."""
        if not 0 <= n < self.sector_count:
            raise ValueError(f"sector {n} out of range (0..{self.sector_count - 1})")
        off = n * self.sector_size
        return bytes(self.data[off:off + self.sector_size])

    def write_sector(self, n: int, payload: bytes) -> None:
        """Write sector n; payload must be exactly one sector."""
        if not 0 <= n < self.sector_count:
            raise ValueError(f"sector {n} out of range (0..{self.sector_count - 1})")
        if len(payload) != self.sector_size:
            raise ValueError(f"sector payload must be {self.sector_size} bytes")
        off = n * self.sector_size
        self.data[off:off + self.sector_size] = payload

    def flush(self) -> None:
        """Write the image back to disk, losslessly (raw bytes, §16)."""
        with open(self.path, "wb") as fh:
            fh.write(self.data)


def open_rk05(path: str) -> RawDiskImage:
    """Open an RK05 (SIMH) container: raw 512-byte sectors, 4879 of them."""
    return RawDiskImage(path, RK05_SECTOR, RK05_SECTORS)


def open_rx01(path: str) -> RawDiskImage:
    """Open an RX01 container: raw 128-byte sectors, 77 x 26."""
    return RawDiskImage(path, RX01_SECTOR, RX01_SECTORS)


def open_rp06(path: str) -> RawDiskImage:
    """Open an RP06 (SIMH) container: raw 512-byte sectors, 815 x 19 x 22."""
    return RawDiskImage(path, RP_SECTOR, RP06_SECTORS)


def open_rp07(path: str) -> RawDiskImage:
    """Open an RP07 (SIMH) container: raw 512-byte sectors, 630 x 32 x 43."""
    return RawDiskImage(path, RP_SECTOR, RP07_SECTORS)


def rp_sector_address(cylinder: int, surface: int, sector: int, *,
                      cylinders: int, surfaces: int, sectors_per_track: int) -> int:
    """RP logical sector number from (cylinder, surface, sector) — SIMH
    GET_DA: ((cyl * surfaces) + surf) * sect + sc."""
    if not (0 <= cylinder < cylinders and 0 <= surface < surfaces
            and 0 <= sector < sectors_per_track):
        raise ValueError(
            f"bad RP address cyl={cylinder} surf={surface} sect={sector}")
    return ((cylinder * surfaces) + surface) * sectors_per_track + sector


def rk05_sector_address(cylinder: int, surface: int, sector: int) -> int:
    """RK05 logical sector number from (cylinder, surface, sector) — geometry
    per spec §16: 2 surfaces x 203 cylinders x 12 sectors."""
    if not (0 <= cylinder < RK05_CYLINDERS and 0 <= surface < RK05_SURFACES
            and 0 <= sector < RK05_SECTORS_PER_TRACK):
        raise ValueError(f"bad RK05 address cyl={cylinder} surf={surface} sect={sector}")
    return (cylinder * RK05_SURFACES + surface) * RK05_SECTORS_PER_TRACK + sector


def open_disk_image(path: str) -> RawDiskImage:
    """Open a disk container, detecting the format from the file size (§16)
    and reading it losslessly. A missing file becomes a blank RK05 pack
    (the boot disk). M4: main.py dispatches RK05-sized containers to the
    RK11 and RX01-sized ones to the RX11 (§11.2)."""
    if not os.path.exists(path):
        _log.info("container %s not found — creating a blank RK05 pack", path)
        return open_rk05(path)
    size = os.path.getsize(path)
    if size == RK05_SECTOR * RK05_SECTORS:
        return open_rk05(path)
    if size == RX01_SECTOR * RX01_SECTORS:
        return open_rx01(path)
    if size == RP_SECTOR * RP06_SECTORS:
        return open_rp06(path)
    if size == RP_SECTOR * RP07_SECTORS:
        return open_rp07(path)
    raise ValueError(
        f"{path}: {size} bytes matches no supported container format (§16: "
        f"RK05={RK05_SECTOR * RK05_SECTORS}, RX01={RX01_SECTOR * RX01_SECTORS}, "
        f"RP06={RP_SECTOR * RP06_SECTORS}, RP07={RP_SECTOR * RP07_SECTORS})")
