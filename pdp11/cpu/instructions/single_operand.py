"""
Single-operand instructions (spec §3 layout; Processor Handbook, instruction
set reference): CLR, COM, INC, DEC, NEG, ADC, SBC, TST, ROR, ROL, ASR, ASL
— word forms 0o0050dd-0o0063dd, byte forms 0o1050dd-0o1063dd (bit 15 set) —
plus SWAB (0o0003dd, word only).

Condition-code rules per the Processor Handbook:
    CLR: result 0, N=0 Z=1 V=0 C=0
    COM: ~v, N Z, V=0, C=1
    INC: v+1, N Z, V=(v==0o077777), C unchanged
    DEC: v-1, N Z, V=(v==0o100000), C unchanged
    NEG: -v, N Z, V=(v==0o100000), C=(v!=0)
    ADC/SBC: add/sub the old C; V/C per handbook carry rules
    TST: N Z, V=0, C=0
    ROR/ROL: rotate through C; N Z from result, V=0, C=shifted-out bit
    ASR: arithmetic shift right, bit 15 preserved, C=bit 0, V=0
    ASL: bit 15 -> C, 0 into bit 0, V = N XOR C (sign changed)
    SWAB: swap bytes; N,Z from result bits <7:0>; V=0, C=0
Byte variants operate on bits <7:0> (N = bit 7).
"""

from __future__ import annotations

from pdp11.cpu.core import (
    BYTE_MASK,
    CPU,
    SIGN_BIT,
    WORD_MASK,
    opcode,
    read_op,
    resolve_operand,
    write_op,
)
from pdp11.cpu.registers import PSW


def _nz(psw: PSW, result: int, is_byte: bool) -> None:
    """Set N and Z from result; N is bit 15 (word) or bit 7 (byte)."""
    psw.set(PSW.N, bool(result & (0o200 if is_byte else SIGN_BIT)))
    psw.set(PSW.Z, (result & (BYTE_MASK if is_byte else WORD_MASK)) == 0)


def _single(cpu: CPU, sub_op: int, is_byte: bool) -> None:
    """Fetch/resolve the destination operand once, run `sub_op`, write back."""
    dst_spec = cpu.last_op_word & 0o77           # destination specifier
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    ea = resolve_operand(st, bus, dst_spec, False, is_byte)
    old = read_op(cpu, dst_spec, ea, is_byte)
    psw = st.psw
    mask = BYTE_MASK if is_byte else WORD_MASK
    sign = 0o200 if is_byte else SIGN_BIT
    result = 0

    if sub_op == 0o50:                            # CLR
        result = 0
        psw.set(PSW.N, False)
        psw.set(PSW.Z, True)
        psw.set(PSW.V, False)
        psw.set(PSW.C, False)
    elif sub_op == 0o51:                          # COM
        result = (~old) & mask
        _nz(psw, result, is_byte)
        psw.set(PSW.V, False)
        psw.set(PSW.C, True)
    elif sub_op == 0o52:                          # INC
        result = (old + 1) & mask
        _nz(psw, result, is_byte)
        psw.set(PSW.V, old == (0o177 if is_byte else 0o077777))  # max positive -> 0
    elif sub_op == 0o53:                          # DEC
        result = (old - 1) & mask
        _nz(psw, result, is_byte)
        psw.set(PSW.V, old == (0o200 if is_byte else 0o100000))  # min negative -> max positive
    elif sub_op == 0o54:                          # NEG
        result = (-old) & mask
        _nz(psw, result, is_byte)
        psw.set(PSW.V, old == (0o200 if is_byte else 0o100000))  # only -(-32768) overflows
        psw.set(PSW.C, old != 0)                  # C=1 unless operand was 0
    elif sub_op == 0o55:                          # ADC: add old carry
        carry = 1 if psw.get(PSW.C) else 0
        result = (old + carry) & mask
        _nz(psw, result, is_byte)
        top = 0o177 if is_byte else 0o177777
        psw.set(PSW.V, old == (0o177 if is_byte else 0o077777) and carry == 1)
        psw.set(PSW.C, old == top and carry == 1)  # 0o177777 + 1 wraps to 0
    elif sub_op == 0o56:                          # SBC: subtract old carry
        borrow = 1 if psw.get(PSW.C) else 0
        result = (old - borrow) & mask
        _nz(psw, result, is_byte)
        psw.set(PSW.V, old == (0o200 if is_byte else 0o100000) and borrow == 1)
        psw.set(PSW.C, old == 0 and borrow == 1)   # 0 - 1 borrows
    elif sub_op == 0o57:                          # TST
        _nz(psw, old, is_byte)
        psw.set(PSW.V, False)
        psw.set(PSW.C, False)
    elif sub_op == 0o60:                          # ROR: rotate right through C
        c_in = 1 if psw.get(PSW.C) else 0
        c_out = old & 1
        result = ((old >> 1) | (c_in << (15 if not is_byte else 7))) & mask
        _nz(psw, result, is_byte)
        psw.set(PSW.V, False)
        psw.set(PSW.C, bool(c_out))
    elif sub_op == 0o61:                          # ROL: rotate left through C
        c_in = 1 if psw.get(PSW.C) else 0
        c_out = (old >> (7 if is_byte else 15)) & 1
        result = ((old << 1) | c_in) & mask
        _nz(psw, result, is_byte)
        psw.set(PSW.V, False)
        psw.set(PSW.C, bool(c_out))
    elif sub_op == 0o62:                          # ASR: arithmetic shift right
        c_out = old & 1
        result = ((old >> 1) | (old & sign)) & mask   # sign bit preserved
        _nz(psw, result, is_byte)
        psw.set(PSW.V, False)
        psw.set(PSW.C, bool(c_out))
    elif sub_op == 0o63:                          # ASL: bit 15 -> C, 0 into bit 0
        c_out = (old >> (7 if is_byte else 15)) & 1
        result = (old << 1) & mask
        _nz(psw, result, is_byte)
        psw.set(PSW.C, bool(c_out))
        psw.set(PSW.V, bool((result & sign) != 0) != bool(c_out))  # V = N XOR C
    else:
        from pdp11.errors import EmulationError
        raise EmulationError(f"single-operand sub-opcode {oct(sub_op)} not implemented")

    write_op(cpu, dst_spec, ea, result, is_byte)


