"""
Jump and subroutine instructions (spec §3 layout; Processor Handbook):
JMP 0o0001dd, JSR 0o004rdd, RTS 0o00500r, EMT 0o104000-0o104377,
TRAP 0o104400-0o104777.

JMP with destination mode 0 (register) is not a legal jump — the hardware
treats it as a reserved instruction (vector 000010). JSR links: the
destination operand is resolved, the old register is pushed on the stack,
R[reg] receives the return address (PC after the operand words), and PC
takes the destination EA. EMT/TRAP vector through 000030/000034 with the
PC after the instruction word (the low byte of the EMT/TRAP word is an
argument carried inside the instruction, not a fetched word).
"""

from __future__ import annotations

from pdp11.cpu import traps
from pdp11.cpu.core import CPU, WORD_MASK, opcode, resolve_operand
from pdp11.cpu.registers import Reg


@opcode(0o000100, mask=0o177700, fmt="dst")         # JMP 0o0001dd
def instr_JMP(cpu: CPU) -> None:
    """JMP dst: PC <- effective address of dst. Mode 0 is reserved (000010)."""
    dst_spec = cpu.last_op_word & 0o77
    bus = cpu.bus
    assert bus is not None
    if (dst_spec & 0o70) == 0:                        # mode 0: no memory target
        traps.reserved_instruction(cpu)
        return
    ea = resolve_operand(cpu.state, bus, dst_spec, False, False)
    cpu.state.R[Reg.PC] = ea & WORD_MASK


@opcode(0o004000, mask=0o177000, fmt="jsr")         # JSR 0o004rdd
def instr_JSR(cpu: CPU) -> None:
    """JSR r,dst: -(SP)<-R[r]; R[r]<-PC; PC<-EA(dst). Mode 0 dest reserved."""
    link_reg = (cpu.last_op_word >> 6) & 0o07         # JSR 0o004RDD: R in bits 8-6
    dst_spec = cpu.last_op_word & 0o77                # dest specifier embedded in the word
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    if (dst_spec & 0o70) == 0:
        traps.reserved_instruction(cpu)
        return
    old = st.R[link_reg]
    ea = resolve_operand(st, bus, dst_spec, False, False)   # may fetch an index word (advances PC)
    st.R[link_reg] = st.R[Reg.PC]                     # link = PC AFTER the operand words
    traps.push_word(cpu, old)                         # push old link register
    st.R[Reg.PC] = ea & WORD_MASK


@opcode(0o000200, mask=0o177770, fmt="rts")         # RTS 0o0002 0r (bits 5-3 zero)
def instr_RTS(cpu: CPU) -> None:
    """RTS r: PC <- R[r]; R[r] <- (SP)+  (return from subroutine)."""
    reg = cpu.last_op_word & 0o07
    st = cpu.state
    st.R[Reg.PC] = st.R[reg] & WORD_MASK
    st.R[reg] = traps.pop_word(cpu)


@opcode(0o104000, mask=0o177400, fmt="none")        # EMT 0o104xxx
def instr_EMT(cpu: CPU) -> None:
    """EMT: trap through vector 000030; low byte is the EMT argument."""
    traps.trap(cpu, traps.VECTOR_EMT)


@opcode(0o104400, mask=0o177400, fmt="none")        # TRAP 0o104xxx (>= 0o104400)
def instr_TRAP(cpu: CPU) -> None:
    """TRAP: trap through vector 000034; low byte is the TRAP argument."""
    traps.trap(cpu, traps.VECTOR_TRAP)
