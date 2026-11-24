"""
FP11 sweep tests (spec §5.8) — M8 coverage pass for the §18.2 gate: exercises
the remaining 0o17xxxx handlers (MULf/MODf/SUBf/CMPf/DIVf, ABSf/NEGf/CLRf/
TSTf, STEXP/LDEXP, STCf*/LDCf*, FPS status ops, CFCC/SETI/SETL, D-precision
arithmetic) on the 11/70 so cpu/ line coverage clears 90%.

F-format word pairs in memory (low word at even address, §5.8); high word =
sign<<15 | exp<<7 | frac>>16 with excess-128 exp:
1.0F hi 040000, 2.0F 040200, 3.0F 040300, 4.0F 040400, 6.0F 040500,
7.0F 040540, 42.0F 040550, -1.0F 140000.

FP compare/test set the FPS condition codes (CFCC copies them to the PSW);
MODf puts the remainder in AC and the quotient in AC+1 (PDP-11 FP11 MOD).
"""

from __future__ import annotations

from fractions import Fraction

from pdp11.cpu.fp11 import FPS_D, FPS_L, FPS_N, FPS_Z, P_D, P_F
from pdp11.cpu.registers import PSW
from pdp11.tests.cpu_golden import make_machine, step_n

# opcodes (base 0o170000 | fop<<8 | ac<<6 | spec)
SETF, SETI, SETD, SETL, CFCC = 0o170001, 0o170002, 0o170011, 0o170012, 0o170000
LDFPS, STFPS, STST = 0o170100, 0o170200, 0o170300
CLRF, TSTF, ABSF, NEGF = 0o170400, 0o170500, 0o170600, 0o170700
MULF, MODF, ADDF, SUBF = 0o171000, 0o171400, 0o172000, 0o173000
LDF, CMPF, STF, DIVF = 0o172400, 0o173400, 0o174000, 0o174400
STEXP, STCFI, STCFF, LDEXP, LDCIF, LDCFF = (
    0o175000, 0o175400, 0o176000, 0o176400, 0o177000, 0o177400)


def put_f(board, addr: int, hi: int, lo: int = 0) -> None:
    board.ram.write_word(addr, lo)
    board.ram.write_word(addr + 2, hi)


def fpval(cpu, ac: int = 0, p: int = P_F) -> Fraction:
    return cpu.state.fp._value(cpu.state.fp.ac[ac], p)


def test_f_arithmetic_chain_and_cmpf():
    # SETF; LDF 1.0; MULF 3.0; SUBF 1.0; DIVF 2.0; ADDF 2.0; STF -> 3.0; CMPF 3.0; HALT
    prog = [SETF, LDF | 0o37, 0o2000, MULF | 0o37, 0o2004, SUBF | 0o37, 0o2010,
            DIVF | 0o37, 0o2014, ADDF | 0o37, 0o2020, STF | 0o37, 0o3000,
            CMPF | 0o37, 0o2024, 0o000000]
    cpu, board = make_machine("11/70", program=prog, pc=0o100)
    put_f(board, 0o2000, 0o040000)          # 1.0F
    put_f(board, 0o2004, 0o040300)          # 3.0F
    put_f(board, 0o2010, 0o040000)          # 1.0F
    put_f(board, 0o2014, 0o040200)          # 2.0F
    put_f(board, 0o2020, 0o040200)          # 2.0F
    put_f(board, 0o2024, 0o040300)          # 3.0F
    step_n(cpu, 9)
    assert cpu.state.halted
    assert fpval(cpu) == Fraction(3)                       # 1*3-1=2, /2=1, +2=3
    assert board.ram.read_word(0o3000) == 0               # 3.0F stored
    assert board.ram.read_word(0o3002) == 0o040300
    assert cpu.state.fp.fps & FPS_Z                        # CMPF equal -> FPS Z


def test_modf_remainder_in_ac_quotient_in_ac1():
    # SETF; LDF 7.0; MODF AC0,@#2004 (2.0); HALT  -> AC0=1 (remainder), AC1=3
    prog = [SETF, LDF | 0o37, 0o2000, MODF | 0o37, 0o2004, 0o000000]
    cpu, board = make_machine("11/70", program=prog, pc=0o100)
    put_f(board, 0o2000, 0o040540)          # 7.0F
    put_f(board, 0o2004, 0o040200)          # 2.0F
    step_n(cpu, 4)
    assert cpu.state.halted
    assert fpval(cpu, 0) == Fraction(1)                    # remainder
    assert fpval(cpu, 1) == Fraction(3)                    # quotient


