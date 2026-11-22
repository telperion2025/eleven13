"""
TK50/TU81 tape via TQK11 (Q-Bus) (spec §7, P2; 11/94).

Reads/writes SIMH .tap containers byte-compatibly like tm11.py (§16).

M6 stub per §19.10.

References: PDP-11 Peripherals Handbook (TQK11/TK50), SIMH .tap format (§21).
"""

from __future__ import annotations


class TQK11:
    """TQK11 Q-Bus tape controller (TK50/TU81). M6."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 TK50/TU81 via TQK11 — M6 (11/94)")
