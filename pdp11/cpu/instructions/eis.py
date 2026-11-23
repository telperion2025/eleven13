"""
EIS — Extended Instruction Set (spec §3 layout): MUL 0o070rss, DIV 0o071rss,
ASH 0o072rss, ASHC 0o073rss, XOR 0o074rdd, SOB 0o077rdd, plus FIS 0o075xxx
on the LSI-11 (KEF11). SXT 0o0067dd rides this group's capability tag.

Format of the 0o07x group: bits 8-6 = destination REGISTER, bits 5-0 =
source operand specifier (memory or register) — the "register + operand"
format of the Processor Handbook. Register-pair results (MUL/DIV/ASHC)
use (R, R+1) with the HIGH word in the lower-numbered register; if R is
odd only R is written — the handbook register-pair rule.

Condition codes per the Processor Handbook, cross-checked against SIMH
pdp11_cpu.c (§22.4; corner behavior is flagged in docs/acceptance-checklist.md):
    MUL: N = bit 31 of product, Z = product == 0, V = 0, C = 1 if the signed
         32-bit product does not fit in 16 bits (SIMH: "MUL carry").
    DIV: quotient -> R, remainder -> R+1. V = 1 on divide-by-zero, on
         -2^31 / -1, or when the signed quotient does not fit in 16 bits;
         C = 1 only on divide-by-zero. On error the pair is left unchanged.
         N/Z from the quotient. (Odd R: the 16-bit dividend is R alone.)
    ASH/ASHC: signed 6-bit count in src (<0 = right shift). Left: C = last
         bit shifted out, V = 1 if any shifted-out bit differs from the
         result sign (SIMH: "bits shifted out do not equal the sign of the
         result"). Right: arithmetic, C = last bit out, V = 0.
    XOR: dst <- dst XOR R; N Z, V = 0, C unchanged.
    SOB: R <- R - 1; branch to dst (specifier bits 5-0) if R != 0; N Z from R.

M1 implemented only SXT; M4 (11/03 KEF11) implements MUL/DIV/ASH/ASHC/XOR/
SOB — on the 11/20 (no EIS cap) they are consumed as no-ops by the
capability gate (§5.4, KD11B_NOOP_HANDLERS). FIS stays NotImplementedError
(KEF11 floating set, follow-up). The capability gate always runs before
these handlers (§5.4).

References: PDP-11 Processor Handbook (EIS section); LSI-11/KEF11 docs;
SIMH pdp11_cpu.c case 007 (§22.4 cross-check source).
"""

from __future__ import annotations

from pdp11.cpu.core import (
    CPU,
    SIGN_BIT,
    WORD_MASK,
    opcode,
    read_op,
    resolve_operand,
    write_op,
)
from pdp11.cpu.registers import PSW, Reg


def _src_signed(cpu: CPU) -> int:
    """Resolve the EIS source specifier (bits 5-0 of the opcode word), read
    it as a word, and return it sign-extended."""
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    src_spec = cpu.last_op_word & 0o77
    ea = resolve_operand(st, bus, src_spec, True, False)
    raw = read_op(cpu, src_spec, ea, False) & WORD_MASK
    return raw - 0o200000 if raw & SIGN_BIT else raw


def _dst_reg(cpu: CPU) -> int:
    """Destination register of the 0o07x group: bits 8-6 of the opcode."""
    return (cpu.last_op_word >> 6) & 0o07


def _ash(value: int, bits: int, count: int) -> tuple[int, bool, bool]:
    """Arithmetic shift of the unsigned `bits`-wide `value` by the signed
    `count`. Returns (result masked to bits, carry, overflow)."""
    mask = (1 << bits) - 1
    sign = 1 << (bits - 1)
    orig = value & mask
    signed = orig - (1 << bits) if orig & sign else orig
    if count == 0:
        return orig, False, False
    if count > 0:                                        # left shift
        if count < bits:
            lost = orig >> (bits - count)                # bits that fell off the top
            result = (orig << count) & mask
            carry = bool(lost & 1)                       # last bit shifted out
        else:
            lost = orig
            result = 0
            carry = bool(orig & 1) if count == bits else False
        sign_r = 1 if result & sign else 0
        # V = 1 if any shifted-out bit differs from the result's sign
        overflow = any(((lost >> i) & 1) != sign_r for i in range(bits))
        return result, carry, overflow
    n = -count                                           # right shift (arithmetic)
    if n < bits:
        result = (signed >> n) & mask
        carry = bool((orig >> (n - 1)) & 1)
    else:
        result = mask if orig & sign else 0              # all sign (or all zero)
        carry = bool(orig & sign)                        # steps past the width lose sign bits
    return result, carry, False                          # right shifts never overflow