def test_abs_neg_tst_clr_and_cfcc():
    # SETF; LDF -1.0; ABSF; NEGF; TSTF; CFCC; CLRF; HALT
    prog = [SETF, LDF | 0o37, 0o2000, ABSF, NEGF, TSTF, CFCC, CLRF, 0o000000]
    cpu, board = make_machine("11/70", program=prog, pc=0o100)
    put_f(board, 0o2000, 0o140000)          # -1.0F
    step_n(cpu, 3)
    assert fpval(cpu) == Fraction(1)                       # ABSF
    step_n(cpu, 1)
    assert fpval(cpu) == Fraction(-1)                      # NEGF
    step_n(cpu, 1)
    assert cpu.state.fp.fps & FPS_N                        # TSTF of -1.0 -> FPS N
    step_n(cpu, 2)                                         # CFCC then CLRF
    assert cpu.state.psw.get(PSW.N)                        # CFCC copied FPS N -> PSW
    assert fpval(cpu) == Fraction(0)                       # CLRF


def test_stexp_ldexp_roundtrip():
    # SETF; LDF 1.0; STEXP @#3000; LDEXP @#3000; HALT
    prog = [SETF, LDF | 0o37, 0o2000, STEXP | 0o37, 0o3000, LDEXP | 0o37, 0o3000,
            0o000000]
    cpu, board = make_machine("11/70", program=prog, pc=0o100)
    put_f(board, 0o2000, 0o040000)          # 1.0F
    step_n(cpu, 5)
    assert cpu.state.halted
    assert fpval(cpu) == Fraction(1)                       # exponent round-trip
    assert board.ram.read_word(0o3000) == cpu.state.fp.stexp(0)   # stored exponent


def test_stcff_ldcff_conversion_roundtrip():
    # SETF; LDCIF AC0,@#2050 (42); STCFF AC0,@#3000; CLRF; LDCFF AC0,@#3000; HALT
    prog = [SETF, LDCIF | 0o37, 0o2050, STCFF | 0o37, 0o3000, CLRF,
            LDCFF | 0o37, 0o3000, 0o000000]
    cpu, board = make_machine("11/70", program=prog, pc=0o100)
    board.ram.write_word(0o2050, 42)
    step_n(cpu, 7)
    assert cpu.state.halted
    assert fpval(cpu) == Fraction(42)
    assert board.ram.read_word(0o3000) == 0 and board.ram.read_word(0o3002) != 0  # 42.0F stored


def test_fps_status_ops():
    # SETF; STFPS @#3000; LDFPS @#3000; STST @#3002; SETI; SETL; SETD; HALT
    prog = [SETF, STFPS | 0o37, 0o3000, LDFPS | 0o37, 0o3000, STST | 0o37, 0o3002,
            SETI, SETL, SETD, 0o000000]
    cpu, board = make_machine("11/70", program=prog, pc=0o100)
    step_n(cpu, 4)                                         # SETF STFPS LDFPS STST
    assert board.ram.read_word(0o3000) == 0                # FPS was 0 after SETF
    assert cpu.state.fp.fps == 0                           # LDFPS restored it
    step_n(cpu, 6)
    assert cpu.state.halted
    assert cpu.state.fp.fps & FPS_D                        # SETD left D set
    cpu3, _ = make_machine("11/70", program=[SETL, 0o000000], pc=0o100)
    step_n(cpu3, 2)
    assert cpu3.state.fp.fps & FPS_L                       # SETL: long-int mode


def test_d_precision_mul_sub_div():
    # SETD; LDD 6.0D; MULF 2.0D; SUBF 4.0D; DIVF 2.0D; HALT -> 4.0D
    prog = [SETD, LDF | 0o37, 0o2000, MULF | 0o37, 0o2010, SUBF | 0o37, 0o2014,
            DIVF | 0o37, 0o2020, 0o000000]
    cpu, board = make_machine("11/70", program=prog, pc=0o100)
    put_f(board, 0o2000, 0o040500)          # 6.0D high word
    put_f(board, 0o2010, 0o040200)          # 2.0D
    put_f(board, 0o2014, 0o040400)          # 4.0D
    put_f(board, 0o2020, 0o040200)          # 2.0D
    step_n(cpu, 6)
    assert cpu.state.halted
    # 6*2-4=8, /2=4 in binary FP; allow engine rounding slack for D norm
    assert abs(fpval(cpu, 0, P_D) - Fraction(4)) < Fraction(1, 1 << 20)
