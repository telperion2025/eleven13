"""
Render backends (spec §8): a curses text-grid backend (default; works over
ODT and full-screen use) and an optional pixel backend (pygame/tkinter,
[pixel] extra) for VT11-class vector display.

This module — and main.py only — may import time.* for host-side pacing
(§19.6); the deterministic machine core never does (§5.6).

M7 stub per §19.10.
"""

from __future__ import annotations


def _render_not_implemented() -> None:
    """Render backends (curses text grid, optional pixel) land in M7 (§12)."""
    raise NotImplementedError("§8 RenderBackend (curses + optional pixel) — M7")
