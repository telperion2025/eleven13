"""
DEC MAINDEC diagnostic runs (spec §3 layout, §11.1): each profile must run
the MAINDEC CPU diagnostics to a pass/fail report.

M1 placeholder per §19.10: MAINDEC binaries arrive with the OS-boot
milestones and the SIMH cross-check loop (§22.4); the 11/20's no-op
behavior on microcoded-only opcodes is already covered by cpu_golden.py
(§11.5) and will be cross-checked against a MAINDEC CK41 run (§22.5 R1).
"""

from __future__ import annotations


def _diagnostics_pending() -> None:
    """MAINDEC pass/fail runs land with M3-M8 (§11.1, §22.4)."""
    raise NotImplementedError("§11.1 MAINDEC diagnostics — M3-M8")
