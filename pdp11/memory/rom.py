"""
Boot/diagnostic ROM (M9312-style boot PROMs, spec §3 layout; §13).

M1 provides the ROM *block* (read-only, byte/word access like RAM); loading
512-word M9312 PROM images from a file is control/rom_loader.py's job and
lands with the boot work in M2/M3 (§13, §22.2). Writes to ROM are ignored,
as on real PROMs.

References: M9312 bootstrap/terminator module documentation (§21).
"""

from __future__ import annotations

from array import array
from typing import Any

from pdp11.bus.abstract_bus import BusDevice


class ROM(BusDevice):
    """A read-only block of `words` 16-bit words holding a boot PROM image."""

    name = "ROM"

    def __init__(self, words: int) -> None:
        self.window_size = words * 2
        self.data: array[int] = array("H", bytes(words * 2))

    def load_image(self, words_list: list[int]) -> None:
        """Load a PROM image (list of 16-bit words) into the block; a longer
        image than the block raises ValueError (wiring error, caught at boot)."""
        if len(words_list) > len(self.data):
            raise ValueError(f"ROM image too large: {len(words_list)} words > {len(self.data)}")
        for i, w in enumerate(words_list):
            self.data[i] = w & 0o177777

    def read_word(self, offset: int) -> int:
        """Read the 16-bit word at byte `offset`."""
        return self.data[offset >> 1]

    def write_word(self, offset: int, value: int) -> None:
        """PROMs are read-only: writes are ignored, never an error."""

    def read_byte(self, offset: int) -> int:
        """Read one byte: low byte at even offset, high byte at odd."""
        word = self.data[offset >> 1]
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        """PROMs are read-only: writes are ignored."""

    def serialize(self) -> dict[str, Any]:
        """Full ROM contents as a JSON-safe dict (§15)."""
        return {"words": len(self.data), "data": list(self.data)}

    def deserialize(self, state: dict[str, Any]) -> None:
        """Restore ROM contents; a word-count mismatch is a schema error."""
        if state.get("words") != len(self.data):
            raise ValueError("ROM snapshot word count does not match this machine")
        self.data = array("H", state["data"])
