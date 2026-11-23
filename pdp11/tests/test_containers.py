"""
Disk container tests (spec §16) — M3: raw RK05/RX01 images are read and
written losslessly, and the format is sniffed from the file size.

The §16 compatibility rule — "if SIMH can read it, this emulator can too"
— is tested as a byte-exact round-trip: write sectors, flush, reopen the
file from disk, read them back unchanged.
"""

from __future__ import annotations

import os

import pytest

from pdp11.disk_images.container import (
    RK05_SECTOR,
    RK05_SECTORS,
    RX01_SECTOR,
    RX01_SECTORS,
    open_disk_image,
    open_rk05,
    open_rx01,
    rk05_sector_address,
)


def test_blank_rk05_created_when_missing(tmp_path):
    path = str(tmp_path / "new.rk05")
    img = open_rk05(path)
    assert os.path.getsize(path) == RK05_SECTOR * RK05_SECTORS
    assert img.read_sector(0) == bytes(RK05_SECTOR)
    assert img.read_sector(RK05_SECTORS - 1) == bytes(RK05_SECTOR)


def test_rk05_sector_roundtrip_lossless(tmp_path):
    path = str(tmp_path / "pack.rk05")
    img = open_rk05(path)
    payload = bytes(range(256)) * 2                     # exactly 512 bytes
    img.write_sector(0, payload)
    img.write_sector(RK05_SECTORS - 1, payload[::-1])
    img.flush()
    again = open_rk05(path)                             # reopen from disk: lossless
    assert again.read_sector(0) == payload
    assert again.read_sector(RK05_SECTORS - 1) == payload[::-1]
    assert again.read_sector(1) == bytes(RK05_SECTOR)   # untouched sectors stay zero


def test_container_rejects_wrong_size(tmp_path):
    path = tmp_path / "bad.rk05"
    path.write_bytes(b"way too small")
    with pytest.raises(ValueError, match="wrong container format"):
        open_rk05(str(path))


def test_sector_bounds_and_payload_size_checked(tmp_path):
    img = open_rk05(str(tmp_path / "p.rk05"))
    with pytest.raises(ValueError, match="out of range"):
        img.read_sector(RK05_SECTORS)
    with pytest.raises(ValueError, match="out of range"):
        img.write_sector(-1, bytes(RK05_SECTOR))
    with pytest.raises(ValueError, match="payload"):
        img.write_sector(0, b"short")


def test_rk05_geometry_addresses():
    """§16 geometry: 2 surfaces x 203 cylinders x 12 sectors, raw order."""
    assert rk05_sector_address(0, 0, 0) == 0
    assert rk05_sector_address(0, 0, 11) == 11
    assert rk05_sector_address(0, 1, 0) == 12           # surface 1 follows surface 0
    assert rk05_sector_address(1, 0, 0) == 24           # cylinder 1 follows both
    assert rk05_sector_address(202, 1, 11) == 4871      # last geometry-addressable
    with pytest.raises(ValueError, match="bad RK05 address"):
        rk05_sector_address(203, 0, 0)                 # cylinders are 0..202
    with pytest.raises(ValueError, match="bad RK05 address"):
        rk05_sector_address(0, 0, 12)                  # sectors are 0..11


def test_rx01_container_roundtrip(tmp_path):
    path = str(tmp_path / "floppy.rx01")
    img = open_rx01(path)
    assert img.sector_count == RX01_SECTORS == 77 * 26
    assert img.sector_size == RX01_SECTOR == 128
    payload = bytes((i * 7) & 0xFF for i in range(RX01_SECTOR))
    img.write_sector(0, payload)
    img.flush()
    assert open_rx01(path).read_sector(0) == payload


def test_open_disk_image_sniffs_format_by_size(tmp_path):
    rk = tmp_path / "a.rk05"
    rk.write_bytes(bytes(RK05_SECTOR * RK05_SECTORS))
    assert open_disk_image(str(rk)).sector_size == RK05_SECTOR
    rx = tmp_path / "b.rx01"
    rx.write_bytes(bytes(RX01_SECTOR * RX01_SECTORS))
    assert open_disk_image(str(rx)).sector_size == RX01_SECTOR   # M4: RX11 binds it
    odd = tmp_path / "c.bin"
    odd.write_bytes(b"\x00" * 1234)
    with pytest.raises(ValueError, match="no supported container"):
        open_disk_image(str(odd))
    blank = open_disk_image(str(tmp_path / "missing.rk05"))   # missing -> blank RK05
    assert blank.sector_size == RK05_SECTOR
    assert blank.sector_count == RK05_SECTORS
