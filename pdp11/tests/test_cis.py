"""
M6: CIS character-string instructions on the 11/94 (J-11 on-chip CIS;
spec §3/§5.4) — the 0o076xxx group per SIMH pdp11_cis.c: MOVC/MOVRC/MOVTC,
LOCC/SKPC, SCANC/SPANC, CMPC, MATC, L2DR/L3DR, and the inline-operand
variants (bit 6). Packed-decimal ops (ADDN…/CRC) raise NotImplementedError
(§22.4). On profiles without the CIS cap the group traps reserved (000010).
"""

from __future__ import annotations

import pytest

from pdp11.cpu.registers import PSW
from pdp11.tests.cpu_golden import make_machine, one_step, step_n

MOVC, MOVRC, MOVTC = 0o076030, 0o076031, 0o076032
LOCC, SKPC, SCANC, SPANC, CMPC, MATC = 0o076040, 0o076041, 0o076042, 0o076043, 0o076044, 0o076045
L2DR = 0o076020
MOVCI, LOCCI = 0o076130, 0o076140


def _run(prog, setup=None, n=3, profile="11/94"):
    cpu, board = make_machine(profile, program=prog, pc=0o100)
    if setup:
        setup(cpu, board)
    step_n(cpu, n)
    return cpu, board


def _bytes(board, addr, n):
    return bytes(board.ram.read_byte(addr + i) for i in range(n))


def _put_str(board, addr, s):
    for i, ch in enumerate(s):
        board.ram.write_byte(addr + i, ch)


def test_movc_forward_with_fill():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"HELLO")
        cpu.state.R[0] = 5
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = 8
        cpu.state.R[3] = 0o3000
        cpu.state.R[4] = 0o40                                   # fill = space
    cpu, board = _run([MOVC, 0o000000], setup, 2)
    assert _bytes(board, 0o3000, 8) == b"HELLO   "
    assert cpu.state.R[0] == 0                                  # max(0, 5-8)
    assert cpu.state.R[1] == cpu.state.R[2] == cpu.state.R[3] == 0
    assert cpu.state.psw.get(PSW.C)                             # 5 < 8: borrow


def test_movc_overlap_backward():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"ABCDEFGH")
        cpu.state.R[0] = 8
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = 8
        cpu.state.R[3] = 0o2002               # overlapping dst
        cpu.state.R[4] = 0
    cpu, board = _run([MOVC, 0o000000], setup, 2)
    assert _bytes(board, 0o2002, 8) == b"ABCDEFGH"


def test_movrc_reverses_at_dst_end():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"HELLO")
        cpu.state.R[0] = 5
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = 8
        cpu.state.R[3] = 0o3000
        cpu.state.R[4] = 0o40
    cpu, board = _run([MOVRC, 0o000000], setup, 2)
    assert _bytes(board, 0o3000, 8) == b"   OLLEH"


def test_movtc_translates():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"HeLLo")
        for i in range(128):
            board.ram.write_byte(0o4000 + i, ord(chr(i).swapcase()) if chr(i).isalpha() else i)
        cpu.state.R[0] = 5
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = 5
        cpu.state.R[3] = 0o3000
        cpu.state.R[4] = 0
        cpu.state.R[5] = 0o4000              # translation table
    cpu, board = _run([MOVTC, 0o000000], setup, 2)
    assert _bytes(board, 0o3000, 5) == b"hEllO"


def test_movci_inline_operands():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"XY")
        board.ram.write_word(0o1010, 2)
        board.ram.write_word(0o1012, 0o2000)
        board.ram.write_word(0o1014, 4)
        board.ram.write_word(0o1016, 0o3000)
    cpu, board = _run([MOVCI, 0o1010, 0o1014, 0o101, 0o000000], setup, 2)  # fill 'A'
    assert _bytes(board, 0o3000, 4) == b"XYAA"
    assert cpu.state.R[7] == 0o112                               # PC past inline words


