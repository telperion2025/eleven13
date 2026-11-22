"""
FP11/FPJ floating-point instruction group (spec §3 layout; §5.8): the
0o17xxxx opcode space — ADDF/D, SUBF/D, MULF/D, DIVF/D, ABS, NEG, CMP, MOD,
LDEXP, SETF/SETD/SETI/SETL, CFCC/CLCC, STC*/LD* accumulator moves, integer
conversions. The arithmetic engine itself lives in cpu/fp11.py.

On machines without the FPU these opcodes trap as reserved (§5.8); on
FPU-fitted machines they raise NotImplementedError until M5 (11/70 FP11) /
M6 (11/94 FPJ) per §19.10.

References: FP11 Floating Point Processor manual (EK-11FP11-TM); DCJ-11 docs.
"""

from __future__ import annotations

from pdp11.cpu.core import CPU, opcode


@opcode(0o170000, mask=0o170000, caps="FPU", fmt="none")  # whole 0o17xxxx FP group
def instr_FP11(cpu: CPU) -> None:
    """Any 0o17xxxx word is an FP11/FPJ instruction; the sub-decode lands
    with the FPU milestones. Without the FPU cap the CPU traps reserved (§5.8)."""
    raise NotImplementedError("§5.8 FP11/FPJ group — M5 (11/70 FP11) / M6 (11/94 FPJ)")
