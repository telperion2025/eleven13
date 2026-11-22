"""
Double-operand instructions: MOV/MOVB, CMP/CMPB, BIT/BITB, BIC/BICB,
BIS/BISB, ADD, SUB (spec §3 layout; Processor Handbook, instruction set
reference). ADD/SUB are word-only on every PDP-11; the byte group is the
word opcode with bit 15 set (nibbles 0o11-0o15).

Both operands are always resolved (address-mode side effects included) —
the hardware fetches source and destination for every double-operand
instruction, source first. Condition-code rules per the Processor Handbook:
    MOV/BIT/BIC/BIS: N,Z from result, V=0, C unchanged (BIT: C unchanged too)
    CMP: like SUB but nothing written
    ADD: N,Z,V,C from the 16-bit sum (V: operands same sign, result differs)
    SUB: N,Z,V,C from dst-src (C = borrow, i.e. dst < src unsigned)
"""

from __future__ import annotations

from collections.abc import Callable

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


def _nz(psw: PSW, result: int, bits: int) -> None:
    """Set N and Z from `result` masked to `bits` (word or byte width)."""
    psw.set(PSW.N, bool(result & (SIGN_BIT if bits == 16 else 0o200)))
    psw.set(PSW.Z, (result & ((1 << bits) - 1)) == 0)


@opcode(0o010000, mask=0o070000, fmt="src_dst")  # bit 15 free: MOV / MOVB pair
def instr_MOV(cpu: CPU) -> None:
    """MOV src,dst: dst <- src. N,Z from result; V=0; C unchanged."""
    op_word = cpu.last_op_word
    is_byte = bool(op_word & 0o100000)
    src_spec = (op_word >> 6) & 0o77      # operand specifiers are EMBEDDED in the opcode word
    dst_spec = op_word & 0o77
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    src_ea = resolve_operand(st, bus, src_spec, True, is_byte)
    dst_ea = resolve_operand(st, bus, dst_spec, False, is_byte)
    value = read_op(cpu, src_spec, src_ea, is_byte)
    write_op(cpu, dst_spec, dst_ea, value, is_byte)
    _nz(st.psw, value, 8 if is_byte else 16)
    st.psw.set(PSW.V, False)


@opcode(0o020000, mask=0o070000, fmt="src_dst")  # word/byte pair: CMP/CMPB
def instr_CMP(cpu: CPU) -> None:
    """CMP src,dst: set flags as for dst-src, write nothing."""
    op_word = cpu.last_op_word
    is_byte = bool(op_word & 0o100000)
    src_spec = (op_word >> 6) & 0o77      # operand specifiers are EMBEDDED in the opcode word
    dst_spec = op_word & 0o77
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    src_ea = resolve_operand(st, bus, src_spec, True, is_byte)
    dst_ea = resolve_operand(st, bus, dst_spec, False, is_byte)
    src = read_op(cpu, src_spec, src_ea, is_byte)
    dst = read_op(cpu, dst_spec, dst_ea, is_byte)
    _sub_flags(st.psw, dst, src, is_byte)


@opcode(0o030000, mask=0o070000, fmt="src_dst")  # word/byte pair: BIT/BITB
def instr_BIT(cpu: CPU) -> None:
    """BIT src,dst: N,Z from dst AND src; V=0; C unchanged."""
    _bitwise(cpu, lambda s, d: d & s, clear_v=True, keep_c=True)


@opcode(0o040000, mask=0o070000, fmt="src_dst")  # word/byte pair: BIC/BICB
def instr_BIC(cpu: CPU) -> None:
    """BIC src,dst: dst <- dst AND NOT src. N,Z from result; V=0; C unchanged."""
    _bitwise(cpu, lambda s, d: d & ~s, clear_v=True, keep_c=True, write=True)


@opcode(0o050000, mask=0o070000, fmt="src_dst")  # word/byte pair: BIS/BISB
def instr_BIS(cpu: CPU) -> None:
    """BIS src,dst: dst <- dst OR src. N,Z from result; V=0; C unchanged."""
    _bitwise(cpu, lambda s, d: d | s, clear_v=True, keep_c=True, write=True)