def test_locc_and_skpc():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"HELLO")
        cpu.state.R[0] = 5
        cpu.state.R[1] = 0o2000
        cpu.state.R[4] = ord("L")
    cpu, board = _run([LOCC, 0o000000], setup, 2)
    assert (cpu.state.R[0], cpu.state.R[1]) == (3, 0o2002)       # remaining incl. match
    assert not cpu.state.psw.get(PSW.Z) and not cpu.state.psw.get(PSW.V)

    def setup2(cpu, board):
        _put_str(board, 0o2000, b"HELLO")
        cpu.state.R[0] = 5
        cpu.state.R[1] = 0o2000
        cpu.state.R[4] = ord("H")
    cpu, board = _run([SKPC, 0o000000], setup2, 2)
    assert (cpu.state.R[0], cpu.state.R[1]) == (4, 0o2001)


def test_loci_inline():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"HELLO")
        board.ram.write_word(0o1010, 5)
        board.ram.write_word(0o1012, 0o2000)
    cpu, board = _run([LOCCI, 0o1010, ord("O"), 0o000000], setup, 2)
    assert (cpu.state.R[0], cpu.state.R[1]) == (1, 0o2004)


def test_scanc_spanc():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"12AB")
        for i in range(128):
            board.ram.write_byte(0o4000 + i, 0o200 if chr(i).isalpha() else 0)
        cpu.state.R[0] = 4
        cpu.state.R[1] = 0o2000
        cpu.state.R[4] = 0o200
        cpu.state.R[5] = 0o4000
    cpu, board = _run([SCANC, 0o000000], setup, 2)
    assert (cpu.state.R[0], cpu.state.R[1]) == (2, 0o2002)

    def setup2(cpu, board):
        _put_str(board, 0o2000, b"AB12")
        for i in range(128):
            board.ram.write_byte(0o4000 + i, 0o200 if chr(i).isalpha() else 0)
        cpu.state.R[0] = 4
        cpu.state.R[1] = 0o2000
        cpu.state.R[4] = 0o200
        cpu.state.R[5] = 0o4000
    cpu, board = _run([SPANC, 0o000000], setup2, 2)
    assert (cpu.state.R[0], cpu.state.R[1]) == (2, 0o2002)


def test_cmpc_equal_and_mismatch():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"ABC")
        _put_str(board, 0o3000, b"ABC")
        cpu.state.R[0] = 3
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = 3
        cpu.state.R[3] = 0o3000
        cpu.state.R[4] = 0
    cpu, board = _run([CMPC, 0o000000], setup, 2)
    assert cpu.state.psw.get(PSW.Z) and not cpu.state.psw.get(PSW.N)

    def setup2(cpu, board):
        _put_str(board, 0o2000, b"ABC")
        _put_str(board, 0o3000, b"ABD")
        cpu.state.R[0] = 3
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = 3
        cpu.state.R[3] = 0o3000
        cpu.state.R[4] = 0
    cpu, board = _run([CMPC, 0o000000], setup2, 2)
    assert cpu.state.psw.get(PSW.N) and cpu.state.psw.get(PSW.C)   # 'C' < 'D'


def test_matc_counts_matches():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"A1B2")
        for i in range(128):
            board.ram.write_byte(0o4000 + i, 0o200 if chr(i).isalpha() else 0)
        cpu.state.R[0] = 4
        cpu.state.R[1] = 0o2000
        cpu.state.R[4] = 0o200
        cpu.state.R[5] = 0o4000
    cpu, board = _run([MATC, 0o000000], setup, 2)
    assert cpu.state.R[0] == 2 and cpu.state.R[1] == 0o2004


def test_l2dr_loads_descriptors():
    def setup(cpu, board):
        board.ram.write_word(0o1000, 0o1010)                       # desc list at R0
        board.ram.write_word(0o1002, 0o1014)
        board.ram.write_word(0o1010, 2)
        board.ram.write_word(0o1012, 0o2000)
        board.ram.write_word(0o1014, 4)
        board.ram.write_word(0o1016, 0o3000)
        cpu.state.R[0] = 0o1000
    cpu, board = _run([L2DR, 0o000000], setup, 2)
    assert (cpu.state.R[0], cpu.state.R[1], cpu.state.R[2], cpu.state.R[3]) == \
        (2, 0o2000, 4, 0o3000)


def test_cis_reserved_on_11_20():
    cpu, _board = one_step([MOVC], mem={0o010: 0o500, 0o012: 0}, profile_key="11/20")
    assert cpu.state.R[7] == 0o500                               # reserved trap 000010


def test_cis_decimal_ops_loud_stub():
    with pytest.raises(NotImplementedError):
        one_step([0o076050], profile_key="11/94")                # ADDN
