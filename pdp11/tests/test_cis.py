"""
M6: CIS character-string instructions on the 11/94 (J-11 on-chip CIS;
spec §3/§5.4) — the 0o076xxx group per SIMH pdp11_cis.c: MOVC/MOVRC/MOVTC,
LOCC/SKPC, SCANC/SPANC, CMPC, MATC, L2DR/L3DR, and the inline-operand
variants (bit 6). Packed-decimal arithmetic (ADDN/SUBN/CMPN/ADDP/SUBP/CMPP/
MULP/DIVP/ASHN/ASHP) plus the conversions (CVTNL/CVTPN/CVTNP/CVTPL/CVTLN/
CVTLP, register + inline) are implemented per SIMH pdp11_cis.c. On profiles
without the CIS cap the group traps reserved (000010).
"""

from __future__ import annotations

from pdp11.cpu.registers import PSW, Reg
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


ADDN, SUBN, CMPN, ASHN = 0o076050, 0o076051, 0o076052, 0o076056
ADDP, SUBP, CMPP, MULP, DIVP, ASHP = 0o076070, 0o076071, 0o076072, 0o076074, 0o076075, 0o076076
CVTNL = 0o076053


def _put_zoned(board, addr, s, sign_neg=False):
    for i, ch in enumerate(s):
        b = ord(ch)
        if sign_neg and i == len(s) - 1:
            b |= 0x80
        board.ram.write_byte(addr + i, b)


def _put_packed(board, addr, digits, sign_neg=False):
    n = len(digits)
    nbytes = n // 2 + 1
    ds = [0] * (n - n) + [int(c) for c in digits]
    ds = [0] * (n - len(ds)) + ds
    for i in range(nbytes):
        hi = ds[2 * i] if 2 * i < n else 0
        if 2 * i + 1 < n:
            lo = ds[2 * i + 1]
        elif 2 * i + 1 == n:
            lo = 0xD if sign_neg else 0xC
        else:
            lo = 0
        board.ram.write_byte(addr + i, (hi << 4) | lo)


def test_cis_addn_zoned():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "12")
        _put_zoned(board, 0o2010, "34")
        cpu.state.R[0] = (0 << 12) | 2          # signed zoned, 2 digits
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (0 << 12) | 2
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (0 << 12) | 3          # dst: 3 digits
        cpu.state.R[5] = 0o3000
    cpu, board = _run([ADDN], setup, 1)
    assert _bytes(board, 0o3000, 3) == b"046"
    assert not cpu.state.psw.get(PSW.Z) and not cpu.state.psw.get(PSW.V)


def test_cis_subn_negative_result():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "10")
        _put_zoned(board, 0o2010, "25")
        cpu.state.R[0] = (0 << 12) | 2
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (0 << 12) | 2
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (0 << 12) | 3
        cpu.state.R[5] = 0o3000
    cpu, board = _run([SUBN], setup, 1)
    assert _bytes(board, 0o3000, 2) == b"01"
    assert _bytes(board, 0o3002, 1) == bytes([ord("5") | 0x80])   # negative zoned
    assert cpu.state.psw.get(PSW.N)
    assert cpu.state.psw.get(PSW.C)                               # borrow


def test_cis_addp_packed():
    def setup(cpu, board):
        _put_packed(board, 0o2000, "12")
        _put_packed(board, 0o2010, "34")
        cpu.state.R[0] = (6 << 12) | 2          # signed packed
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (6 << 12) | 2
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (6 << 12) | 4
        cpu.state.R[5] = 0o3000
    cpu, board = _run([ADDP], setup, 1)
    # 46 packed in 4 digits: digit nibbles 0..3, sign nibble 4 (hi of byte 2)
    assert _bytes(board, 0o3000, 3) == bytes([0x00, 0x46, 0xC0])


def test_cis_cmpn():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "50")
        _put_zoned(board, 0o2010, "50")
        cpu.state.R[0] = (1 << 12) | 2          # unsigned zoned
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (1 << 12) | 2
        cpu.state.R[3] = 0o2010
    cpu, board = _run([CMPN], setup, 1)
    assert cpu.state.psw.get(PSW.Z)
    assert not cpu.state.psw.get(PSW.C)