@opcode(0o060000, mask=0o070000, fmt="src_dst")  # bit 15 free: word/byte pair   # ADD (word only)
def instr_ADD(cpu: CPU) -> None:
    """ADD src,dst: dst <- dst+src (mod 0o200000). N,Z,V,C per handbook:
    C = carry out of bit 15; V = operands same sign but result sign differs."""
    op_word = cpu.last_op_word
    src_spec = (op_word >> 6) & 0o77      # operand specifiers are EMBEDDED in the opcode word
    dst_spec = op_word & 0o77
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    src_ea = resolve_operand(st, bus, src_spec, True, False)
    dst_ea = resolve_operand(st, bus, dst_spec, False, False)
    src = read_op(cpu, src_spec, src_ea, False)
    dst = read_op(cpu, dst_spec, dst_ea, False)
    true_sum = dst + src
    result = true_sum & WORD_MASK                # modulo 0o200000 (16-bit)
    write_op(cpu, dst_spec, dst_ea, result, False)
    psw = st.psw
    _nz(psw, result, 16)
    psw.set(PSW.C, true_sum > WORD_MASK)          # carry when the true sum exceeded 16 bits
    psw.set(PSW.V, bool((~(dst ^ src) & (dst ^ result)) & SIGN_BIT))


@opcode(0o160000, mask=0o170000, fmt="src_dst")   # SUB (word only)
def instr_SUB(cpu: CPU) -> None:
    """SUB src,dst: dst <- dst-src (mod 0o200000). C = borrow (dst < src
    unsigned); V = operands differ in sign and result sign != dst sign."""
    op_word = cpu.last_op_word
    src_spec = (op_word >> 6) & 0o77      # operand specifiers are EMBEDDED in the opcode word
    dst_spec = op_word & 0o77
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    src_ea = resolve_operand(st, bus, src_spec, True, False)
    dst_ea = resolve_operand(st, bus, dst_spec, False, False)
    src = read_op(cpu, src_spec, src_ea, False)
    dst = read_op(cpu, dst_spec, dst_ea, False)
    result = (dst - src) & WORD_MASK
    write_op(cpu, dst_spec, dst_ea, result, False)
    _sub_flags(st.psw, dst, src, False)


def _sub_flags(psw: PSW, dst: int, src: int, is_byte: bool) -> None:
    """CMP/SUB flag logic: compute dst-src, discard the result, set N Z V C."""
    bits = 8 if is_byte else 16
    mask = (1 << bits) - 1
    sign = 1 << (bits - 1)
    d = dst & mask
    s = src & mask
    result = (d - s) & mask
    psw.set(PSW.N, bool(result & sign))
    psw.set(PSW.Z, result == 0)
    psw.set(PSW.C, d < s)                        # borrow: unsigned dst < src
    psw.set(PSW.V, bool(((d ^ s) & (d ^ result)) & sign))


def _bitwise(cpu: CPU, func: Callable[[int, int], int], clear_v: bool, keep_c: bool,
             write: bool = False) -> None:
    """Shared skeleton for BIT/BIC/BIS (byte variants included)."""
    op_word = cpu.last_op_word
    is_byte = bool(op_word & 0o100000)
    src_spec = (op_word >> 6) & 0o77      # operand specifiers are EMBEDDED in the opcode word
    dst_spec = op_word & 0o77
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    src_ea = resolve_operand(st, bus, src_spec, True, is_byte)
    dst_ea = resolve_operand(st, bus, dst_spec, False, is_byte)
    src = read_op(cpu, src_spec, src_ea, is_byte)
    dst = read_op(cpu, dst_spec, dst_ea, is_byte)
    result = func(src, dst) & (BYTE_MASK if is_byte else WORD_MASK)
    if write:
        write_op(cpu, dst_spec, dst_ea, result, is_byte)
    _nz(st.psw, result, 8 if is_byte else 16)
    if clear_v:
        st.psw.set(PSW.V, False)
    # C unchanged (keep_c is implicit: we never touch PSW.C here)
