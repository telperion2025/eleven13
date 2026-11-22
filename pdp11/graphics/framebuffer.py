"""
Optional VT11-class vector graphics device (spec §3 layout, §8): a pixel
framebuffer for the pixel render backend. Out of v1's console scope; the
module exists so the optional pixel backend (pygame/tkinter, [pixel] extra)
has a target.

M7 stub per §19.10.
"""

from __future__ import annotations


def _framebuffer_not_implemented() -> None:
    """VT11-class vector graphics lands after v1 console scope (§8)."""
    raise NotImplementedError("§8 VT11 framebuffer — post-v1 / M7+")
