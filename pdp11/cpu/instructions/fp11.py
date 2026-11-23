"""
FP11/FPJ floating-point instruction group (spec §3 layout; §5.8): the
0o17xxxx opcode space. Decode (SIMH pdp11_fp.c, verified): fop = IR<11:8>,
accumulator ac = IR<7:6>, operand specifier = IR<5:0>.

    fop 0o00  CFCC 0o170000 / SETF 0o170001 / SETI 0o170002 / SETD 0o170011 /
              SETL 0o170012 (ac 0); LDFPS 0o1701dd / STFPS 0o1702dd / STST 0o1703dd
    fop 0o01  CLRf 0o1704dd / TSTf 0o1705dd / ABSf 0o1706dd / NEGf 0o1707dd
    fop 0o02  MULf 0o1710as   fop 0o03  MODf 0o1714as   fop 0o04  ADDF 0o172ass
    fop 0o05  LDF 0o1724as    fop 0o06  SUBf 0o173ass   fop 0o07  CMPf 0o1734as
    fop 0o10  STf 0o174ass    fop 0o11  DIVf 0o1744as
    fop 0o12  STEXP 0o1750dd  fop 0o13  STCfi 0o1754dd  fop 0o14  STCff' 0o1760dd
    fop 0o15  LDEXP 0o1764dd  fop 0o16  LDCif 0o1770dd  fop 0o17  LDCff' 0o1774dd

FP operand specifiers are length-aware (F = 4 bytes, D = 8; integer operands
2 or 4 per FPS<L>; PC immediates are always 2 bytes), resolved in D space
except the displacement word of modes 6/7 which is fetched from I space
(§5.7). The arithmetic engine lives in cpu/fp11.py. On machines without the
FPU these opcodes trap as reserved (§5.8 — the capability gate handles it).

Exceptions trap through vector 000244 when enabled (FPS ID/IE bits, §5.8);
reserved-operand, divide-by-zero and undefined-variable NOP the operation.
The accumulator word is always 64 bits (F uses the top 32), so the sign bit
is bit 63 for both precisions.
"""

from __future__ import annotations

from pdp11.cpu import traps
from pdp11.cpu.core import (
    CPU,
    DEC_ADDR_MODE,
    DEC_REG_NUM,
    SIGN_BIT,
    WORD_MASK,
    opcode,
    resolve_operand,
)
from pdp11.cpu.fp11 import (
    FEC_DZRO,
    FEC_ICVT,
    FEC_OP,
    FEC_OVFLO,
    FEC_UNDFV,
    FP11,
    FPS_CC,
    FPS_D,
    FPS_L,
    FPS_RW,
    FPS_T,
    P_D,
    P_F,
    VECTOR_FPE,
)
from pdp11.cpu.registers import PSW, Reg

_SIGN63 = 1 << 63


def _fp(cpu: CPU) -> FP11:
    fp = cpu.state.fp
    assert fp is not None
    return fp


def _ea_fp(cpu: CPU, spec: int, length: int) -> int:
    """FP operand effective address (SIMH GeteaFP): like resolve_operand but
    auto-inc/dec by the operand length (2 for PC); the mode 6/7 displacement
    word is fetched from I space (§5.7). Returns -1 for mode 0 (AC itself)."""
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    mode = (spec & DEC_ADDR_MODE) >> 3
    reg = spec & DEC_REG_NUM
    if mode == 0:
        return -1
    if mode == 1:
        return st.R[reg]
    if mode == 2:
        addr = st.R[reg]
        inc = 2 if reg == Reg.PC else length
        st.R[reg] = (addr + inc) & WORD_MASK
        return addr
    if mode == 3:
        addr = st.R[reg]
        st.R[reg] = (addr + 2) & WORD_MASK
        return bus.read_word(addr)
    if mode == 4:
        addr = (st.R[reg] - length) & WORD_MASK
        st.R[reg] = addr
        return addr
    if mode == 5:
        addr = (st.R[reg] - 2) & WORD_MASK
        st.R[reg] = addr
        return bus.read_word(addr)
    pc = st.R[Reg.PC]
    index = bus.read_word(pc, fetch=True)          # displacement: I space (§5.7)
    st.R[Reg.PC] = (pc + 2) & WORD_MASK
    if index & SIGN_BIT:
        index -= 0o200000
    base = (st.R[reg] + index) & WORD_MASK
    if mode == 6:
        return base
    return bus.read_word(base)


