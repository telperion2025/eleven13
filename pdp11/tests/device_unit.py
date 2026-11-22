"""
Device register/state machine unit tests (spec §3 layout), PRNG-seeded.

M1 placeholder per §19.10: no devices are implemented yet (the P0 boot set
lands in M2-M3). The first entries will be DL11/KW11-L register tests in M2
and RK11/RK05 transfer tests in M3, including the reference "boot RT-11 from
RK05" transcript (§7).
"""

from __future__ import annotations


def _device_tests_pending() -> None:
    """Device unit tests arrive with the device milestones (§12: M2-M6)."""
    raise NotImplementedError("§3/§7 device_unit.py — M2 (DL11/KW11-L) onward")
