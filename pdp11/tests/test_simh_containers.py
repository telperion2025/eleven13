"""
SIMH container round-trip tests (spec §16, §11.10) — M8: the .tap framing is
pinned to the §16 text (LE32 length + payload + LE32 length; bare zero = tape
mark/EOF; end of file = EOM) and round-trips byte-identically. A cross-check
against a real SIMH-created .tap file awaits the operator (§22.1).
"""

from __future__ import annotations

import struct

import pytest

from pdp11.disk_images import simh_compat
from pdp11.disk_images.container import (
    TAP_EOF_MARK,
    PtImage,
    TapImage,
    frame_tap,
    parse_tap,
)


def handbuilt_tap() -> bytes:
    """A .tap image built by hand exactly per the §16 framing rules."""
    return (
        struct.pack("<I", 4) + b"BOOT" + struct.pack("<I", 4)   # record "BOOT"
        + TAP_EOF_MARK                                           # tape mark
        + struct.pack("<I", 2) + b"XY" + struct.pack("<I", 2)   # record "XY"
        + TAP_EOF_MARK                                           # EOF mark
    )


def test_parse_handbuilt_simh_framing():
    assert parse_tap(handbuilt_tap()) == [b"BOOT", None, b"XY"]


def test_tap_roundtrip_byte_identical(tmp_path):
    recs: list[bytes | None] = [b"hello", None, bytes(range(256))]
    path = str(tmp_path / "t.tap")
    img = TapImage(path, recs)
    img.flush()
    raw = open(path, "rb").read()
    img2 = TapImage(path)                       # re-read from disk
    assert img2.records == recs
    img2.flush()
    assert open(path, "rb").read() == raw       # rewrite is byte-identical (§11.10)


def test_tap_read_cursor_and_eom(tmp_path):
    path = str(tmp_path / "t.tap")
    img = TapImage(path, [b"AA", None, b"BB"])
    img.flush()
    img2 = TapImage(path)
    assert img2.read_record() == b"AA"
    assert img2.read_record() is None           # tape mark
    assert img2.read_record() == b"BB"
    assert img2.read_record() == -1             # EOM
    img2.rewind()
    assert img2.read_record() == b"AA"


def test_tap_corrupt_trailer_rejected():
    bad = struct.pack("<I", 4) + b"BOOT" + struct.pack("<I", 5) + TAP_EOF_MARK
    with pytest.raises(ValueError, match="trailer"):
        parse_tap(bad)
    with pytest.raises(ValueError, match="truncated"):
        parse_tap(struct.pack("<I", 100) + b"short")


def test_pt_raw_roundtrip(tmp_path):
    path = str(tmp_path / "boot.pt")
    payload = bytes((i * 7) & 0xFF for i in range(1024))
    img = PtImage(path, payload)
    img.flush()
    assert PtImage(path).data == bytearray(payload)
    assert open(path, "rb").read() == payload


def test_simh_compat_flatten_roundtrip():
    data = handbuilt_tap()
    assert simh_compat.tap_to_raw(data) == b"BOOTXY"       # marks dropped
    assert simh_compat.tap_records(data) == [b"BOOT", b"XY"]
    assert simh_compat.raw_to_tap(b"ABCD") == frame_tap([b"ABCD"])
    # flatten then reframe is lossless for mark-free tapes
    flat = simh_compat.tap_to_raw(data)
    assert parse_tap(simh_compat.raw_to_tap(flat)) == [b"BOOTXY"]