def test_cis_ashn_shift_left():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "12")
        cpu.state.R[0] = (0 << 12) | 2
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (0 << 12) | 4
        cpu.state.R[3] = 0o3000
        cpu.state.R[4] = 2                      # shift left 2 digits
    cpu, board = _run([ASHN], setup, 1)
    assert _bytes(board, 0o3000, 4) == b"1200"


def test_cis_mulp_divp_packed():
    def setup(cpu, board):
        _put_packed(board, 0o2000, "06")
        _put_packed(board, 0o2010, "07")
        cpu.state.R[0] = (6 << 12) | 2
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (6 << 12) | 2
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (6 << 12) | 4
        cpu.state.R[5] = 0o3000
    cpu, board = _run([MULP], setup, 1)
    assert _bytes(board, 0o3000, 3) == bytes([0x00, 0x42, 0xC0])   # 42
    cpu2, board2 = _run([DIVP], setup, 1)
    assert _bytes(board2, 0o3000, 3) == bytes([0x00, 0x00, 0xC0])  # 0 (6/7)


# --- CVT* conversions (SIMH pdp11_cis.c cases 053/054/055/057/073/077) -------

CVTNL, CVTPN, CVTNP, CVTLN = 0o076053, 0o076054, 0o076055, 0o076057
CVTPL, CVTLP = 0o076073, 0o076077
CVTNLI, CVTPNI, CVTNPI, CVTLNI = 0o076153, 0o076154, 0o076155, 0o076157
CVTPLI, CVTLPI = 0o076173, 0o076177


def test_cis_cvtnl_zoned_to_long():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "123")
        cpu.state.R[0] = (0 << 12) | 3          # signed zoned, 3 digits
        cpu.state.R[1] = 0o2000
    cpu, board = _run([CVTNL], setup, 1)
    assert cpu.state.R[2] == 0 and cpu.state.R[3] == 123
    assert cpu.state.R[0] == 0 and cpu.state.R[1] == 0
    assert not cpu.state.psw.get(PSW.N) and not cpu.state.psw.get(PSW.Z)
    assert not cpu.state.psw.get(PSW.V) and not cpu.state.psw.get(PSW.C)


def test_cis_cvtnl_negative_sets_n_and_c():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "45", sign_neg=True)
        cpu.state.R[0] = (0 << 12) | 2
        cpu.state.R[1] = 0o2000
    cpu, board = _run([CVTNL], setup, 1)
    r = (-45) & 0xFFFFFFFF
    assert cpu.state.R[2] == (r >> 16) & 0o177777
    assert cpu.state.R[3] == r & 0o177777
    assert cpu.state.psw.get(PSW.N) and cpu.state.psw.get(PSW.C)
    assert not cpu.state.psw.get(PSW.V) and not cpu.state.psw.get(PSW.Z)


def test_cis_cvtnl_overflow_sets_v():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "3000000000")  # > 2^31-1
        cpu.state.R[0] = (0 << 12) | 10
        cpu.state.R[1] = 0o2000
    cpu, board = _run([CVTNL], setup, 1)
    r = 3000000000 & 0xFFFFFFFF
    assert cpu.state.R[2] == (r >> 16) & 0o177777 and cpu.state.R[3] == r & 0o177777
    assert cpu.state.psw.get(PSW.V) and cpu.state.psw.get(PSW.N)
    assert not cpu.state.psw.get(PSW.C) and not cpu.state.psw.get(PSW.Z)


def test_cis_cvtnl_zero_sets_z():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "000")
        cpu.state.R[0] = (0 << 12) | 3
        cpu.state.R[1] = 0o2000
    cpu, board = _run([CVTNL], setup, 1)
    assert cpu.state.R[2] == 0 and cpu.state.R[3] == 0
    assert cpu.state.psw.get(PSW.Z) and not cpu.state.psw.get(PSW.N)
    assert not cpu.state.psw.get(PSW.V) and not cpu.state.psw.get(PSW.C)


def test_cis_cvtpl_reads_packed_regardless_of_declared_type():
    def setup(cpu, board):
        _put_packed(board, 0o2000, "00123")
        cpu.state.R[0] = (0 << 12) | 5          # declared zoned; CVTPL forces packed
        cpu.state.R[1] = 0o2000
    cpu, board = _run([CVTPL], setup, 1)
    assert cpu.state.R[2] == 0 and cpu.state.R[3] == 123
    assert not cpu.state.psw.get(PSW.V) and not cpu.state.psw.get(PSW.C)


