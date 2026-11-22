"""
Massbus (spec §6.1) — 11/70 only: the RH70 controller translates Unibus
program I/O into Massbus register space and drives RP04/RP06/RP07/RM03
units beneath it (devices/rh70_rp.py carries the device side).

M5 stub per §19.10.

References: PDP-11/70 Processor Handbook Massbus chapter (EK-1170B-MM-001);
RH70/RP06 manuals (§21).
"""

from __future__ import annotations


def _massbus_not_implemented() -> None:
    """Massbus (RH70 + RP/RM drives) lands in M5 (§12)."""
    raise NotImplementedError("§6.1 Massbus / RH70 — M5 (11/70)")
