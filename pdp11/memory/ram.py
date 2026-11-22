"""
Byte/word-addressable RAM blocks (spec §3 layout).

The PDP-11 bus is little-endian at word granularity: the LOW byte of a word
lives at the EVEN address, the high byte at the odd address — byte access
to either half is legal everywhere (§6.2). One bus cycle per word or byte
transfer (§5.6); the board counts them via cycles_used.

Speed class per machine (11/70 cache, LSI-11, etc.) lands with M4-M6 as a
profile-driven timing field; M1 has a single flat speed class.
"""

from __future__ import annotations

from array import array
from typing import Any

from pdp11.bus.abstract_bus import BusDevice


class RAM(BusDevice):
    """A flat RAM block of `words` 16-bit words, zeroed at power-on (§13:
    memory initial content is part of the power-on state, except in
    diagnostic RAM-test mode)."""

    name = "RAM"

    def __init__(self, words: int) -> None:
        self.window_size = words * 2            # window size in BYTES (§10)
        self.data: array[int] = array("H", bytes(words * 2))  # preallocated, zeroed

    # -- bus-facing ops: `offset` is a byte offset into this block ---------
    def read_word(self, offset: int) -> int:
        """Read the 16-bit word at byte `offset` (must be even — the board
        enforces that before routing here)."""
        return self.data[offset >> 1]

    def write_word(self, offset: int, value: int) -> None:
        """Write a 16-bit word at byte `offset`."""
        self.data[offset >> 1] = value & 0o177777

    def read_byte(self, offset: int) -> int:
        """Read one byte: low byte at even offset, high byte at odd (§6.2)."""
        word = self.data[offset >> 1]
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        """Write one byte into the low (even) or high (odd) half of a word."""
        idx = offset >> 1
        if offset % 2 == 0:
            self.data[idx] = (self.data[idx] & 0o177400) | (value & 0o377)
        else:
            self.data[idx] = (self.data[idx] & 0o000377) | ((value & 0o377) << 8)

    # -- snapshot contract (§15) -------------------------------------------
    def serialize(self) -> dict[str, Any]:
        """Full RAM contents as a JSON-safe dict (compressed at the snapshot
        layer in M8; M1 keeps the plain list)."""
        return {"words": len(self.data), "data": list(self.data)}

    def deserialize(self, state: dict[str, Any]) -> None:
        """Restore RAM contents; a word-count mismatch is a schema error."""
        if state.get("words") != len(self.data):
            raise ValueError("RAM snapshot word count does not match this machine")
        self.data = array("H", state["data"])
