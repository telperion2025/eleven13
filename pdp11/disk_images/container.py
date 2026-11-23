"""
Disk/tape container formats, read-and-written losslessly (spec §16) — no
internal conversion to an emulator-private format: if SIMH can read it,
this emulator can too.

M3 implements the raw disk containers:

| Format      | Bytes/sector | Layout                                        |
|-------------|--------------|-----------------------------------------------|
| RK05 (SIMH) | 512          | raw sector stream, 4879 sectors, no header    |
| RX01        | 128          | raw, 77 tracks x 26 sectors                   |

(RP06/RP07 raw-with-optional-.hp, SIMH .tap magtape, and PC11 .pt land with
their device milestones M5/M8; see simh_compat.py for legacy importers.)

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
    raise ValueError(
        f"{path}: {size} bytes matches no supported container format (§16: "
        f"RK05={RK05_SECTOR * RK05_SECTORS}, RX01={RX01_SECTOR * RX01_SECTORS})")
