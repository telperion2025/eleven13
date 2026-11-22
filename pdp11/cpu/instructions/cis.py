"""
CIS — Commercial Instruction Set (spec §3 layout): LOCC, SKPC, MOVC/MOVRC/
MOVTC, MATCHC, SCANC, CMPC string instructions (J-11 on-chip on the 11/94;
KH11 option on the 11/70).

M1 registers no CIS opcodes: they trap as reserved on every profile until
M6 lands the decoder (§19.10 wiring restraint — a stub that silently did
nothing would be silently-wrong behavior). When implemented, entries will
carry caps="CIS" so the §5.4 capability matrix gates them on the 11/03/20.

References: PDP-11 Commercial Instruction Set manual; DCJ-11 (J-11) docs (§21).
"""

from __future__ import annotations


def _cis_not_implemented() -> None:
    """CIS opcodes are not decoded in M1; see module docstring."""
    raise NotImplementedError("§5.4 CIS (LOCC/SKPC/MOVC string ops) — M6 (11/94 J-11)")