def test_cis_cvtpn_packed_to_zoned():
    def setup(cpu, board):
        _put_packed(board, 0o2000, "00123")
        cpu.state.R[0] = (0 << 12) | 5          # declared zoned; CVTPN forces packed read
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (0 << 12) | 5          # dst: signed zoned, 5 digits
        cpu.state.R[3] = 0o3000
    cpu, board = _run([CVTPN], setup, 1)
    assert _bytes(board, 0o3000, 5) == b"00123"
    assert cpu.state.R[0] == 0 and cpu.state.R[1] == 0
    assert cpu.state.R[2] == (0 << 12) | 5 and cpu.state.R[3] == 0o3000
    assert not cpu.state.psw.get(PSW.N) and not cpu.state.psw.get(PSW.V)
    assert not cpu.state.psw.get(PSW.C)


def test_cis_cvtnp_zoned_to_packed():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "123")
        cpu.state.R[0] = (0 << 12) | 3
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (6 << 12) | 5          # dst: signed packed, 5 digits
        cpu.state.R[3] = 0o3000
    cpu, board = _run([CVTNP], setup, 1)
    assert _bytes(board, 0o3000, 3) == bytes([0x00, 0x12, 0x3C])
    assert not cpu.state.psw.get(PSW.N) and not cpu.state.psw.get(PSW.V)


def test_cis_cvtnp_negative_and_length_overflow():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "123", sign_neg=True)
        cpu.state.R[0] = (0 << 12) | 3
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (6 << 12) | 2          # dst: 2 packed digits
        cpu.state.R[3] = 0o3000
    cpu, board = _run([CVTNP], setup, 1)
    assert _bytes(board, 0o3000, 2) == bytes([0x23, 0xD0])   # -123 -> 23, sign D
    assert cpu.state.psw.get(PSW.N) and cpu.state.psw.get(PSW.V)  # sign kept, overflow
    assert not cpu.state.psw.get(PSW.C) and not cpu.state.psw.get(PSW.Z)


def test_cis_cvtln_long_to_zoned():
    def setup(cpu, board):
        cpu.state.R[0] = (0 << 12) | 5          # dst: signed zoned, 5 digits
        cpu.state.R[1] = 0o3000
        cpu.state.R[2] = 0
        cpu.state.R[3] = 123
    cpu, board = _run([CVTLN], setup, 1)
    assert _bytes(board, 0o3000, 5) == b"00123"
    assert cpu.state.R[2] == 0 and cpu.state.R[3] == 0
    assert not cpu.state.psw.get(PSW.N) and not cpu.state.psw.get(PSW.V)


def test_cis_cvtln_negative():
    def setup(cpu, board):
        cpu.state.R[0] = (0 << 12) | 3
        cpu.state.R[1] = 0o3000
        cpu.state.R[2] = 0o177777
        cpu.state.R[3] = (-45) & 0o177777
    cpu, board = _run([CVTLN], setup, 1)
    assert _bytes(board, 0o3000, 3) == b"04\xb5"           # '5' with sign bit
    assert cpu.state.psw.get(PSW.N) and not cpu.state.psw.get(PSW.Z)
    assert not cpu.state.psw.get(PSW.V) and not cpu.state.psw.get(PSW.C)


def test_cis_cvtlp_long_to_packed():
    def setup(cpu, board):
        cpu.state.R[0] = (6 << 12) | 5
        cpu.state.R[1] = 0o3000
        cpu.state.R[2] = 0
        cpu.state.R[3] = 123
    cpu, board = _run([CVTLP], setup, 1)
    assert _bytes(board, 0o3000, 3) == bytes([0x00, 0x12, 0x3C])
    assert cpu.state.R[2] == 0 and cpu.state.R[3] == 0


def test_cis_cvtnli_inline():
    # CVTNLI: inline words = src descriptor address, dest longword address
    cpu, board = make_machine("11/94", program=[CVTNLI, 0o2000, 0o3000], pc=0o100)
    _put_zoned(board, 0o2020, "123")
    board.ram.write_word(0o2000, (0 << 12) | 3)   # src descriptor
    board.ram.write_word(0o2002, 0o2020)
    step_n(cpu, 1)
    assert board.ram.read_word(0o3000) == 123              # low word first
    assert board.ram.read_word(0o3002) == 0                # high word
    assert cpu.state.R[Reg.PC] == 0o106                    # opcode + 2 inline words
    assert cpu.state.R[2] == 0 and cpu.state.R[3] == 0     # registers untouched