def _read_fp(cpu: CPU, ea: int, spec: int, lenf: int) -> int:
    """Read an FP operand (lenf bytes) -> 64-bit accumulator word; mode 0 is
    the accumulator itself (reg >= 6 is a reserved operand -> FEC_OP)."""
    fp = _fp(cpu)
    bus = cpu.bus
    assert bus is not None
    if ea < 0:
        ac = spec & DEC_REG_NUM
        if ac >= 6:
            raise _FPFault(FEC_OP)
        return fp.ac[ac]
    h = bus.read_word(ea) | (bus.read_word(ea + 2) << 16)
    if lenf == 8:
        lo = bus.read_word(ea + 4) | (bus.read_word(ea + 6) << 16)
        return (h << 32) | lo
    return h << 32


def _write_fp(cpu: CPU, ea: int, spec: int, lenf: int, w: int) -> None:
    bus = cpu.bus
    assert bus is not None
    if ea < 0:
        return                                     # mode 0: caller stores the AC
    h = (w >> 32) & 0xFFFFFFFF
    bus.write_word(ea, h & WORD_MASK)
    bus.write_word(ea + 2, (h >> 16) & WORD_MASK)
    if lenf == 8:
        lo = w & 0xFFFFFFFF
        bus.write_word(ea + 4, lo & WORD_MASK)
        bus.write_word(ea + 6, (lo >> 16) & WORD_MASK)


def _read_int(cpu: CPU, ea: int, spec: int, leni: int) -> int:
    """Read an integer operand (2 or 4 bytes), left-justified in 32 bits
    (SIMH ReadI)."""
    bus = cpu.bus
    assert bus is not None
    if ea < 0:
        return cpu.state.R[spec & DEC_REG_NUM] << 16
    if leni == 2:
        return bus.read_word(ea) << 16
    return (bus.read_word(ea) << 16) | bus.read_word(ea + 2)


def _write_int(cpu: CPU, ea: int, spec: int, leni: int, data: int) -> None:
    """Write an integer operand (2 or 4 bytes) from a 32-bit left-justified
    value (SIMH WriteI)."""
    bus = cpu.bus
    assert bus is not None
    if ea < 0:
        cpu.state.R[spec & DEC_REG_NUM] = (data >> 16) & WORD_MASK
        return
    if leni == 2:
        bus.write_word(ea, (data >> 16) & WORD_MASK)
    else:
        bus.write_word(ea, (data >> 16) & WORD_MASK)
        bus.write_word(ea + 2, data & WORD_MASK)


def _word_ea(cpu: CPU, spec: int) -> int:
    """Word EA for LDFPS/STFPS/STEXP/LDEXP (SIMH GeteaFW)."""
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    return resolve_operand(st, bus, spec, False, False)


class _FPFault(Exception):
    """Internal: FP exception that aborts (NOPs) the instruction (§5.8)."""

    def __init__(self, code: int) -> None:
        self.code = code


def _trap_if(cpu: CPU, code: int) -> None:
    """Record FP exception `code`; trap through 000244 when enabled (§5.8)."""
    if _fp(cpu).exception(code, cpu.last_instr_addr):
        traps.trap(cpu, VECTOR_FPE, saved_pc=cpu.last_instr_addr)


def _lenf(cpu: CPU) -> int:
    return 8 if _fp(cpu).fps & FPS_D else 4


def _undef(w: int, p: int) -> bool:
    """Undefined variable: exponent field 0 with a nonzero fraction."""
    if p == P_D:
        return ((w >> 55) & 0o377) == 0 and (w & ((1 << 55) - 1)) != 0
    h = (w >> 32) & 0xFFFFFFFF
    return ((h >> 23) & 0o377) == 0 and (h & 0o7777777) != 0


