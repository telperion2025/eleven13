"""
EIS instruction tests (spec §5.3/§5.4) — M4 on the 11/03 (KEF11 fitted):
MUL/DIV/ASH/ASHC/XOR/SOB execute with Processor-Handbook condition codes
(cross-checked against SIMH pdp11_cpu.c, §22.4), and the 11/20 (KD11-B, no
EIS cap) consumes them as no-ops with operands resolved (§2/§5.4).

Format of the 0o07x group: bits 8-6 = destination register, bits 5-0 =
source specifier. Register pairs hold the HIGH word in the lower register.
"""

from __future__ import annotations

import random

from pdp11.cpu.registers import PSW, Reg
from pdp11.tests.cpu_golden import make_machine

SIGN16 = 0o100000


def s16(v: int) -> int:
    return v - 0o200000 if v & SIGN16 else v


# -- MUL (§5.3): product into R (high), R+1 (low); odd R stores low only ------


def test_mul_positive_and_flags():
    cpu, _b = make_machine("11/03", program=[0o070002, 0o000000], pc=0o100)  # MUL R2,R0
    cpu.state.R[Reg.R0] = 7
    cpu.state.R[Reg.R2] = 3
    cpu.step()
    assert cpu.state.R[0] == 0 and cpu.state.R[1] == 0o25           # 21: high 0, low 0o25
    assert not cpu.state.psw.get(PSW.N) and not cpu.state.psw.get(PSW.Z)
    assert not cpu.state.psw.get(PSW.V) and not cpu.state.psw.get(PSW.C)


def test_mul_negative_sets_n_and_c_when_result_needs_32_bits():
    cpu, _b = make_machine("11/03", program=[0o070002, 0o000000], pc=0o100)
    cpu.state.R[Reg.R0] = (-3) & 0o177777
    cpu.state.R[Reg.R2] = 7
    cpu.step()                                                        # -21
    assert cpu.state.R[0] == 0o177777 and cpu.state.R[1] == (-21) & 0o177777
    assert cpu.state.psw.get(PSW.N) and not cpu.state.psw.get(PSW.C)  # fits in 16 bits
    cpu2, _b2 = make_machine("11/03", program=[0o070002, 0o000000], pc=0o100)
    cpu2.state.R[Reg.R0] = 0o100000                                # -32768
    cpu2.state.R[Reg.R2] = 0o100000                                # -32768 -> 2^30
    cpu2.step()
    assert cpu2.state.R[0] == 0o40000 and cpu2.state.R[1] == 0     # 2^30: high 0o40000
    assert cpu2.state.psw.get(PSW.C)                                # 2^30 doesn't fit in 16 bits


def test_mul_odd_register_stores_low_only():
    cpu, _b = make_machine("11/03", program=[0o070102, 0o000000], pc=0o100)  # MUL R2,R1
    cpu.state.R[Reg.R1] = 7
    cpu.state.R[Reg.R2] = 3
    cpu.state.R[Reg.R2 + 1] = 0o123456                               # R2 must stay (pair reg is R1)
    cpu.step()
    assert cpu.state.R[1] == 0o25                                   # low word only in odd R


# -- DIV ---------------------------------------------------------------------


def test_div_quotient_remainder():
    cpu, _b = make_machine("11/03", program=[0o071002, 0o000000], pc=0o100)  # DIV R2,R0
    cpu.state.R[Reg.R0] = 0
    cpu.state.R[Reg.R1] = 64                                        # dividend 64
    cpu.state.R[Reg.R2] = 7
    cpu.step()
    assert cpu.state.R[0] == 9 and cpu.state.R[1] == 1              # 64 = 9*7 + 1
    assert not cpu.state.psw.get(PSW.V) and not cpu.state.psw.get(PSW.C)


def test_div_by_zero_sets_v_and_c_leaves_pair():
    cpu, _b = make_machine("11/03", program=[0o071002, 0o000000], pc=0o100)
    cpu.state.R[Reg.R0] = 0o11
    cpu.state.R[Reg.R1] = 0o22
    cpu.state.R[Reg.R2] = 0
    cpu.step()
    assert cpu.state.psw.get(PSW.V) and cpu.state.psw.get(PSW.C)
    assert cpu.state.R[0] == 0o11 and cpu.state.R[1] == 0o22


def test_div_overflow_sets_v():
    cpu, _b = make_machine("11/03", program=[0o071002, 0o000000], pc=0o100)
    cpu.state.R[Reg.R0] = 0o000001                                  # dividend 65536
    cpu.state.R[Reg.R1] = 0
    cpu.state.R[Reg.R2] = 1                                          # quotient 65536 > 32767
    cpu.step()
    assert cpu.state.psw.get(PSW.V) and not cpu.state.psw.get(PSW.C)


# -- ASH / ASHC ---------------------------------------------------------------


