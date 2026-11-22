"""
Boot ROM loader (spec §9, §13): loads M9312 512-word boot PROM images so
any of the four machines can run real DEC boot ROMs (173000-series: XXDP,
TU tape boot, RP/RM disk boot). --bootrom <file> supplies the image; without
it the emulator falls back to a simplified built-in bootstrap, clearly
documented as non-authentic (§13).

M2/M3 stub per §19.10.

References: M9312 bootstrap/terminator module documentation (§21).
"""

from __future__ import annotations


def _rom_loader_not_implemented() -> None:
    """M9312 PROM loading lands in M2/M3 (§12, §13)."""
    raise NotImplementedError("§13/§9 rom_loader (M9312 512-word PROMs) — M2/M3")