def instr_SINGLE(cpu: CPU) -> None:
    """Word single-operand group: sub-opcode 0o50-0o63 from bits 11-6.
    The 0o64-0o67 range (MARK/SXT/MFPI/MTPI) is registered separately with
    capability tags — see eis.py / control.py."""
    _single(cpu, (cpu.last_op_word >> 6) & 0o177, is_byte=False)


def instr_SINGLEB(cpu: CPU) -> None:
    """Byte single-operand group (bit 15 set); 0o1064-0o1067 are MMU-gated
    (MTPS/MTPD/MFPD/MFPS) and registered separately in control.py."""
    _single(cpu, (cpu.last_op_word >> 6) & 0o177, is_byte=True)


def _register_single_operand() -> None:
    """Register each sub-opcode 0o50-0o63 with mask 0o177700 (bits 15-6 exact),
    word and byte forms — a coarser mask would shadow the gated 0o64-0o67 ops."""
    for sub in range(0o50, 0o64):
        opcode(sub << 6, mask=0o177700, fmt="dst")(instr_SINGLE)
        opcode(0o100000 | (sub << 6), mask=0o177700, fmt="dst")(instr_SINGLEB)


_register_single_operand()


@opcode(0o000300, mask=0o177700, fmt="dst")       # SWAB (word only)
def instr_SWAB(cpu: CPU) -> None:
    """SWAB dst: swap the two bytes of dst. N,Z from result bits <7:0>; V=0; C=0."""
    dst_spec = cpu.last_op_word & 0o77
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    ea = resolve_operand(st, bus, dst_spec, False, False)
    old = read_op(cpu, dst_spec, ea, False)
    result = ((old << 8) | (old >> 8)) & WORD_MASK
    write_op(cpu, dst_spec, ea, result, False)
    psw = st.psw
    psw.set(PSW.N, bool(result & 0o200))          # N from the NEW low byte
    psw.set(PSW.Z, (result & BYTE_MASK) == 0)
    psw.set(PSW.V, False)
    psw.set(PSW.C, False)
