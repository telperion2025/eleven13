"""
VT52/VT100 software terminal emulators (spec §8): a real state machine, not
raw pass-through — RT-11/RSTS expect the escape semantics. Scope for v1:
cursor control sequences, AVO 24-line mode, ANSI mode; VT52 mode as a
reduced variant. Graphics character sets are out of scope for v1 (§8, R5).

Character-encoding contract (§8): strictly 7-bit ASCII; host Unicode keys
map to 7-bit ASCII by truncation/modulo where a plausible match exists;
keys with no 7-bit equivalent are dropped with a debug-log note. The
frontend documents which host escape sequences translate to ANSI sequences
and which are consumed as editing keys.

M7 stub per §19.10.
"""

from __future__ import annotations


def _vt100_not_implemented() -> None:
    """VT100/VT52 terminal state machine lands in M7 (§12)."""
    raise NotImplementedError("§8 VT100/VT52 terminal emulation — M7")
