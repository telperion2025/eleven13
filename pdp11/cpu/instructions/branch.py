"""
Branch instructions (spec §3 layout; Processor Handbook): BR and the 15
conditional branches. Opcode bits 15-8 select the condition (BR = 0o000400
-> cond 0o001 ... BCS = 0o103400 -> cond 0o207), bits 7-0 are a SIGNED byte
offset in words, relative to the PC AFTER the branch word was fetched.

    0o000400+  BR BNE BEQ BGE BLT BGT BLE        (cond 0o001-0o007)
    0o100000+  BPL BMI BHI BLOS BVC BVS BCC BCS  (cond 0o200-0o207)
"""

from __future__ import annotations

from collections.abc import Callable

from pdp11.cpu.core import CPU, WORD_MASK, opcode
from pdp11.cpu.registers import PSW, Reg
from pdp11.errors import EmulationError


def _cond_test(cond: int) -> Callable[[PSW], bool]:
    """Map a branch condition code (bits 15-8 of the branch word) to its
    predicate over the PSW — Processor Handbook branch table."""
    table: dict[int, Callable[[PSW], bool]] = {
        0o001: lambda p: True,                                   # BR
        0o002: lambda p: not p.get(PSW.Z),                      # BNE
        0o003: lambda p: p.get(PSW.Z),                           # BEQ
        0o004: lambda p: p.get(PSW.N) == p.get(PSW.V),           # BGE
        0o005: lambda p: p.get(PSW.N) != p.get(PSW.V),           # BLT
        0o006: lambda p: (not p.get(PSW.Z)) and (p.get(PSW.N) == p.get(PSW.V)),  # BGT
        0o007: lambda p: p.get(PSW.Z) or (p.get(PSW.N) != p.get(PSW.V)),         # BLE
        0o200: lambda p: not p.get(PSW.N),                       # BPL
        0o201: lambda p: p.get(PSW.N),                           # BMI
        0o202: lambda p: (not p.get(PSW.C)) and (not p.get(PSW.Z)),  # BHI
        0o203: lambda p: p.get(PSW.C) or p.get(PSW.Z),           # BLOS
        0o204: lambda p: not p.get(PSW.V),                       # BVC
        0o205: lambda p: p.get(PSW.V),                           # BVS
        0o206: lambda p: not p.get(PSW.C),                       # BCC
        0o207: lambda p: p.get(PSW.C),                           # BCS
    }
    if cond not in table:
        raise EmulationError(f"branch condition {oct(cond)} unknown")
    return table[cond]


def instr_BRANCH(cpu: CPU) -> None:
    """Evaluate the branch condition; if taken, add the signed word offset
    to PC (PC already points past the branch word)."""
    cond = (cpu.last_op_word >> 8) & 0o377
    if _cond_test(cond)(cpu.state.psw):
        offset = cpu.last_op_word & 0o377
        if offset & 0o200:                            # signed byte displacement
            offset -= 0o400
        cpu.state.R[Reg.PC] = (cpu.state.R[Reg.PC] + 2 * offset) & WORD_MASK


def _register_branches() -> None:
    """One masked entry per branch opcode (cond code in bits 15-8)."""
    lo = [0o000400, 0o001000, 0o001400, 0o002000, 0o002400, 0o003000, 0o003400]
    hi = [0o100000, 0o100400, 0o101000, 0o101400, 0o102000, 0o102400, 0o103000, 0o103400]
    for pattern in lo + hi:
        opcode(pattern, mask=0o177400, fmt="branch")(instr_BRANCH)


_register_branches()
