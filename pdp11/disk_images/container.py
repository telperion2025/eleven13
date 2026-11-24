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
import struct

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


# -- RL01/RL02 (RL11, §7 P1; SIMH pdp11_rl.c geometry) ---------------------------
# 256-byte sectors, 40 sectors/surface, 2 surfaces/cylinder;
# RL01 = 256 cylinders, RL02 = 512.
RL_SECTOR = 256
RL_NUMSC = 40                   # sectors per surface
RL_NUMSF = 2                    # surfaces per cylinder
RL01_NUMCYL = 256
RL02_NUMCYL = 512
RL01_SECTORS = RL01_NUMCYL * RL_NUMSF * RL_NUMSC       # 20480 (5,242,880 bytes)
RL02_SECTORS = RL02_NUMCYL * RL_NUMSF * RL_NUMSC       # 40960 (10,485,760 bytes)


def rl_sector_address(cylinder: int, surface: int, sector: int) -> int:
    """RL logical sector from (cylinder, surface/head, sector) — SIMH GET_DA:
    track = cyl*2 + head; da = track * 40 + sect."""
    if not (0 <= cylinder and 0 <= surface < RL_NUMSF and 0 <= sector < RL_NUMSC):
        raise ValueError(f"bad RL address cyl={cylinder} surf={surface} sect={sector}")
    return (cylinder * RL_NUMSF + surface) * RL_NUMSC + sector


class RawDiskImage:
    """A raw fixed-sector disk image file, read/written losslessly (§16)."""

    def __init__(self, path: str, sector_size: int, sector_count: int,
                 pad_short: bool = False) -> None:
        self.path = path
        self.sector_size = sector_size
        self.sector_count = sector_count
        self.data = bytearray()
        if os.path.exists(path):
            with open(path, "rb") as fh:
                self.data = bytearray(fh.read())
            expected = sector_size * sector_count
            if len(self.data) != expected:
                # SIMH AUTOSIZE behavior for RL packs: a shorter file is a
                # valid (smaller) pack zero-padded to the drive capacity —
                # RT-11 file-structured RL images come in any size.
                if pad_short and len(self.data) < expected \
                        and len(self.data) % sector_size == 0:
                    self.data.extend(bytes(expected - len(self.data)))
                else:
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


def open_rl(path: str) -> RawDiskImage:
    """Open an RL01/RL02 container: raw 256-byte sectors. Exact RL01/RL02
    sizes pick the drive type; any other 256-aligned size up to RL02
    capacity is autosized to RL02 and zero-padded (SIMH AUTOSIZE, §16)."""
    if not os.path.exists(path):
        _log.info("container %s not found — creating a blank RL01 pack", path)
        return RawDiskImage(path, RL_SECTOR, RL01_SECTORS)
    size = os.path.getsize(path)
    if size == RL_SECTOR * RL01_SECTORS:
        return RawDiskImage(path, RL_SECTOR, RL01_SECTORS)
    if size % RL_SECTOR == 0 and size <= RL_SECTOR * RL02_SECTORS:
        return RawDiskImage(path, RL_SECTOR, RL02_SECTORS, pad_short=True)
    raise ValueError(
        f"{path}: {size} bytes is not an RL01/RL02 container (§16: "
        f"RL01={RL_SECTOR * RL01_SECTORS}, RL02<={RL_SECTOR * RL02_SECTORS}, "
        f"256-byte aligned)")


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


# -- SIMH magtape (.tap) and PC11 paper tape (.pt) containers (M8, §16) --------
#
# SIMH .tap framing (spec §16, hard requirement — tm11/tqk11 must read/write
# SIMH-created .tap byte-compatibly): each record is a 32-bit LITTLE-ENDIAN
# length header, the raw record bytes, then a trailing 32-bit length (verified
# equal on read). A bare 32-bit zero is a tape mark (EOF marker; it has NO
# trailing length). Physical end of file after the last record/marks is EOM.
# A real SIMH-created .tap cross-check awaits the operator (§22.1); the framing
# here is pinned to the §16 text and round-trip tested byte-exactly.

TAP_MARK: bytes | None = None   # tape mark = None entry in TapImage.records


def frame_tap_record(record: bytes) -> bytes:
    """Frame one .tap record: LE32 length + payload + LE32 length (§16)."""
    n = len(record)
    return struct.pack("<I", n) + record + struct.pack("<I", n)


TAP_EOF_MARK = struct.pack("<I", 0)   # bare zero length: EOF / tape mark (§16)