def test_cis_cvtlni_inline():
    # CVTLNI: inline words = dst descriptor address, source long descriptor
    cpu, board = make_machine("11/94", program=[CVTLNI, 0o2000, 0o2010], pc=0o100)
    board.ram.write_word(0o2000, (0 << 12) | 5)   # dst descriptor: zoned, 5
    board.ram.write_word(0o2002, 0o3000)
    board.ram.write_word(0o2010, 123)             # source long: low word
    board.ram.write_word(0o2012, 0)               # high word
    step_n(cpu, 1)
    assert _bytes(board, 0o3000, 5) == b"00123"
    assert cpu.state.R[Reg.PC] == 0o106


def test_cis_cvtpni_inline():
    # CVTPNI: inline words = src descriptor address, dst descriptor address
    cpu, board = make_machine("11/94", program=[CVTPNI, 0o2000, 0o2010], pc=0o100)
    _put_packed(board, 0o2020, "00123")
    board.ram.write_word(0o2000, (0 << 12) | 5)   # src declared zoned (forced packed)
    board.ram.write_word(0o2002, 0o2020)
    board.ram.write_word(0o2010, (0 << 12) | 5)   # dst descriptor: zoned, 5
    board.ram.write_word(0o2012, 0o3000)
    step_n(cpu, 1)
    assert _bytes(board, 0o3000, 5) == b"00123"
    assert cpu.state.R[Reg.PC] == 0o106


def test_cis_reserved_opcode_traps():
    cpu, board = make_machine("11/94", program=[0o076047], pc=0o100)  # reserved slot
    board.ram.write_word(0o000010, 0o004000)
    board.ram.write_word(0o000012, 0)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o004000


# --- decimal format coverage (zoned/overpunch/separate/packed, inline) ---

def _put_overpunch(board, addr, s, leading=False, neg=False):
    # s = digits; overpunch char on first (LO) or last (TO) byte
    from pdp11.cpu.instructions.cis import _binover
    raw = [ord(c) for c in s]
    idx = 0 if leading else len(raw) - 1
    raw[idx] = _binover(1 if neg else 0, raw[idx] - 0o060)
    for i, b in enumerate(raw):
        board.ram.write_byte(addr + i, b)


def test_cis_subn_overpunch_trailing():
    def setup(cpu, board):
        _put_overpunch(board, 0o2000, "25", leading=False, neg=False)   # +25 -> 'R'? no: 5 -> 'E'
        _put_overpunch(board, 0o2010, "10", leading=False, neg=True)    # -10
        cpu.state.R[0] = (2 << 12) | 2          # trailing overpunch
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (2 << 12) | 2
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (2 << 12) | 3
        cpu.state.R[5] = 0o3000
    cpu, board = _run([SUBN], setup, 1)
    # 25 - (-10) = 35 -> 3 digits: "03" + trailing overpunch 'E' (+5)
    assert _bytes(board, 0o3000, 3) == b"03E"


def test_cis_addn_leading_overpunch_negative():
    def setup(cpu, board):
        _put_overpunch(board, 0o2000, "05", leading=True, neg=True)     # -05
        _put_overpunch(board, 0o2010, "03", leading=True, neg=False)    # +03
        cpu.state.R[0] = (3 << 12) | 2          # leading overpunch
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (3 << 12) | 2
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (3 << 12) | 2
        cpu.state.R[5] = 0o3000
    cpu, board = _run([ADDN], setup, 1)
    # -5 + 3 = -2 -> 2 digits: leading overpunch '}' (-0), then '2'
    assert _bytes(board, 0o3000, 2) == b"}2"


def test_cis_addn_trailing_separate():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"12+")
        _put_str(board, 0o2010, b"34-")
        cpu.state.R[0] = (4 << 12) | 2          # trailing separate, 2 digits
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (4 << 12) | 2
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (4 << 12) | 3
        cpu.state.R[5] = 0o3000
    cpu, board = _run([ADDN], setup, 1)
    assert _bytes(board, 0o3000, 4) == b"022-"      # 12 + (-34) = -22


