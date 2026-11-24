"""
FP11 floating-point arithmetic engine (spec §3 layout; §5.8) — 32-bit (F)
and 64-bit (D) sign-magnitude formats with an excess-128 exponent and an
implicit hidden bit (FP11 manual EK-11FP11-TM-001; decode cross-checked
against SIMH pdp11_fp.c, §21):

    F (32b):  <31> sign, <30:23> exponent (bias 0o200), <22:0> fraction
    D (64b):  <63> sign, <62:55> exponent, <54:0> fraction
    exponent 0 denotes zero (no denormals). Value = (-1)^s * (1.frac) *
    2^(exp-0o200).

The engine stores the six accumulators AC0-AC5 as 64-bit integers
(high 32 bits in the top word; F-format ops use only the top 32). All
arithmetic is done exactly (Python big-int / Fraction) and rounded once at
pack time: round-to-nearest (add half then truncate, as SIMH's roundfp11)
unless FPS<T> selects truncation. Exceptions set FEC/FEA and the FPS error
bit; when enabled (FPS<ID> clear and the per-exception IE bit clear) the
CPU traps through vector 000244 (§5.8; SIMH VEC_FPE). Reserved-operand,
divide-by-zero and undefined-variable NOP the operation; the others let it
complete with V/C set (§5.8).

FPS register (SIMH pdp11_defs.h): <0> C <1> V <2> Z <3> N <5> T truncate
<6> L long-int <7> D double <8> IC <9> IV <10> IU <11> IUV (per-exception
interrupt enables) <14> ID interrupt disable <15> ER error.

Exact rounding corners vs SIMH (guard-bit path) and the LDEXP overflow
range checks are flagged §22.4 in docs/acceptance-checklist.md.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

# -- FPS bits -------------------------------------------------------------------
FPS_C = 0o000001
FPS_V = 0o000002
FPS_Z = 0o000004
FPS_N = 0o000010
FPS_T = 0o000040      # truncate instead of round
FPS_L = 0o000100      # long (32-bit) integer operands
FPS_D = 0o000200      # double precision
FPS_IC = 0o000400     # interrupt on conversion error
FPS_IV = 0o001000     # interrupt on overflow
FPS_IU = 0o002000     # interrupt on underflow
FPS_IUV = 0o004000    # interrupt on undefined variable
FPS_ID = 0o010000     # interrupt disable (all FP exceptions)
FPS_ER = 0o100000     # error
FPS_CC = FPS_N | FPS_Z | FPS_V | FPS_C
FPS_RW = (FPS_ER | FPS_ID | FPS_IUV | FPS_IU | FPS_IV | FPS_IC
          | FPS_D | FPS_L | FPS_T | FPS_CC)

# -- FP exception codes (SIMH FEC_*) ---------------------------------------------
FEC_OP = 0o2          # illegal opcode/address mode (reserved operand)
FEC_DZRO = 0o4        # divide by zero
FEC_ICVT = 0o6        # integer conversion error
FEC_OVFLO = 0o10      # overflow
FEC_UNFLO = 0o12      # underflow
FEC_UNDFV = 0o14      # undefined variable

VECTOR_FPE = 0o244    # floating point exception trap (SIMH VEC_FPE)

_FEC_IE = {FEC_OP: 0, FEC_DZRO: 0, FEC_ICVT: FPS_IC, FEC_OVFLO: FPS_IV,
           FEC_UNFLO: FPS_IU, FEC_UNDFV: FPS_IUV}

FP_BIAS = 0o200       # excess-128 exponent bias
P_F = 24              # F mantissa bits incl. hidden bit
P_D = 56              # D mantissa bits incl. hidden bit


class FP11:
    """The FP11 processor state: FPS/FEC/FEA plus six 64-bit accumulators."""

    def __init__(self) -> None:
        self.fps = 0
        self.fec = 0
        self.fea = 0
        self.ac: list[int] = [0] * 6     # each a 64-bit word (h<<32 | l)

    # -- snapshot contract (§15) ---------------------------------------------
    def serialize(self) -> dict[str, Any]:
        """FPS/FEC/FEA + the six 64-bit accumulators, JSON-safe."""
        return {"fps": self.fps, "fec": self.fec, "fea": self.fea, "ac": list(self.ac)}

    def deserialize(self, state: dict[str, Any]) -> None:
        """Restore FP11 state; validates the schema keys (§15 contract)."""
        for key in ("fps", "fec", "fea", "ac"):
            if key not in state:
                raise ValueError(f"FP11 snapshot missing {key!r}")
        if len(state["ac"]) != 6:
            raise ValueError("FP11 snapshot must hold exactly 6 accumulators")
        self.fps, self.fec, self.fea = state["fps"], state["fec"], state["fea"]
        self.ac = [int(a) for a in state["ac"]]

    # -- format conversions --------------------------------------------------
    def _unpack(self, w: int, p: int) -> tuple[int, int, int]:
        """64-bit accumulator word -> (sign, expfield, mantissa incl. hidden).
        expfield 0 = zero."""
        if p == P_D:
            sign = (w >> 63) & 1
            exp = (w >> 55) & 0o377
            frac = w & ((1 << 55) - 1)
            m = (1 << 55) + frac if exp else 0
            return sign, exp, m
        h = (w >> 32) & 0xFFFFFFFF
        sign = (h >> 31) & 1
        exp = (h >> 23) & 0o377
        frac = h & 0o37777777   # 23-bit F fraction
        m = (1 << 23) + frac if exp else 0
        return sign, exp, m

    def _pack(self, sign: int, exp: int, m: int, p: int) -> int:
        """(sign, expfield, mantissa incl. hidden) -> 64-bit accumulator word."""
        if exp == 0 or m == 0:
            return 0
        if p == P_D:
            return (sign << 63) | (exp << 55) | (m - (1 << 55))
        h = (sign << 31) | (exp << 23) | (m - (1 << 23))
        return h << 32

    def _value(self, w: int, p: int) -> Fraction:
        """Accumulator word -> exact value (0 when exp field is 0)."""
        sign, exp, m = self._unpack(w, p)
        if exp == 0 or m == 0:
            return Fraction(0)
        v = Fraction(m) * Fraction(2) ** (exp - FP_BIAS - (p - 1))
        return -v if sign else v

    def _norm(self, f: Fraction, p: int, truncate: bool) -> tuple[int, int, int, int]:
        """Exact Fraction -> (sign, expfield, mantissa, status) rounded to p
        mantissa bits. status: 0 ok, FEC_OVFLO, FEC_UNFLO."""
        if f == 0:
            return 0, 0, 0, 0
        sign = 1 if f < 0 else 0
        f = abs(f)
        den = f.denominator            # a power of two
        k = den.bit_length() - 1
        m = f.numerator                # value = m * 2^-k
        e = -k
        bl = m.bit_length()
        shift = bl - p
        if shift > 0:
            rem = m & ((1 << shift) - 1)
            m >>= shift
            e += shift
            if not truncate and rem >= (1 << (shift - 1)):   # round half up (SIMH)
                m += 1
                if m.bit_length() > p:
                    m >>= 1
                    e += 1
        else:
            m <<= -shift
            e -= -shift
        exp = e + FP_BIAS + (p - 1)
        if exp > 0o377:
            return sign, 0, 0, FEC_OVFLO
        if exp < 1:
            return sign, 0, 0, FEC_UNFLO
        return sign, exp, m, 0

    def _store(self, ac: int, f: Fraction, p: int) -> int:
        """Round `f` to precision p and store into AC `ac`; returns the
        exception status (0 / FEC_OVFLO / FEC_UNFLO)."""
        truncate = bool(self.fps & FPS_T)
        sign, exp, m, status = self._norm(f, p, truncate)
        self.ac[ac] = self._pack(sign, exp, m, p)
        return status

    # -- condition codes -------------------------------------------------------
    def _set_cc(self, w: int) -> None:
        """FPS N/Z/V/C from a packed accumulator word (V set by caller)."""
        sign, exp, m = self._unpack(w, P_D) if self.fps & FPS_D else self._unpack(w, P_F)
        zero = exp == 0 or m == 0
        cc = FPS_Z if zero else (FPS_N if sign else 0)
        self.fps = (self.fps & ~FPS_CC) | cc

    def set_cc(self, w: int, v: bool = False) -> None:
        """Set FPS condition codes from result word `w`, with V flag."""
        self._set_cc(w)
        self.fps = (self.fps & ~FPS_V) | (FPS_V if v else 0)

    # -- exceptions --------------------------------------------------------------
    def exception(self, code: int, fea: int) -> bool:
        """Record an FP exception; returns True when the CPU must trap
        through vector 000244 (FPS ID clear and the per-exception IE clear)."""
        self.fec = code
        self.fea = fea & 0o177777
        self.fps |= FPS_ER
        ie = _FEC_IE.get(code, 0)
        return not (self.fps & FPS_ID) and not (self.fps & ie)

    # -- arithmetic ---------------------------------------------------------------
    def _p(self) -> int:
        return P_D if self.fps & FPS_D else P_F

    def add(self, ac: int, fsrc: Fraction) -> int:
        """AC[ac] += fsrc (exact); returns exception status."""
        p = self._p()
        status = self._store(ac, self._value(self.ac[ac], p) + fsrc, p)
        return status

    def sub(self, ac: int, fsrc: Fraction) -> int:
        p = self._p()
        return self._store(ac, self._value(self.ac[ac], p) - fsrc, p)

    def mul(self, ac: int, fsrc: Fraction) -> int:
        p = self._p()
        return self._store(ac, self._value(self.ac[ac], p) * fsrc, p)

    def div(self, ac: int, fsrc: Fraction) -> int | None:
        """AC[ac] /= fsrc; None on divide by zero (caller raises FEC_DZRO)."""
        if fsrc == 0:
            return None
        p = self._p()
        return self._store(ac, self._value(self.ac[ac], p) / fsrc, p)

    def mod(self, ac: int, fsrc: Fraction) -> tuple[int | None, Fraction, Fraction]:
        """MOD: AC[ac] <- remainder, AC[ac|1] <- integer quotient (truncated
        toward zero, FP11 semantics). Returns (status, remainder, quotient)."""
        p = self._p()
        a = self._value(self.ac[ac], p)
        if fsrc == 0:
            return None, Fraction(0), Fraction(0)
        q = a / fsrc
        q_int = Fraction(int(q))          # truncate toward zero
        rem = a - q_int * fsrc
        status = self._store(ac, rem, p)
        # quotient stored into the paired accumulator (high word in ac|1)
        self._store(ac | 1, q_int, p)
        return status, rem, q_int

    def cmp(self, ac: int, fsrc: Fraction) -> int:
        """Compare AC[ac] with fsrc: returns -1/0/1; zero==zero clears AC."""
        p = self._p()
        a = self._value(self.ac[ac], p)
        if a == fsrc:
            if a == 0:
                self.ac[ac] = 0            # +0 == -0: normalize to +0
            return 0
        return -1 if a < fsrc else 1

    # -- conversions ---------------------------------------------------------------
    def ldc_int(self, ac: int, val: int, is_long: bool) -> None:
        """LDCif: integer -> float into AC[ac] (exact). `val` is 32-bit
        left-justified for words (SIMH ReadI convention)."""
        p = self._p()
        if is_long:
            v = val - (1 << 32) if val & 0x80000000 else val
        else:
            w = (val >> 16) & 0o177777
            v = w - 0o200000 if w & 0o100000 else w
        self._store(ac, Fraction(v), p)

    def stc_int(self, ac: int, is_long: bool) -> tuple[int, bool]:
        """STCfi: AC[ac] -> integer, truncated toward zero. Returns
        (value, conversion_error). Sets FPS N/Z/C."""
        p = self._p()
        v = self._value(self.ac[ac], p)
        i = int(v)                          # truncate toward zero
        if is_long:
            ok = -(1 << 31) <= i < (1 << 31)
        else:
            ok = -0o100000 <= i < 0o100000
        err = not ok
        if err:
            i = 0
        cc = FPS_Z if i == 0 else (FPS_N if i < 0 else 0)
        self.fps = (self.fps & ~FPS_CC) | cc | (FPS_C if err else 0)
        return i & (0xFFFFFFFF if is_long else 0o177777), err

    def ldexp(self, ac: int, n: int) -> int:
        """LDEXP: set AC[ac] exponent from integer n (signed 16-bit);
        returns exception status (overflow/underflow per FP11)."""
        p = self._p()
        w = self.ac[ac]
        sign, exp, m = self._unpack(w, p)
        if exp == 0 or m == 0:
            return 0                        # zero stays zero
        if n & 0o100000:
            n -= 0o200000                   # signed displacement
        newexp = (n + FP_BIAS) & 0o377
        status = 0
        if n > 0o177 and n <= 0o177600:    # SIMH range checks (§22.4)
            status = FEC_OVFLO if n < 0o100000 else FEC_UNFLO
            if status:
                self.ac[ac] = 0
                return status
        self.ac[ac] = self._pack(sign, newexp, m, p)
        return status

    def stexp(self, ac: int) -> int:
        """STEXP: AC[ac] exponent (unbiased, signed) as a 16-bit word."""
        p = self._p()
        sign, exp, m = self._unpack(self.ac[ac], p)
        if exp == 0 or m == 0:
            return 0
        return (exp - FP_BIAS) & 0o177777
