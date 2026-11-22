"""
FP11 arithmetic engine (spec §3 layout; §5.8): 32-bit (F) and 64-bit (D)
sign-magnitude formats with excess-128 exponent; exceptions (reserved
operand, overflow, underflow, divide by zero) set V/C and trap per the
FP11 manual.

M1 stub per §19.10: instruction decoding for the 0o17xxxx group lives in
cpu/instructions/fp11.py; this module will hold the format conversion and
exception machinery behind profile.has_fpu (M5: 11/70 FP11, M6: 11/94 FPJ).

References: FP11 Floating Point Processor manual (EK-11FP11-TM-001) (§21).
"""

from __future__ import annotations


def _fp11_not_implemented() -> None:
    """FP11 arithmetic is not active in M1 (§12 M1: 11/20 BASE only)."""
    raise NotImplementedError("§5.8 FP11 engine (32/64-bit formats, exceptions) — M5/M6")
