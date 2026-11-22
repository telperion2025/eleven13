"""
Bus map registers (spec §3 layout): the 11/70 Unibus Map (22-bit physical
addressing from 16-bit virtual, §5.7/§13) and the 11/94 Q22-CD map
registers.

M1 stub per §19.10: flat addressing only — the 11/20 has no map at all, and
the 11/03/70/94 profiles reach this module only from M4-M6, gated by
profile.physical_bits (never by a profile.key check, §19.1).

References: PDP-11/70 Processor Handbook Unibus Map chapter; DCJ-11 (J-11)
Q22-CD documentation (§21).
"""

from __future__ import annotations


def _map_not_implemented() -> None:
    """Map registers are not active in M1 (flat addressing, §12 M1)."""
    raise NotImplementedError("§5.7/§13 Unibus Map & Q22-CD map — M5 (11/70) / M6 (11/94)")