def test_ash_left_and_right():
    cpu, _b = make_machine("11/03", program=[0o072002, 0o000000], pc=0o100)  # ASH R2,R0
    cpu.state.R[Reg.R0] = 0o000010
    cpu.state.R[Reg.R2] = 2
    cpu.step()
    assert cpu.state.R[0] == 0o40
    cpu2, _b2 = make_machine("11/03", program=[0o072002, 0o000000], pc=0o100)
    cpu2.state.R[Reg.R0] = 0o100010                                 # negative
    cpu2.state.R[Reg.R2] = (-2) & 0o77                               # right 2, arithmetic
    cpu2.step()
    assert cpu2.state.R[0] == 0o160002                              # sign-extended
    assert cpu2.state.psw.get(PSW.N) and not cpu2.state.psw.get(PSW.V)


def test_ash_left_overflow_sets_v():
    cpu, _b = make_machine("11/03", program=[0o072002, 0o000000], pc=0o100)
    cpu.state.R[Reg.R0] = 0o040000
    cpu.state.R[Reg.R2] = 1
    cpu.step()                                                       # 0o100000: sign flips
    assert cpu.state.R[0] == 0o100000
    assert cpu.state.psw.get(PSW.V) and cpu.state.psw.get(PSW.N)


def test_ashc_shifts_32bit_pair():
    cpu, _b = make_machine("11/03", program=[0o073002, 0o000000], pc=0o100)  # ASHC R2,R0
    cpu.state.R[Reg.R0] = 0o077777
    cpu.state.R[Reg.R1] = 0o177777
    cpu.state.R[Reg.R2] = 1
    cpu.step()
    assert cpu.state.R[0] == 0o177777 and cpu.state.R[1] == 0o177776
    assert cpu.state.psw.get(PSW.V)                                  # sign changed


# -- XOR / SOB ----------------------------------------------------------------


def test_xor():
    cpu, _b = make_machine("11/03", program=[0o074100, 0o000000], pc=0o100)  # XOR R1,R0
    cpu.state.R[Reg.R0] = 0o125252
    cpu.state.R[Reg.R1] = 0o052525
    cpu.step()
    assert cpu.state.R[0] == 0o177777
    assert cpu.state.psw.get(PSW.N) and not cpu.state.psw.get(PSW.V)


def test_sob_branches_until_zero():
    cpu, _b = make_machine("11/03", program=[0o077037, 0o000200, 0o000000], pc=0o100)
    cpu.state.R[Reg.R0] = 3
    cpu.step()                                                       # SOB R0,@#0o200
    assert cpu.state.R[0] == 2 and cpu.state.R[7] == 0o200
    cpu2, _b2 = make_machine("11/03", program=[0o077037, 0o000200, 0o000000], pc=0o100)
    cpu2.state.R[Reg.R0] = 1
    cpu2.step()
    assert cpu2.state.R[0] == 0 and cpu2.state.R[7] == 0o102         # no branch
    assert cpu2.state.psw.get(PSW.Z)


# -- 11/20 capability gate: no-op with operands consumed (§2/§5.4) ------------


def test_1120_noops_eis_opcodes():
    cpu, _b = make_machine("11/20", program=[0o070002, 0o000240], pc=0o100)
    cpu.state.R[Reg.R0] = 7
    cpu.state.R[Reg.R2] = 3
    cpu.step()                                                       # MUL R2,R0 as no-op
    assert cpu.state.R[0] == 7 and cpu.state.R[7] == 0o102
    cpu2, _b2 = make_machine("11/20", program=[0o077037, 0o000200, 0o000240], pc=0o100)
    cpu2.state.R[Reg.R0] = 3
    cpu2.step()                                                      # SOB: dest word consumed
    assert cpu2.state.R[0] == 3 and cpu2.state.R[7] == 0o104


# -- PRNG-seeded cross-check against host arithmetic (§3) ----------------------


def test_eis_prng_seeded_mul_div_crosscheck():
    rng = random.Random(0o11)
    for _ in range(64):
        a = rng.randrange(0, 0o200000)
        b = rng.randrange(0, 0o200000)
        cpu, _b = make_machine("11/03", program=[0o070002, 0o000000], pc=0o100)
        cpu.state.R[Reg.R0] = a
        cpu.state.R[Reg.R2] = b
        cpu.step()                                                   # MUL R2,R0
        product = s16(a) * s16(b)
        assert cpu.state.R[0] == (product >> 16) & 0o177777
        assert cpu.state.R[1] == product & 0o177777
        assert cpu.state.psw.get(PSW.N) == bool(product & 0x80000000)
        assert cpu.state.psw.get(PSW.Z) == (product == 0)
        assert cpu.state.psw.get(PSW.C) == (not -0o200000 <= product <= 0o177777)
        # now divide the product back by b (when nonzero)
        if b != 0:
            cpu2, _b2 = make_machine("11/03", program=[0o071002, 0o000000], pc=0o100)
            cpu2.state.R[Reg.R0] = (product >> 16) & 0o177777
            cpu2.state.R[Reg.R1] = product & 0o177777
            cpu2.state.R[Reg.R2] = b
            cpu2.step()                                              # DIV R2,R0
            q = (product - (1 << 32) if product & 0x80000000 else product) // s16(b)
            r = (product - (1 << 32) if product & 0x80000000 else product) - q * s16(b)
            if -0o200000 <= q <= 0o177777:
                assert cpu2.state.R[0] == q & 0o177777
                assert cpu2.state.R[1] == r & 0o177777
                assert not cpu2.state.psw.get(PSW.V)
