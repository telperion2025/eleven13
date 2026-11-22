"""
Instruction handlers, registered into cpu.core's dispatch registry via the
@opcode decorator (§5.3). Importing this package registers every handler;
CPU.__init__ imports it once and renders the registry into jump buckets.

Capability gating (§5.4): every entry carries a CpuCap tag. The CPU checks
the tag against the profile before execute. On the KD11-B (11/20), the
microcoded-only opcodes of spec §2 are consumed as no-ops — that set is
KD11B_NOOP_HANDLERS below; any other missing capability (FPU, CIS, MMU-only
ops) traps as reserved (§5.8).

PDP-11 opcode map (bits 15-12 = group), Processor Handbook:
    0o00xxxx  group 0: control, condition codes, SWAB, JMP, branches (lo),
              single-operand word, JSR/RTS, EIS 0o07xxxx
    0o01-0o06ssdd  MOV CMP BIT BIC BIS ADD (word double-operand)
    0o16ssdd  SUB (word double-operand)
    0o11-0o15ssdd  MOVB CMPB BITB BICB BISB (byte double-operand)
    0o10xxxx  byte single-operand, branches (hi), EMT/TRAP 0o104xxx
    0o17xxxx  FP11 floating-point group
"""

from __future__ import annotations

# Spec §2: on the KD11-B (11/20) these microcoded-only opcodes are consumed
# as no-ops (advance PC, no trap) rather than trapping as reserved (§5.4).
KD11B_NOOP_HANDLERS = frozenset({
    "instr_MUL", "instr_DIV", "instr_ASH", "instr_ASHC",
    "instr_XOR", "instr_SOB", "instr_MARK_SPL", "instr_SXT",
    "instr_MTPS", "instr_MFPS",
})

from pdp11.cpu.instructions import (  # noqa: E402  (registry order matters not; F401)
    branch,
    cis,
    control,
    double_operand,
    eis,
    fp11,
    jump_subroutine,
    single_operand,
)

__all__ = [
    "KD11B_NOOP_HANDLERS",
    "branch",
    "cis",
    "control",
    "double_operand",
    "eis",
    "fp11",
    "jump_subroutine",
    "single_operand",
]
