"""
FIS (KEF11 floating instruction set, 0o075xxx) tests — LSI-11/11/03 only
(spec §5.3/§5.8; profile.has_fis). Semantics cross-checked against SIMH
pdp11_fp.c fis11(): IR<2:0> is a register holding a STACK POINTER — the
source F-float is at R[reg]..R[reg]+2, the accumulator F-float at
R[reg]+4..R[reg]+6; the result is stored back at R[reg]+4 and R[reg] pops
by 4. op = IR<4:3>: 0 FADD, 1 FSUB, 2 FMUL, 3 FDIV
(binutils pdp11-opc.c: 0x7a00/0x7a08/0x7a10/0x7a18, mask 0xfff8).
IR & 0o000740 (bits 8-5) != 0 is reserved (000010, after a read of
R[reg] on the 11/03). FDIV by zero sets
N=V=C=1 and traps 000244 with FEC unchanged; overflow/underflow set V
(N too on underflow), FEC/FEA, and trap 000244 (fis11 forces FPS = IU|IV).
On success N/Z from the result word, V=C=0. -0 operands are cleaned to +0.
PDP-11 F format has the IEEE-754 binary32 bit layout with exponent bias
128 (not 127) and little-endian word order in memory, so struct plus a
bias shift is used to build/check operands.
"""

from __future__ import annotations

import struct

from pdp11.cpu.fp11 import FEC_OVFLO, FEC_UNFLO, FPS_IU, FPS_IV
from pdp11.cpu.registers import PSW, Reg
from pdp11.tests.cpu_golden import make_machine

FADD, FSUB, FMUL, FDIV = 0o075000, 0o075010, 0o075020, 0o075030

FLT_MAX = 3.4028234663852886e+38        # 0x7F7FFFFF
FLT_MIN_NORMAL = 1.1754943508222875e-38  # 0x00800000


def f32(x: float) -> int:
    """Python float -> PDP-11 F-format word. Same bit layout as IEEE-754
    binary32 but exponent bias 128 (not 127), so shift the exponent field
    up by one for normal numbers."""
    w = struct.unpack(">I", struct.pack(">f", x))[0]
    if w & 0x7F800000:                              # nonzero exponent field
        w += 1 << 23
    return w


def put_f(board, addr: int, x: float) -> None:
    """Store an F-float as a PDP-11 longword: LOW word at addr, high word
    at addr+2 (little-endian word order, as SIMH ReadW(A)/ReadW(A+2))."""
    w = f32(x)
    board.ram.write_word(addr, w & 0o177777)
    board.ram.write_word(addr + 2, (w >> 16) & 0o177777)


def get_f(board, addr: int) -> float:
    """Read an F-float back from its two words."""
    w = (board.ram.read_word(addr + 2) << 16) | board.ram.read_word(addr)
    if w & 0x7F800000:
        w -= 1 << 23
    return struct.unpack(">f", struct.pack(">I", w))[0]


def arm_vector(board, vec: int, handler: int) -> None:
    board.ram.write_word(vec, handler)
    board.ram.write_word(vec + 2, 0)


def fis_machine(op: int, src: float, acc: float, reg: int = Reg.R5,
                base: int = 0o1000, profile: str = "11/03"):
    """11/03 with `op|reg` at 0o100, R[reg]=base, fsrc=src at base, fac=acc
    at base+4 — the FIS stack frame."""
    cpu, board = make_machine(profile, program=[op | reg], pc=0o100)
    cpu.state.R[reg] = base
    put_f(board, base, src)
    put_f(board, base + 4, acc)
    return cpu, board


# -- the four arithmetic ops ---------------------------------------------------


def test_fadd_stores_result_and_pops_stack():
    cpu, board = fis_machine(FADD, 2.0, 1.0)          # FADD R5
    cpu.step()
    assert get_f(board, 0o1004) == 3.0                # acc <- acc + src
    assert get_f(board, 0o1000) == 2.0                # source untouched
    assert cpu.state.R[Reg.R5] == 0o1004              # popped by 4
    psw = cpu.state.psw
    assert not (psw.get(PSW.N) or psw.get(PSW.Z) or psw.get(PSW.V) or psw.get(PSW.C))


def test_fsub_negative_result_sets_n():
    cpu, board = fis_machine(FSUB, 2.0, 1.0)          # FSUB R5: 1.0 - 2.0
    cpu.step()
    assert get_f(board, 0o1004) == -1.0
    assert cpu.state.psw.get(PSW.N) and not cpu.state.psw.get(PSW.Z)
    assert not cpu.state.psw.get(PSW.V) and not cpu.state.psw.get(PSW.C)


def test_fmul():
    cpu, board = fis_machine(FMUL, -2.0, 3.0)         # FMUL R5: 3.0 * -2.0
    cpu.step()
    assert get_f(board, 0o1004) == -6.0
    assert cpu.state.psw.get(PSW.N)


def test_fdiv():
    cpu, board = fis_machine(FDIV, 4.0, 6.0)          # FDIV R5: 6.0 / 4.0
    cpu.step()
    assert get_f(board, 0o1004) == 1.5
    assert not cpu.state.psw.get(PSW.N) and not cpu.state.psw.get(PSW.Z)


def test_zero_result_sets_z():
    cpu, board = fis_machine(FADD, -1.0, 1.0)         # 1.0 + -1.0 = +0
    cpu.step()
    assert get_f(board, 0o1004) == 0.0
    assert cpu.state.psw.get(PSW.Z) and not cpu.state.psw.get(PSW.N)


