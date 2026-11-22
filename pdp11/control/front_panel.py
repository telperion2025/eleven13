"""
Front panel / ODT console (spec §9, §13): on HALT, control falls to ODT —
the classic `@` prompt behaving like the real M9312/KD11-F console: `/`
examine, `\\` deposit, `G` go, octal addresses only.

Panel semantics (§13): RUN and HALT are external two-state events —
BREAK-to-ODT on the 11/03/94, halt-switch on the 11/70 console, manual
deposit on the 11/20's console switches. Host-side BREAK/clean-restart is
surfaced here, never by stopping mid-cycle or corrupting machine state.

M2 stub per §19.10.
"""

from __future__ import annotations


def _odt_not_implemented() -> None:
    """ODT shell + RUN/HALT panel events land in M2 (§12, §13)."""
    raise NotImplementedError("§9/§13 ODT front panel — M2")
