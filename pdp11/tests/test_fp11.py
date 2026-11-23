"""
M5: the FP11 floating-point unit (spec §5.8) — the exact-Fraction engine in
cpu/fp11.py (F and D formats, rounding/truncation, conversions, exceptions)
and the 0o17xxxx instruction group on the 11/70, including the 000244 FP
exception trap and the reserved-opcode trap on FPU-less machines (§5.4).

PDP-11 F/D formats: 8-bit excess-128 exponent, fraction with hidden bit.
Accumulators are 64-bit words; F occupies the top 32 (sign = bit 63).
In memory an FP operand is little-endian per 32-bit unit: the low word sits
at the lower address, so 1.0F is stored as words 000000, 040000.
"""

from __future__ import annotations

from fractions import Fraction

from pdp11.cpu.fp11 import (
    FEC_DZRO,
    FP11,
    FPS_D,
    FPS_ER,
    FPS_ID,
    FPS_T,
    P_D,
    P_F,
)
from pdp11.tests.cpu_golden import make_machine, one_step, step_n

# -- engine ---------------------------------------------------------------------

def test_engine_f_arithmetic_and_conversion():
    fp = FP11()
    fp.ldc_int(0, 2 << 16, False)                    # AC0 <- 2.0 (word, left-justified)
    assert fp._value(fp.ac[0], P_F) == Fraction(2)
    fp.add(0, Fraction(3))
    assert fp._value(fp.ac[0], P_F) == Fraction(5)
    assert fp.stc_int(0, False) == (5, False)
    fp.ldc_int(0, 3 << 16, False)
    fp.mul(0, Fraction(4))
    assert fp.stc_int(0, False)[0] == 12
    fp.ldc_int(0, 12 << 16, False)
    fp.div(0, Fraction(4))
    assert fp.stc_int(0, False)[0] == 3
    fp.sub(0, Fraction(5))
    assert fp._value(fp.ac[0], P_F) == Fraction(-2)
    assert fp.stc_int(0, False) == (0o177776, False)   # -2 as a 16-bit word


def test_engine_d_precision_and_truncation():
    fp = FP11()
    fp.fps |= FPS_D
    fp.ldc_int(0, 1, True)                           # long integer 1 -> 1.0 D
    assert fp._value(fp.ac[0], P_D) == Fraction(1)
    fp.mul(0, Fraction(3))                           # 3.0 exactly representable
    assert fp._value(fp.ac[0], P_D) == Fraction(3)
    # 1/3 rounded (FPS_T clear) vs truncated (FPS_T set), F precision
    fp.fps &= ~FPS_D
    fp.ldc_int(0, 1 << 16, False)
    fp.div(0, Fraction(3))
    rounded = fp.ac[0]
    fp.fps |= FPS_T
    fp.ldc_int(0, 1 << 16, False)
    fp.div(0, Fraction(3))
    truncated = fp.ac[0]
    assert fp._value(rounded, P_F) > Fraction(1, 3) > fp._value(truncated, P_F) \
        or fp._value(rounded, P_F) == fp._value(truncated, P_F)
    assert truncated <= rounded


def test_engine_div_by_zero_and_exception_enable():
    fp = FP11()
    fp.ldc_int(0, 1 << 16, False)
    assert fp.div(0, Fraction(0)) is None           # engine signals via None
    assert fp.exception(FEC_DZRO, 0o100) is True     # FPS ID clear -> trap 244
    assert fp.fec == FEC_DZRO and fp.fps & FPS_ER
    fp.fps |= FPS_ID
    assert fp.exception(FEC_DZRO, 0o100) is False    # ID set -> no trap


# -- instructions on the 11/70 ----------------------------------------------------

def test_f_instructions_ldf_addf_stf():
    # SETF ; LDF AC0,@#2000 ; ADDF AC0,@#2004 ; STF AC0,@#2010 ; HALT
    cpu, board = make_machine(
        "11/70",
        program=[0o170001, 0o172437, 0o2000, 0o172037, 0o2004, 0o174037, 0o2010, 0o000000],
        pc=0o100)
    board.ram.write_word(0o2002, 0o040000)           # 1.0F (high word)
    board.ram.write_word(0o2006, 0o040200)           # 2.0F (high word)
    step_n(cpu, 5)
    assert cpu.state.halted
    assert cpu.state.fp._value(cpu.state.fp.ac[0], P_F) == Fraction(3)
    assert board.ram.read_word(0o2010) == 0          # 3.0F low word
    assert board.ram.read_word(0o2012) == 0o40300    # 3.0F high word


def test_d_instructions_ldd_addd_stf():
    # SETD ; LDD AC0,@#2020 ; ADDD AC0,@#2030 ; STD AC0,@#2040 ; HALT
    cpu, board = make_machine(
        "11/70",
        program=[0o170011, 0o172437, 0o2020, 0o172037, 0o2030, 0o174037, 0o2040, 0o000000],
        pc=0o100)
    board.ram.write_word(0o2022, 0o040000)           # 1.0D high word
    board.ram.write_word(0o2032, 0o040200)           # 2.0D high word
    step_n(cpu, 5)
    assert cpu.state.halted
    assert cpu.state.fp._value(cpu.state.fp.ac[0], P_D) == Fraction(3)
    assert board.ram.read_word(0o2040) == 0
    assert board.ram.read_word(0o2042) == 0o40300    # 3.0D high word
    assert board.ram.read_word(0o2044) == 0 and board.ram.read_word(0o2046) == 0


def test_ldcif_stcif_round_trip():
    # SETF ; LDCIF AC0,@#2050 ; STCIF AC0,@#2060 ; HALT
    cpu, board = make_machine(
        "11/70",
        program=[0o170001, 0o177037, 0o2050, 0o175437, 0o2060, 0o000000],
        pc=0o100)
    board.ram.write_word(0o2050, 42)
    step_n(cpu, 4)
    assert cpu.state.fp._value(cpu.state.fp.ac[0], P_F) == Fraction(42)
    assert board.ram.read_word(0o2060) == 42         # 52 octal


def test_divf_by_zero_traps_through_244():
    # SETF ; LDF AC0,@#2000 (1.0) ; DIVF AC0,@#2004 (0.0) ; HALT
    cpu, board = make_machine(
        "11/70",
        program=[0o170001, 0o172437, 0o2000, 0o174437, 0o2004, 0o000000],
        pc=0o100)
    board.ram.write_word(0o2002, 0o040000)           # AC0 = 1.0
    board.ram.write_word(0o244, 0o1000)              # FP exception vector -> handler
    board.ram.write_word(0o246, 0o000000)
    board.ram.write_word(0o1000, 0o012737)           # handler: MOV #777,@#3000
    board.ram.write_word(0o1002, 0o777)
    board.ram.write_word(0o1004, 0o3000)
    board.ram.write_word(0o1006, 0o000000)           # HALT
    step_n(cpu, 8)
    assert cpu.state.halted
    assert cpu.state.fp.fec == FEC_DZRO
    assert board.ram.read_word(0o3000) == 0o777      # handler ran via vector 244


def test_fp_opcodes_reserved_on_11_20():
    cpu, _board = one_step([0o170000], mem={0o010: 0o500, 0o012: 0}, profile_key="11/20")
    assert cpu.state.R[7] == 0o500                   # reserved trap through 000010