def test_negative_zero_operands_cleaned():
    # SIMH fis11: sign set with exponent 0 -> +0 before the op, so -0 + -0
    # yields +0 (Z set), not -0.
    cpu, board = fis_machine(FADD, -0.0, -0.0)
    cpu.step()
    assert board.ram.read_word(0o1004) == 0 and board.ram.read_word(0o1006) == 0
    assert cpu.state.psw.get(PSW.Z) and not cpu.state.psw.get(PSW.N)


def test_round_half_up_not_ties_to_even():
    # 2^24 + 1.0 is exactly halfway between 2^24 and 2^24+2 in F format;
    # SIMH roundfp11 (add half, truncate) rounds UP to 2^24+2.
    cpu, board = fis_machine(FADD, 1.0, float(2 ** 24))
    cpu.step()
    assert get_f(board, 0o1004) == float(2 ** 24 + 2)


def test_bits_8_to_5_set_are_reserved():
    # SIMH fis11: IR & 000740 (bits 8-5) != 0 -> TRAP_ILL, so only
    # 0o075000-0o075037 are defined (binutils mask 0xfff8: just FADD..FDIV|r).
    cpu, board = make_machine("11/03", program=[0o075045], pc=0o100)
    cpu.state.R[Reg.R5] = 0o1000
    arm_vector(board, 0o000010, 0o4000)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o4000             # reserved, not FADD-aliased
    assert cpu.state.R[Reg.R5] == 0o1000              # stack untouched


# -- exceptions ------------------------------------------------------------------


def test_fdiv_by_zero_traps_244_and_keeps_state():
    cpu, board = fis_machine(FDIV, 0.0, 1.0)
    arm_vector(board, 0o244, 0o2000)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o2000             # trapped through 000244
    # the trap loads the vector's PSW, so V/N/C are checked on the PUSHED PSW
    pushed = board.ram.read_word(0o17772)
    assert pushed & (PSW.V | PSW.N | PSW.C) == PSW.V | PSW.N | PSW.C
    assert not pushed & PSW.Z
    assert get_f(board, 0o1004) == 1.0                # result NOT stored
    assert cpu.state.R[Reg.R5] == 0o1000              # stack NOT popped
    assert cpu.state.fp.fec == 0                      # SIMH fis11: FEC stays 0
    assert cpu.state.R[Reg.SP] == 0o17772             # PC+PSW pushed
    assert board.ram.read_word(0o17774) == 0o100      # saved PC = FIS instruction


def test_fdiv_by_negative_zero_also_traps():
    cpu, board = fis_machine(FDIV, -0.0, 1.0)         # -0 cleaned -> div by zero
    arm_vector(board, 0o244, 0o2000)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o2000
    pushed = board.ram.read_word(0o17772)
    assert pushed & (PSW.V | PSW.C) == PSW.V | PSW.C


def test_fadd_overflow_traps_244():
    cpu, board = fis_machine(FADD, FLT_MAX, FLT_MAX)
    arm_vector(board, 0o244, 0o2000)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o2000
    pushed = board.ram.read_word(0o17772)
    assert pushed & PSW.V and not pushed & (PSW.N | PSW.C)
    assert cpu.state.fp.fec == FEC_OVFLO
    assert get_f(board, 0o1004) == FLT_MAX            # accumulator unchanged
    assert cpu.state.R[Reg.R5] == 0o1000              # stack not popped


def test_fdiv_underflow_traps_244():
    cpu, board = fis_machine(FDIV, 4.0, FLT_MIN_NORMAL)   # 2^-126 / 4 -> 2^-128
    arm_vector(board, 0o244, 0o2000)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o2000
    pushed = board.ram.read_word(0o17772)
    assert pushed & (PSW.V | PSW.N) == PSW.V | PSW.N  # SIMH fis11: UNFLO -> V=N=1
    assert cpu.state.fp.fec == FEC_UNFLO
    assert get_f(board, 0o1004) == FLT_MIN_NORMAL


def test_fps_forced_to_iu_iv():
    # SIMH fis11 sets FPS = FPS_IU|FPS_IV on every FIS (traps ovf/unf enabled).
    cpu, _board = fis_machine(FADD, 2.0, 1.0)
    cpu.step()
    assert cpu.state.fp.fps == FPS_IU | FPS_IV
    assert cpu.state.fp.fec == 0


# -- decode / capability gating ----------------------------------------------------


def test_bits_8_6_set_is_reserved_instruction():
    cpu, board = make_machine("11/03", program=[0o075105], pc=0o100)  # IR<8:6> = 1
    cpu.state.R[Reg.R5] = 0o1000
    arm_vector(board, 0o000010, 0o4000)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o4000             # reserved trap, not executed


def test_fis_reserved_on_11_70_without_kef11():
    # 11/70 has the EIS cap but no KEF11 (SIMH: OPT_FIS unset -> TRAP_ILL).
    cpu, board = make_machine("11/70", program=[0o075005], pc=0o100)
    arm_vector(board, 0o000010, 0o4000)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o4000


def test_reg7_reads_instruction_space():
    # R7 (PC) as the FIS stack pointer: after the fetch PC = 0o102, so the
    # frame is the instruction stream itself (SIMH isenable for reg 7).
    cpu, board = make_machine("11/03", pc=0o100)
    w2, w1 = f32(2.0), f32(1.0)
    program = [0o075007,
               w2 & 0o177777, (w2 >> 16) & 0o177777,      # fsrc at 0o102/0o104
               w1 & 0o177777, (w1 >> 16) & 0o177777]      # fac  at 0o106/0o110
    for i, word in enumerate(program):
        board.ram.write_word(0o100 + 2 * i, word)
    cpu.step()                                        # FADD R7
    assert get_f(board, 0o106) == 3.0
    assert cpu.state.R[Reg.PC] == 0o106               # popped by 4