@opcode(0o070000, mask=0o177000, caps="EIS", fmt="src")   # MUL
def instr_MUL(cpu: CPU) -> None:
    """MUL src,R: signed 32-bit product into R (high) and R+1 (low);
    odd R stores only the low 16 bits in R. Not on the 11/20."""
    st = cpu.state
    psw = st.psw
    r = _dst_reg(cpu)
    src = _src_signed(cpu)
    multiplicand = st.R[r] - 0o200000 if st.R[r] & SIGN_BIT else st.R[r]
    product = multiplicand * src                        # signed 32-bit product
    if r & 1:                                            # odd R: low word only
        st.R[r] = product & WORD_MASK
    else:
        st.R[r] = (product >> 16) & WORD_MASK            # high word in R
        st.R[r + 1] = product & WORD_MASK                # low word in R+1
    psw.set(PSW.N, bool(product & 0x80000000))           # bit 31 of the product
    psw.set(PSW.Z, product == 0)
    psw.set(PSW.V, False)
    psw.set(PSW.C, not (-0o200000 <= product <= 0o177777))  # fits in 16 signed bits?


@opcode(0o071000, mask=0o177000, caps="EIS", fmt="src")   # DIV
def instr_DIV(cpu: CPU) -> None:
    """DIV src,R: 32-bit dividend (R:R+1, R = high) divided by src;
    quotient -> R, remainder -> R+1. V on div-by-zero/overflow, C on
    div-by-zero; the pair is left unchanged on error. Not on the 11/20."""
    st = cpu.state
    psw = st.psw
    r = _dst_reg(cpu)
    divisor = _src_signed(cpu)
    if r & 1:
        dividend = st.R[r] - 0o200000 if st.R[r] & SIGN_BIT else st.R[r]
    else:
        dividend = (st.R[r] << 16) | st.R[r + 1]
        if dividend & 0x80000000:
            dividend -= 0x100000000                     # sign-extend the 32-bit pair
    if divisor == 0:
        psw.set(PSW.N, False)
        psw.set(PSW.Z, False)
        psw.set(PSW.V, True)                             # divide by zero
        psw.set(PSW.C, True)
        return
    quotient = dividend // divisor                       # truncation == PDP-11 semantics
    remainder = dividend - quotient * divisor
    if not (-0o200000 <= quotient <= 0o177777) or (dividend == -0x80000000 and divisor == -1):
        psw.set(PSW.N, False)                            # overflow: pair unchanged
        psw.set(PSW.Z, False)
        psw.set(PSW.V, True)
        psw.set(PSW.C, False)
        return
    st.R[r] = quotient & WORD_MASK
    if not r & 1:
        st.R[r + 1] = remainder & WORD_MASK
    psw.set(PSW.N, bool(quotient & 0x8000))
    psw.set(PSW.Z, quotient == 0)
    psw.set(PSW.V, False)
    psw.set(PSW.C, False)


@opcode(0o072000, mask=0o177000, caps="EIS", fmt="src")   # ASH
def instr_ASH(cpu: CPU) -> None:
    """ASH src,R: R shifted arithmetically by the signed 6-bit count in src."""
    st = cpu.state
    psw = st.psw
    r = _dst_reg(cpu)
    count = _src_signed(cpu) & 0o77
    if count & 0o40:                                     # 0o40-0o77 = negative count
        count -= 0o100
    result, carry, overflow = _ash(st.R[r], 16, count)
    st.R[r] = result
    psw.set(PSW.N, bool(result & SIGN_BIT))
    psw.set(PSW.Z, result == 0)
    psw.set(PSW.V, overflow)
    psw.set(PSW.C, carry)


