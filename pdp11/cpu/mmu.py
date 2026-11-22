"""
MMU — 11/70 and 11/94 only (spec §5.7): PAR/PDR pairs (8 APRs per mode,
3 modes: kernel/supervisor/user), MMR0-3 diagnostic registers, I/D space
separation via MMR0 bit 3, 22-bit physical addressing through the Unibus
Map (memory/map_registers.py) on the 11/70 or the Q22-CD map on the 11/94.

M1 stub per §19.10: flat 16-bit addressing only; the MemoryInterface in
board.py switches to MMU-mapped mode here in M5/M6, gated by
profile.has_mmu (never by a profile.key check, §19.1).

References: PDP-11/70 Processor Handbook (MMU chapter, EK-1170B-MM-001);
DCJ-11 (J-11) documentation set (§21).
"""

from __future__ import annotations


def _mmu_not_implemented() -> None:
    """MMU translation is not active in M1 (flat 16-bit mode, §12 M1)."""
    raise NotImplementedError(
        "§5.7 MMU (PAR/PDR, MMR0-3, I/D separation) — M5/M6")
