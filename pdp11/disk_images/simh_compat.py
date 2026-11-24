"""
SIMH compatibility utilities (spec §16) — M8.

The v1 hard requirement (byte-compatible .tap/.pt read/write) lives in
container.py (`TapImage`, `PtImage`, `parse_tap`/`frame_tap`). This module
carries the utilitarian exporters/importers for working with those containers
outside the emulator:

- `tap_to_raw(data)` — concatenate a .tap image's record payloads into one
  raw byte stream, dropping tape marks (a "dump-style" flattening).
- `raw_to_tap(payload)` — frame a raw byte stream as a single-record .tap.

Both are byte-exact inverses of each other for mark-free tapes and are
round-trip tested (§11.10). A cross-check against a real SIMH-created .tap
file awaits the operator (§22.1); the framing itself is pinned to the §16
text. Formats beyond the v1 core set (other dump-style images) remain out of
v1 scope per §16.
"""

from __future__ import annotations

from pdp11.disk_images.container import frame_tap, frame_tap_record, parse_tap


def tap_to_raw(data: bytes) -> bytes:
    """Flatten a .tap image to its raw record payloads (tape marks dropped)."""
    out = bytearray()
    for rec in parse_tap(data):
        if rec is not None:
            out += rec
    return bytes(out)


def raw_to_tap(payload: bytes) -> bytes:
    """Frame a raw byte stream as a one-record .tap image (with EOF mark)."""
    return frame_tap([payload]) if payload else frame_tap([])


def tap_records(data: bytes) -> list[bytes]:
    """The record payloads of a .tap image as a plain list (marks dropped)."""
    return [rec for rec in parse_tap(data) if rec is not None]


__all__ = ["tap_to_raw", "raw_to_tap", "tap_records", "frame_tap", "frame_tap_record"]