@opcode(0o170000, mask=0o170000, caps="FPU", fmt="none")  # whole 0o17xxxx FP group
def instr_FP11(cpu: CPU) -> None:
    """0o17xxxx: FP11 group — fop = IR<11:8>, ac = IR<7:6>, spec = IR<5:0>
    (SIMH pdp11_fp.c); dispatch to the engine in cpu/fp11.py. Without the
    FPU cap the CPU traps reserved (§5.8 capability gate)."""
    fp = _fp(cpu)
    ir = cpu.last_op_word
    fop = (ir >> 8) & 0o17
    ac = (ir >> 6) & 0o3
    spec = ir & 0o77
    p = P_D if fp.fps & FPS_D else P_F
    lenf = _lenf(cpu)
    bus = cpu.bus
    assert bus is not None
    try:
        if fop == 0o00:
            if ac == 0:                            # specials
                if ir == 0o170000:                 # CFCC: FPS cc -> PSW cc
                    cpu.state.psw.value = (cpu.state.psw.value & ~0o17) | (fp.fps & 0o17)
                elif ir == 0o170001:               # SETF
                    fp.fps &= ~FPS_D
                elif ir == 0o170002:               # SETI
                    fp.fps &= ~FPS_L
                elif ir == 0o170011:               # SETD
                    fp.fps |= FPS_D
                elif ir == 0o170012:               # SETL
                    fp.fps |= FPS_L
                else:
                    raise _FPFault(FEC_OP)
            elif ac == 1:                          # LDFPS
                ea = _word_ea(cpu, spec)
                val = cpu.state.R[spec & 7] if ea < 0 else bus.read_word(ea)
                fp.fps = val & FPS_RW
            elif ac == 2:                          # STFPS
                ea = _word_ea(cpu, spec)
                if ea < 0:
                    cpu.state.R[spec & 7] = fp.fps & FPS_RW
                else:
                    bus.write_word(ea, fp.fps & FPS_RW)
            else:                                  # STST: (FEC<<16)|FEA as long
                ea = _word_ea(cpu, spec)
                _write_int(cpu, ea, spec, 4, (((fp.fec & 0o17) << 16) | fp.fea) << 16)
        elif fop == 0o01:                          # CLRf/TSTf/ABSf/NEGf
            ea = _ea_fp(cpu, spec, lenf)
            if ac == 0:                            # CLRf
                _write_fp(cpu, ea, spec, lenf, 0)
                fp.fps = (fp.fps & ~FPS_CC) | 0o4   # Z
            else:
                w = _read_fp(cpu, ea, spec, lenf)
                if ac == 1:                        # TSTf
                    fp.set_cc(w)
                else:
                    if _undef(w, p):
                        raise _FPFault(FEC_UNDFV)
                    _, exp, m = fp._unpack(w, p)
                    if exp == 0 or m == 0:
                        w = 0
                    elif ac == 2:                  # ABSf: clear sign (bit 63)
                        w &= ~_SIGN63
                    else:                          # NEGf: toggle sign
                        w ^= _SIGN63
                    if ea >= 0:
                        _write_fp(cpu, ea, spec, lenf, w)
                    else:
                        fp.ac[spec & 7] = w
                    fp.set_cc(w)
        elif fop in (0o02, 0o03, 0o04, 0o06):      # MULf/MODf/ADDF/SUBf
            ea = _ea_fp(cpu, spec, lenf)
            w = _read_fp(cpu, ea, spec, lenf)
            if _undef(w, p):
                raise _FPFault(FEC_UNDFV)
            fsrc = fp._value(w, p)
            if fop == 0o04:                        # ADDF
                status: int | None = fp.add(ac, fsrc)
            elif fop == 0o06:                      # SUBf
                status = fp.sub(ac, fsrc)
            elif fop == 0o02:                      # MULf
                status = fp.mul(ac, fsrc)
            else:                                  # MODf
                status, _rem, _q = fp.mod(ac, fsrc)
            fp.set_cc(fp.ac[ac], v=status == FEC_OVFLO)
            if status:
                _trap_if(cpu, status)
        elif fop == 0o05:                          # LDF
            ea = _ea_fp(cpu, spec, lenf)
            w = _read_fp(cpu, ea, spec, lenf)
            if _undef(w, p):
                raise _FPFault(FEC_UNDFV)
            fp.ac[ac] = w
            fp.set_cc(w)
        elif fop == 0o07:                          # CMPf
            ea = _ea_fp(cpu, spec, lenf)
            w = _read_fp(cpu, ea, spec, lenf)
            if _undef(w, p):
                raise _FPFault(FEC_UNDFV)
            c = fp.cmp(ac, fp._value(w, p))
            cc = 0o4 if c == 0 else (0o10 if c > 0 else 0)   # Z, else N if operand < AC
            fp.fps = (fp.fps & ~FPS_CC) | cc
        elif fop == 0o10:                          # STf
            ea = _ea_fp(cpu, spec, lenf)
            _write_fp(cpu, ea, spec, lenf, fp.ac[ac])
        elif fop == 0o11:                          # DIVf
            ea = _ea_fp(cpu, spec, lenf)
            w = _read_fp(cpu, ea, spec, lenf)
            if _undef(w, p):
                raise _FPFault(FEC_UNDFV)
            status = fp.div(ac, fp._value(w, p))
            if status is None:                     # divide by zero: NOP the op
                _trap_if(cpu, FEC_DZRO)
            else:
                fp.set_cc(fp.ac[ac], v=status == FEC_OVFLO)
                if status:
                    _trap_if(cpu, status)
        elif fop == 0o12:                          # STEXP
            ea = _word_ea(cpu, spec)
            dst = fp.stexp(ac)
            if ea < 0:
                cpu.state.R[spec & 7] = dst
            else:
                bus.write_word(ea, dst)
            psw = cpu.state.psw
            psw.value = (psw.value & ~(PSW.N | PSW.Z)) \
                | (PSW.N if dst & 0o100000 else 0) | (PSW.Z if dst == 0 else 0)
        elif fop == 0o13:                          # STCfi (float -> integer)
            leni = 4 if fp.fps & FPS_L else 2
            ea = _ea_fp(cpu, spec, leni)
            val, err = fp.stc_int(ac, leni == 4)
            _write_int(cpu, ea, spec, leni, val << 16 if leni == 2 else val)
            psw = cpu.state.psw
            psw.value = (psw.value & ~(PSW.N | PSW.Z | PSW.C)) \
                | (PSW.N if fp.fps & 0o10 else 0) | (PSW.Z if fp.fps & 0o4 else 0) \
                | (PSW.C if fp.fps & 0o1 else 0)
            if err:
                _trap_if(cpu, FEC_ICVT)
        elif fop == 0o14:                          # STCff' (AC -> other precision)
            other_len = 12 - lenf
            ea = _ea_fp(cpu, spec, other_len)
            other = P_F if fp.fps & FPS_D else P_D
            sign, exp, m, status = fp._norm(fp._value(fp.ac[ac], p), other,
                                            bool(fp.fps & FPS_T))
            _write_fp(cpu, ea, spec, other_len, fp._pack(sign, exp, m, other))
            fp.set_cc(fp.ac[ac], v=bool(status))
            if status:
                _trap_if(cpu, status)
        elif fop == 0o15:                          # LDEXP
            ea = _word_ea(cpu, spec)
            n = cpu.state.R[spec & 7] if ea < 0 else bus.read_word(ea)
            status = fp.ldexp(ac, n)
            fp.set_cc(fp.ac[ac], v=bool(status))
            if status:
                _trap_if(cpu, status)
        elif fop == 0o16:                          # LDCif (integer -> float)
            leni = 4 if fp.fps & FPS_L else 2
            ea = _ea_fp(cpu, spec, leni)
            fp.ldc_int(ac, _read_int(cpu, ea, spec, leni), leni == 4)
            fp.set_cc(fp.ac[ac])
        else:                                      # fop 0o17: LDCff' (other precision -> AC)
            other_len = 12 - lenf
            ea = _ea_fp(cpu, spec, other_len)
            w = _read_fp(cpu, ea, spec, other_len)
            other = P_F if fp.fps & FPS_D else P_D
            if _undef(w, other):
                raise _FPFault(FEC_UNDFV)
            status = fp._store(ac, fp._value(w, other), p)
            fp.set_cc(fp.ac[ac], v=bool(status) and not bool(fp.fps & FPS_T))
            if status:
                _trap_if(cpu, status)
    except _FPFault as fault:
        _trap_if(cpu, fault.code)                  # reserved operand etc.: NOP the op