def parse_tap(data: bytes) -> list[bytes | None]:
    """Parse .tap bytes into records; None entries are tape marks. The final
    bare zero (if present) is the EOF mark and is not returned as a record.
    Malformed framing raises ValueError (user-facing, §18.3 layer 3)."""
    records: list[bytes | None] = []
    pos = 0
    while pos + 4 <= len(data):
        (n,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if n == 0:
            if pos == len(data):
                break                       # trailing EOF mark — not a record
            records.append(TAP_MARK)        # mid-file tape mark (no trailer)
            continue
        if pos + n + 4 > len(data):
            raise ValueError("truncated .tap record (header/payload/trailer mismatch)")
        (trailer,) = struct.unpack_from("<I", data, pos + n)
        if trailer != n:
            raise ValueError(f".tap trailer {trailer} != header {n} (corrupt record)")
        records.append(data[pos:pos + n])
        pos += n + 4
    if pos != len(data):
        raise ValueError("trailing garbage at end of .tap file (not 4-byte aligned)")
    return records


def frame_tap(records: list[bytes | None]) -> bytes:
    """Serialize records to .tap bytes, appending the EOF mark (§16)."""
    out = bytearray()
    for rec in records:
        out += TAP_EOF_MARK if rec is None else frame_tap_record(rec)
    out += TAP_EOF_MARK                     # EOF marker at end of medium
    return bytes(out)


class TapImage:
    """A SIMH .tap magtape container, read/written losslessly (§16, §11.10).
    `records` holds the tape's records in order; tape marks are None entries."""

    def __init__(self, path: str, records: list[bytes | None] | None = None) -> None:
        self.path = path
        self._pos = 0                       # read cursor (record index)
        if records is not None:
            self.records = records
        elif os.path.exists(path):
            with open(path, "rb") as fh:
                self.records = parse_tap(fh.read())
        else:
            self.records = []
            self.flush()
        _log.info("tape container %s: %d records", path, len(self.records))

    def flush(self) -> None:
        """Write the tape back, byte-compatible with the SIMH framing (§16)."""
        with open(self.path, "wb") as fh:
            fh.write(frame_tap(self.records))

    def read_record(self) -> bytes | None | int:
        """Next record from the cursor: bytes, None for a tape mark, -1 at EOM."""
        if self._pos >= len(self.records):
            return -1
        rec = self.records[self._pos]
        self._pos += 1
        return rec

    def space_back(self) -> bytes | None | int:
        """Space one record in reverse: the record just passed (bytes),
        None if it was a tape mark, -1 at BOT."""
        if self._pos == 0:
            return -1
        self._pos -= 1
        return self.records[self._pos]

    def write_record(self, record: bytes) -> None:
        """Append a data record at the cursor (truncating anything after it,
        as a real tape would) and flush, byte-compatible .tap framing."""
        self.records[self._pos:len(self.records)] = [record]
        self._pos += 1
        self.flush()

    def write_mark(self) -> None:
        """Write a tape mark (EOF) at the cursor."""
        self.records[self._pos:len(self.records)] = [TAP_MARK]
        self._pos += 1
        self.flush()

    def rewind(self) -> None:
        """Rewind the cursor to the start of the tape (BOT)."""
        self._pos = 0

    @property
    def bot(self) -> bool:
        """True when the tape is positioned at beginning-of-tape."""
        return self._pos == 0

    @property
    def pos(self) -> int:
        """Current record cursor (debugger/snapshot introspection)."""
        return self._pos

    @pos.setter
    def pos(self, value: int) -> None:
        self._pos = max(0, min(value, len(self.records)))


class PtImage:
    """A PC11 paper tape container: SIMH .pt is a raw byte stream (read-only
    in v1, §16); written back byte-exactly for round-trip tests (§11.10)."""

    def __init__(self, path: str, data: bytes | None = None) -> None:
        self.path = path
        if data is not None:
            self.data = bytearray(data)
        elif os.path.exists(path):
            with open(path, "rb") as fh:
                self.data = bytearray(fh.read())
        else:
            self.data = bytearray()
            self.flush()

    def flush(self) -> None:
        with open(self.path, "wb") as fh:
            fh.write(self.data)


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
        f"{path}: {size} bytes matches no supported container format "
        f"(§16: RK05={RK05_SECTOR * RK05_SECTORS}, RX01={RX01_SECTOR * RX01_SECTORS}, "
        f"RP06={RP_SECTOR * RP06_SECTORS}, RP07={RP_SECTOR * RP07_SECTORS}; "
        f"RL01/RL02 images go via --rl)")