def test_cis_addn_leading_separate():
    def setup(cpu, board):
        _put_str(board, 0o2000, b"-07")
        _put_str(board, 0o2010, b"+09")
        cpu.state.R[0] = (5 << 12) | 2          # leading separate
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (5 << 12) | 2
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (5 << 12) | 2
        cpu.state.R[5] = 0o3000
    cpu, board = _run([ADDN], setup, 1)
    assert _bytes(board, 0o3000, 3) == b"+02"


def test_cis_unsigned_zoned_and_packed():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "99")         # unsigned zoned
        _put_packed(board, 0o2010, "01")
        cpu.state.R[0] = (1 << 12) | 2
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (7 << 12) | 2          # unsigned packed
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (7 << 12) | 3
        cpu.state.R[5] = 0o3000
    cpu, board = _run([ADDP], setup, 1)
    # 99 + 1 = 100 -> unsigned packed 3 digits: 0x10 0x00 0x0F
    assert _bytes(board, 0o3000, 2) == bytes([0x10, 0x0F])


def test_cis_subp_cmpp_packed():
    def setup(cpu, board):
        _put_packed(board, 0o2000, "50")
        _put_packed(board, 0o2010, "20")
        cpu.state.R[0] = (6 << 12) | 2
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (6 << 12) | 2
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (6 << 12) | 3
        cpu.state.R[5] = 0o3000
    cpu, board = _run([SUBP], setup, 1)
    assert _bytes(board, 0o3000, 2) == bytes([0x03, 0x0C])   # 30 packed in 3 digits
    cpu2, board2 = _run([CMPP], setup, 1)
    assert not cpu2.state.psw.get(PSW.Z) and not cpu2.state.psw.get(PSW.C)  # 50 > 20


def test_cis_ashp_right_shift_round():
    def setup(cpu, board):
        _put_packed(board, 0o2000, "159")
        cpu.state.R[0] = (6 << 12) | 3
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (6 << 12) | 3
        cpu.state.R[3] = 0o3000
        cpu.state.R[4] = 0o200 | 1 | (5 << 8)    # right 1 digit, round digit 5
    cpu, board = _run([ASHP], setup, 1)
    # 159 >> 1 digit with round 5 -> 16, packed in 3 digits: 0x01 0x6C
    assert _bytes(board, 0o3000, 2) == bytes([0x01, 0x6C])


def test_cis_addn_overflow_sets_v():
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "90")
        _put_zoned(board, 0o2010, "20")
        cpu.state.R[0] = (0 << 12) | 2
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (0 << 12) | 2
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (0 << 12) | 2          # dst only 2 digits: 110 overflows
        cpu.state.R[5] = 0o3000
    cpu, board = _run([ADDN], setup, 1)
    assert cpu.state.psw.get(PSW.V)


def test_cis_divp_by_zero():
    def setup(cpu, board):
        _put_packed(board, 0o2000, "10")
        _put_packed(board, 0o2010, "00")
        cpu.state.R[0] = (6 << 12) | 2
        cpu.state.R[1] = 0o2000
        cpu.state.R[2] = (6 << 12) | 2
        cpu.state.R[3] = 0o2010
        cpu.state.R[4] = (6 << 12) | 2
        cpu.state.R[5] = 0o3000
    cpu, board = _run([DIVP], setup, 1)
    assert cpu.state.psw.get(PSW.V) and cpu.state.psw.get(PSW.C)


def test_cis_addni_inline():
    ADDNI = 0o076150
    def setup(cpu, board):
        _put_zoned(board, 0o2000, "05")
        _put_zoned(board, 0o2010, "06")
        _put_zoned(board, 0o3000, "000")
        # descriptor list: desc addrs at 0o4000/0o4002/0o4004
        board.ram.write_word(0o4000, (0 << 12) | 2)
        board.ram.write_word(0o4002, 0o2000)
        board.ram.write_word(0o4004, (0 << 12) | 2)
        board.ram.write_word(0o4006, 0o2010)
        board.ram.write_word(0o4010, (0 << 12) | 3)
        board.ram.write_word(0o4012, 0o3000)
    cpu, board = _run([ADDNI, 0o4000, 0o4004, 0o4010], setup, 1)
    assert _bytes(board, 0o3000, 3) == b"011"