@opcode(0o073000, mask=0o177000, caps="EIS", fmt="src")   # ASHC
def instr_ASHC(cpu: CPU) -> None:
    """ASHC src,R: the 32-bit pair (R:R+1, R = high word) shifted by the
    signed 6-bit count in src. Odd R shifts R alone. Not on the 11/20."""
    st = cpu.state
    psw = st.psw
    r = _dst_reg(cpu)
    count = _src_signed(cpu) & 0o77
    if count & 0o40:
        count -= 0o100
    if r & 1:
        result, carry, overflow = _ash(st.R[r], 16, count)
        st.R[r] = result
        psw.set(PSW.N, bool(result & SIGN_BIT))
        psw.set(PSW.Z, result == 0)
        psw.set(PSW.V, overflow)
        psw.set(PSW.C, carry)
        return
    pair = (st.R[r] << 16) | st.R[r + 1]
    result, carry, overflow = _ash(pair, 32, count)
    st.R[r] = (result >> 16) & WORD_MASK                 # high word back into R
    st.R[r + 1] = result & WORD_MASK
    psw.set(PSW.N, bool(result & 0x80000000))
    psw.set(PSW.Z, result == 0)
    psw.set(PSW.V, overflow)
    psw.set(PSW.C, carry)


@opcode(0o074000, mask=0o177000, caps="EIS", fmt="src_dst")  # XOR 0o074rdd
def instr_XOR(cpu: CPU) -> None:
    """XOR 0o074rdd: dst <- dst XOR R (register bits 8-6).
    N Z from result, V = 0, C unchanged. No-op on the KD11-B (spec §2)."""
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    psw = st.psw
    r = _dst_reg(cpu)
    dst_spec = cpu.last_op_word & 0o77
    ea = resolve_operand(st, bus, dst_spec, False, False)
    old = read_op(cpu, dst_spec, ea, False)
    result = (old ^ st.R[r]) & WORD_MASK
    write_op(cpu, dst_spec, ea, result, False)
    psw.set(PSW.N, bool(result & SIGN_BIT))
    psw.set(PSW.Z, result == 0)
    psw.set(PSW.V, False)


@opcode(0o075000, mask=0o177000, caps="EIS", fmt="src")   # FIS 0o075xxx (LSI-11)
def instr_FIS(cpu: CPU) -> None:
    """FIS floating-integer instructions (LSI-11 KEF11): FADD etc."""
    raise NotImplementedError("§5.3 FIS — KEF11 floating set, M4 follow-up (§22.4)")


@opcode(0o077000, mask=0o177000, caps="EIS", fmt="dst")   # SOB 0o077rdd
def instr_SOB(cpu: CPU) -> None:
    """SOB R,addr: R <- R-1; if R != 0, PC <- addr (specifier bits 5-0).
    N Z from the decremented R. No-op on the 11/20."""
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    psw = st.psw
    r = _dst_reg(cpu)
    dst_spec = cpu.last_op_word & 0o77
    st.R[r] = (st.R[r] - 1) & WORD_MASK
    psw.set(PSW.N, bool(st.R[r] & SIGN_BIT))
    psw.set(PSW.Z, st.R[r] == 0)
    if st.R[r] != 0:
        ea = resolve_operand(st, bus, dst_spec, False, False)
        st.R[Reg.PC] = ea & WORD_MASK


@opcode(0o006700, mask=0o177700, caps="EIS", fmt="dst")   # SXT 0o0067dd
def instr_SXT(cpu: CPU) -> None:
    """SXT dst: dst <- 0o177777 if N else 0 (sign extend). Flags unchanged.
    Present on the LSI-11; consumed as a no-op on the KD11-B (spec §2)."""
    dst_spec = cpu.last_op_word & 0o77
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    ea = resolve_operand(st, bus, dst_spec, False, False)
    result = WORD_MASK if st.psw.get(PSW.N) else 0   # sign of N into the whole word
    write_op(cpu, dst_spec, ea, result, False)
