"""
CIS — Commercial Instruction Set, character-string ops (spec §3 layout):
the 0o076xxx group (register operands) and its inline-operand variants
(0o076xxx | 0o100). Opcodes and semantics per SIMH pdp11_cis.c
(cross-checked against binutils pdp11-opc.c), op = IR<6:0>:

    0o020-0o027 L2DR   load 2 descriptors (R0-R3) from descriptor list at Rn
    0o030 MOVC / 0o031 MOVRC / 0o032 MOVTC   move char string (fwd/rev/xlate)
    0o040 LOCC / 0o041 SKPC                 locate / skip char
    0o042 SCANC / 0o043 SPANC               scan / span masked chars
    0o044 CMPC / 0o045 MATC                 compare strings / match count
    0o060-0o067 L3DR   load 3 descriptors (R0-R5)
    0o050-0o057, 0o070-0o077 (+inline 0o150-0o177)   packed-decimal ops —
    arithmetic implemented (ADDN/SUBN/CMPN/ADDP/SUBP/CMPP/MULP/DIVP/ASHN/
    ASHP); CVT*/CRC stay loud NotImplementedError (§19.10/§22.4)

String descriptor: word 0 = length in bytes, word 1 = starting byte address.
Register forms use R0:R1 = source descriptor, R2:R3 = dest descriptor,
R4 = fill/match char (R5 = mask-table address for SCANC/SPANC/MATC, xlate
table for MOVTC); inline forms take descriptor ADDRESSES as inline words
(fetched from I space) and leave R0-R5 untouched except where documented.

CIS runs to completion (no FPD restart state — SIMH notes CIS is
interruptible/restartable; v1 does not model that, §22.4). All data
accesses are D-space (fetch=False), inline operand words are I-space
(fetch=True) — §5.7.

On the 11/94 (J-11) CIS is on-chip; on the 11/03 only with KEF11; elsewhere
the capability gate traps these opcodes as reserved (§5.4/§5.8).
"""

from __future__ import annotations

from typing import Any

from pdp11.cpu import traps
from pdp11.cpu.core import CPU, SIGN_BIT, WORD_MASK, opcode
from pdp11.cpu.registers import PSW, Reg

_CIS_GROUP = 0o076000      # IR<15:8> == 0o76
_INLINE = 0o100            # IR<6> selects the inline-operand variant

_L2DR = 0o020              # L2DR/L3DR window
_L3DR = 0o060
_MOVC, _MOVRC, _MOVTC = 0o030, 0o031, 0o032
_LOCC, _SKPC = 0o040, 0o041
_SCANC, _SPANC, _CMPC, _MATC = 0o042, 0o043, 0o044, 0o045
# packed-decimal ops (binutils pdp11-opc.c, cross-checked vs SIMH pdp11_cis.c
# opntab): N-group 0o050-0o057, P-group 0o070-0o077, inline = op | 0o100
_ADDN, _SUBN, _CMPN, _CVTNL, _CVTPN, _CVTNP, _ASHN, _CVTLN = range(0o050, 0o060)
_ADDP, _SUBP, _CMPP, _CVTPL, _MULP, _DIVP, _ASHP, _CVTLP = range(0o070, 0o100)
_ARITH_OPS = frozenset({_ADDN, _SUBN, _CMPN, _ADDP, _SUBP, _CMPP,
                        _MULP, _DIVP, _ASHN, _ASHP})
_DECIMAL_OPS = frozenset(range(0o050, 0o060)) | frozenset(range(0o070, 0o100))

# decimal string types (descriptor word 0, <14:12>) — SIMH pdp11_cis.c
XZ, UZ, TO, LO, TS, LS, XP, UP = range(8)


def _overbin(c: int) -> int:
    """ASCII overpunch char -> digit | 0x80(negative) (SIMH overbin[])."""
    if 0o060 <= c <= 0o071:                  # '0'-'9' plain digits
        return c - 0o060
    if c == 0o173:                           # '{' = +0
        return 0
    if c in (0o175, 0o41, 0o72, 0o155):      # '}' '!' ':' '%' = -0
        return 0x80
    if 0o101 <= c <= 0o111:                  # 'A'-'I' = +1..+9
        return c - 0o101 + 1
    if 0o112 <= c <= 0o122:                  # 'J'-'R' = -1..-9
        return c - 0o112 + 1 + 0x80
    return 0


def _binover(sign: int, digit: int) -> int:
    """(sign, digit) -> ASCII overpunch char (SIMH binover[][])."""
    if digit == 0:
        return 0o173 if sign == 0 else 0o175
    if sign == 0:
        return 0o101 + digit - 1             # 'A'+d-1
    return 0o112 + digit - 1                 # 'J'+d-1


def _read_dstr(bus: Any, dsc: int, addr: int) -> tuple[int, int]:
    """Read a decimal string (descriptor word `dsc`, address `addr`) ->
    (sign, value); sign 1 = negative. All 8 CIS data types (SIMH ReadDstr)."""
    typ = (dsc >> 12) & 7
    n = dsc & 0o37                           # digit count
    if typ in (XP, UP):                      # packed: digit nibbles 0..n-1, sign nibble n
        nbytes = n // 2 + 1
        raw = [bus.read_byte((addr + i) & WORD_MASK) for i in range(nbytes)]
        digits = []
        for i in range(n):
            b = raw[i // 2]
            digits.append((b >> 4) & 0xF if i % 2 == 0 else b & 0xF)
        sb = raw[n // 2]
        sign_nib = (sb >> 4) & 0xF if n % 2 == 0 else sb & 0xF
        value = 0
        for d in digits:
            value = value * 10 + d
        sign = 1 if (typ == XP and sign_nib == 0xD) else 0
        return sign, value
    if typ in (TS, LS):                      # separate sign: n+1 bytes
        raw = [bus.read_byte((addr + i) & WORD_MASK) for i in range(n + 1)]
        sign = 1 if (raw[0] if typ == LS else raw[-1]) == 0o055 else 0
        body = raw[1:] if typ == LS else raw[:-1]
        value = 0
        for b in body:
            value = value * 10 + (b - 0o060)
        return sign, value
    if typ in (XZ, UZ):                      # zoned: ASCII digits, sign in bit 7 of last
        raw = [bus.read_byte((addr + i) & WORD_MASK) for i in range(n)]
        sign = 1 if (typ == XZ and (raw[-1] & 0x80)) else 0
        value = 0
        for b in raw:
            value = value * 10 + (b & 0x7F) - 0o060
        return sign, value
    if typ in (TO, LO):                      # overpunch at the end / start
        raw = [bus.read_byte((addr + i) & WORD_MASK) for i in range(n)]
        ob = _overbin(raw[0] if typ == LO else raw[-1])
        sign = 1 if ob & 0x80 else 0
        digit = ob & 0xF
        value = 0
        body = raw[1:] if typ == LO else raw[:-1]
        if typ == LO:
            value = digit
        for b in body:
            value = value * 10 + (b - 0o060)
        if typ == TO:
            value = value * 10 + digit
        return sign, value
    return 0, 0


def _write_dstr(bus: Any, dsc: int, addr: int, sign: int, value: int) -> None:
    """Write (sign, value) as a decimal string of descriptor `dsc` at `addr`
    (SIMH WriteDstr). Value is |value|; sign 1 = negative."""
    typ = (dsc >> 12) & 7
    n = dsc & 0o37
    digits = []
    v = value
    while v:
        digits.append(v % 10)
        v //= 10
    ds = digits[::-1][-n:] if n else []           # low n digits, high-first
    ds = [0] * (n - len(ds)) + ds                  # left-pad with zeros
    if typ in (XP, UP):
        nbytes = n // 2 + 1
        sign_nib = 0xD if (typ == XP and sign) else (0xC if typ == XP else 0xF)
        pout = bytearray(nbytes)
        for i in range(nbytes):
            if 2 * i < n:
                hi = ds[2 * i]
            elif 2 * i == n:
                hi = sign_nib
            else:
                hi = 0
            if 2 * i + 1 < n:
                lo = ds[2 * i + 1]
            elif 2 * i + 1 == n:
                lo = sign_nib
            else:
                lo = 0
            pout[i] = (hi << 4) | lo
        for i, b in enumerate(pout):
            bus.write_byte((addr + i) & WORD_MASK, b)
        return
    if typ in (TS, LS):
        body = bytes(0o060 + d for d in ds)
        if typ == LS:
            raw = bytes([0o055 if sign else 0o053]) + body
        else:
            raw = body + bytes([0o055 if sign else 0o053])
        for i, b in enumerate(raw):
            bus.write_byte((addr + i) & WORD_MASK, b)
        return
    if typ in (XZ, UZ):
        out = [0o060 + d for d in ds]
        if typ == XZ and sign:
            out[-1] |= 0x80
        for i, b in enumerate(out):
            bus.write_byte((addr + i) & WORD_MASK, b)
        return
    if typ in (TO, LO):
        out = [0o060 + d for d in ds]
        if typ == LO:
            out[0] = _binover(sign, out[0] - 0o060)
        else:
            out[-1] = _binover(sign, out[-1] - 0o060)
        for i, b in enumerate(out):
            bus.write_byte((addr + i) & WORD_MASK, b)
        return


def _dsc_operands(cpu: CPU, op: int, inline: bool) -> tuple[tuple[int, int], ...]:
    """Collect the descriptor (word0, addr) pairs for a decimal op:
    3-desc ops (ADD/SUB/MUL/DIV): src1, src2, dst; CMP: src1, src2;
    ASH: src, dst + shift arg in R4 / 3rd inline word."""
    st = cpu.state
    if op in (_ADDN, _SUBN, _ADDP, _SUBP, _MULP, _DIVP):
        n_desc = 3
    elif op in (_CMPN, _CMPP):
        n_desc = 2
    else:                                   # ASHN/ASHP
        n_desc = 2
    out: list[tuple[int, int]] = []
    if inline:
        for _ in range(n_desc):
            a = _inline_word(cpu)
            out.append(_descriptor(cpu, a))
    else:
        pairs = [(st.R[0], st.R[1]), (st.R[2], st.R[3]), (st.R[4], st.R[5])]
        out = pairs[:n_desc]
    return tuple(out)


def _decimal(cpu: CPU, op: int, inline: bool) -> None:
    """CIS packed/numeric decimal ops (binutils opcode map; SIMH pdp11_cis.c
    semantics via (sign, value) big-int conversion). Arithmetic ops are
    implemented; CVT*/CRC stay loud stubs (§19.10/§22.4)."""
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    psw = st.psw
    if op not in _ARITH_OPS:
        raise NotImplementedError(
            f"§5.4 CIS op 0o{0o076000 | (op | (0o100 if inline else 0)):06o} "
            "(CVT*/CRC) — deferred, flagged §22.4")
    descs = _dsc_operands(cpu, op, inline)
    if op in (_ASHN, _ASHP):
        s_dsc, s_addr = descs[0]
        d_dsc, d_addr = descs[1]
        arg = _inline_word(cpu) if inline else st.R[4]
        count = arg & 0o177                  # <6:0> shift count
        neg = bool(arg & 0o200)              # <7> 1 = right shift
        round_digit = (arg >> 8) & 0o17      # <11:8> round digit
        sign, value = _read_dstr(bus, s_dsc, s_addr)
        if not neg:
            shifted_out = value % (10 ** count) if count else 0
            value *= 10 ** count
            psw.set(PSW.V, shifted_out != 0)
            psw.set(PSW.C, False)
        else:
            div = 10 ** count
            q, r = divmod(value, div)
            if round_digit >= 5 and r * 2 >= div:
                q += 1
            psw.set(PSW.V, False)
            psw.set(PSW.C, r != 0)
            value = q
        if sign:
            value = -value
        dsign = 1 if value < 0 else 0
        _write_dstr(bus, d_dsc, d_addr, dsign, abs(value))
        _set_nz(psw, value & WORD_MASK)
        return
    src1 = _read_dstr(bus, descs[0][0], descs[0][1])
    src2 = _read_dstr(bus, descs[1][0], descs[1][1])
    v1 = -src1[1] if src1[0] else src1[1]
    v2 = -src2[1] if src2[0] else src2[1]
    if op in (_CMPN, _CMPP):
        d = v1 - v2
        _set_nz(psw, d & WORD_MASK)
        psw.set(PSW.V, False)
        psw.set(PSW.C, v1 < v2)
        return
    d_dsc, d_addr = descs[2]
    ndigits = d_dsc & 0o37
    if op in (_ADDN, _ADDP):
        result = v1 + v2
        carry = 1 if abs(result) >= 10 ** ndigits else 0
        psw.set(PSW.C, bool(carry))
    elif op in (_SUBN, _SUBP):
        result = v1 - v2
        psw.set(PSW.C, v1 < v2)
    elif op == _MULP:
        result = v1 * v2
        psw.set(PSW.C, abs(result) >= 10 ** ndigits)
    else:                                   # DIVP
        if v2 == 0:
            psw.set(PSW.V, True)
            psw.set(PSW.C, True)
            _set_nz(psw, 0)
            return
        result = abs(v1) // abs(v2)
        if (v1 < 0) != (v2 < 0):
            result = -result
        psw.set(PSW.C, False)
    psw.set(PSW.V, abs(result) >= 10 ** ndigits)
    dsign = 1 if result < 0 else 0
    _write_dstr(bus, d_dsc, d_addr, dsign, abs(result))
    _set_nz(psw, result & WORD_MASK)


def _set_nz(psw: PSW, value: int) -> None:
    psw.set(PSW.N, bool(value & SIGN_BIT))
    psw.set(PSW.Z, value == 0)


def _inline_word(cpu: CPU) -> int:
    """Fetch an inline operand word from I space (PC auto-increments)."""
    bus = cpu.bus
    assert bus is not None
    w = bus.read_word(cpu.state.R[Reg.PC], fetch=True)
    cpu.state.R[Reg.PC] = (cpu.state.R[Reg.PC] + 2) & WORD_MASK
    return w


def _descriptor(cpu: CPU, addr: int) -> tuple[int, int]:
    """Read a 2-word string descriptor at `addr` -> (length, byte address)."""
    bus = cpu.bus
    assert bus is not None
    return bus.read_word(addr), bus.read_word((addr + 2) & WORD_MASK)


def _movx(cpu: CPU, op: int, src: tuple[int, int], dst: tuple[int, int],
          fill: int, table: int | None) -> int:
    """MOVC/MOVRC/MOVTC core (CIS manual; SIMH movx_setup/movx_cleanup): move
    min(src.len,dst.len) bytes and pad the rest of dst with `fill`. MOVC
    copies forward from the start, or backward from the end when src < dst
    (overlap-safe); MOVRC writes the REVERSED string at the END of dst and
    fills the front. CC from src.len - dst.len. Returns max(0, src.len-dst.len)."""
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    sln, saddr0 = src
    dln, daddr0 = dst
    mvlnt = min(sln, dln)

    def _get(i: int) -> int:
        t = bus.read_byte((saddr0 + i) & WORD_MASK)
        if table is not None:              # MOVTC: translate via table
            t = bus.read_byte((table + t) & WORD_MASK)
        return t

    if op == _MOVRC:                       # reversed at the end of dst
        dbase = (daddr0 + dln - mvlnt) & WORD_MASK
        for i in range(mvlnt):
            bus.write_byte((dbase + (mvlnt - 1 - i)) & WORD_MASK, _get(i))
        fill_base = daddr0                 # front gets the fill
    elif saddr0 < daddr0:                  # MOVC backward (overlap-safe):
        for i in range(mvlnt):             # both ends step down together, so
            bus.write_byte((daddr0 + mvlnt - 1 - i) & WORD_MASK,   # dst[0:mvlnt]
                           _get(mvlnt - 1 - i))                     # = src[0:mvlnt]
        fill_base = (daddr0 + mvlnt) & WORD_MASK
    else:                                  # MOVC forward
        for i in range(mvlnt):
            bus.write_byte((daddr0 + i) & WORD_MASK, _get(i))
        fill_base = (daddr0 + mvlnt) & WORD_MASK
    for i in range(dln - mvlnt):
        bus.write_byte((fill_base + i) & WORD_MASK, fill)
    diff = (sln - dln) & WORD_MASK
    psw = st.psw
    _set_nz(psw, diff)
    psw.set(PSW.V, False)
    psw.set(PSW.C, sln < dln)              # borrow, as a 16-bit subtract
    return max(0, sln - dln)


def _locc(cpu: CPU, op: int, ln: int, addr: int, ch: int) -> tuple[int, int]:
    """LOCC/SKPC: scan until match (LOCC: c==ch) or mismatch (SKPC: c!=ch)."""
    bus = cpu.bus
    assert bus is not None
    while ln != 0:
        c = bus.read_byte(addr)
        if (c == ch) ^ bool(op & 1):
            break
        ln -= 1
        addr = (addr + 1) & WORD_MASK
    return ln, addr


def _scanc(cpu: CPU, op: int, ln: int, addr: int, mask: int,
           table: int) -> tuple[int, int]:
    """SCANC/SPANC: scan until (table[c] & mask) != 0 (SCANC) / == 0 (SPANC)."""
    bus = cpu.bus
    assert bus is not None
    while ln != 0:
        c = bus.read_byte(addr)
        t = bus.read_byte((table + c) & WORD_MASK)
        if bool((t & mask) != 0) ^ bool(op & 1):
            break
        ln -= 1
        addr = (addr + 1) & WORD_MASK
    return ln, addr


def _cmpc(cpu: CPU, ln1: int, ad1: int, ln2: int, ad2: int,
          fill: int) -> tuple[int, int, int, int]:
    """CMPC: compare two strings, padding the shorter with `fill`. CC from
    the first differing byte (C = borrow); all-equal sets Z."""
    bus = cpu.bus
    assert bus is not None
    st = cpu.state
    c = t = 0
    while ln1 or ln2:
        c = bus.read_byte(ad1) if ln1 else fill
        t = bus.read_byte(ad2) if ln2 else fill
        if c != t:
            break
        if ln1:
            ln1 -= 1
            ad1 = (ad1 + 1) & WORD_MASK
        if ln2:
            ln2 -= 1
            ad2 = (ad2 + 1) & WORD_MASK
    psw = st.psw
    if ln1 == 0 and ln2 == 0:
        psw.value = (psw.value & ~0o17) | PSW.Z      # equal: Z=1, N=V=C=0
    else:
        d = (c - t) & 0o377
        psw.set(PSW.N, bool(d & 0o200))
        psw.set(PSW.Z, d == 0)
        psw.set(PSW.V, False)
        psw.set(PSW.C, c < t)
    return ln1, ad1, ln2, ad2


@opcode(_CIS_GROUP, mask=0o177400, caps="CIS", fmt="none")   # whole 0o076xxx CIS group
def instr_CIS(cpu: CPU) -> None:
    """0o076xxx: CIS group (J-11 on-chip; KH11 on 11/70; KEF11 on 11/03) —
    op = IR<6:0>, bit 6 (0o100) = inline operands. String ops implemented;
    packed-decimal ops raise NotImplementedError (§22.4)."""
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    ir = cpu.last_op_word
    op = ir & 0o177
    inline = bool(op & _INLINE)
    op &= ~_INLINE

    if _L2DR <= op < _L2DR + 8 or _L3DR <= op < _L3DR + 8:      # L2DR / L3DR
        rn = ir & 0o7
        limit = 6 if op >= _L3DR else 4
        t = st.R[rn]
        for j in range(0, limit, 2):
            addr = bus.read_word((t + j) & WORD_MASK, fetch=(rn == Reg.PC))
            st.R[j] = bus.read_word(addr)
            st.R[j + 1] = bus.read_word((addr + 2) & WORD_MASK)
        if rn >= limit:
            st.R[rn] = (st.R[rn] + limit) & WORD_MASK
        return

    if op in (_MOVC, _MOVRC, _MOVTC):                            # move string
        if inline:
            # MOVCI/MOVRCI: 3 inline words (src desc addr, dst desc addr, fill);
            # MOVTCI: a 4th inline word (translation table address)
            a1 = _inline_word(cpu)
            a2 = _inline_word(cpu)
            a3 = _inline_word(cpu)
            src = _descriptor(cpu, a1)
            dst = _descriptor(cpu, a2)
            fill = a3 & 0o377
            table = _inline_word(cpu) if op == _MOVTC else None
            _movx(cpu, op, src, dst, fill, table)
        else:
            src = (st.R[0], st.R[1])
            dst = (st.R[2], st.R[3])
            fill = st.R[4] & 0o377
            table = st.R[5] if op == _MOVTC else None
            left = _movx(cpu, op, src, dst, fill, table)
            st.R[0] = left & WORD_MASK                          # register form only
            st.R[1] = st.R[2] = st.R[3] = 0
        return

    if op in (_LOCC, _SKPC):                                    # locate/skip char
        if inline:
            a1 = _inline_word(cpu)
            a2 = _inline_word(cpu)
            ln, addr = _descriptor(cpu, a1)
            ch = a2 & 0o377
            saved = st.R[4]
            ln, addr = _locc(cpu, op, ln, addr, ch)
            st.R[0], st.R[1] = ln, addr
            st.R[4] = saved
        else:
            ln, addr = _locc(cpu, op, st.R[0], st.R[1], st.R[4] & 0o377)
            st.R[0], st.R[1] = ln, addr
        _set_nz(st.psw, st.R[0])
        st.psw.set(PSW.V, False)
        st.psw.set(PSW.C, False)
        return

    if op in (_SCANC, _SPANC):                                  # scan/span masked
        if inline:
            a1 = _inline_word(cpu)
            a2 = _inline_word(cpu)
            a3 = _inline_word(cpu)
            ln, addr = _descriptor(cpu, a1)
            mask = a2 & 0o377
            table = a3
            s4, s5 = st.R[4], st.R[5]
            ln, addr = _scanc(cpu, op, ln, addr, mask, table)
            st.R[0], st.R[1] = ln, addr
            st.R[4], st.R[5] = s4, s5
        else:
            ln, addr = _scanc(cpu, op, st.R[0], st.R[1], st.R[4] & 0o377, st.R[5])
            st.R[0], st.R[1] = ln, addr
        _set_nz(st.psw, st.R[0])
        st.psw.set(PSW.V, False)
        st.psw.set(PSW.C, False)
        return

    if op == _CMPC:                                             # compare strings
        if inline:
            a1 = _inline_word(cpu)
            a2 = _inline_word(cpu)
            a3 = _inline_word(cpu)
            l1, a1 = _descriptor(cpu, a1)
            l2, a2 = _descriptor(cpu, a2)
            fill = a3 & 0o377
            _cmpc(cpu, l1, a1, l2, a2, fill)
        else:
            _cmpc(cpu, st.R[0], st.R[1], st.R[2], st.R[3], st.R[4] & 0o377)
        return

    if op == _MATC:                                             # match count
        if inline:
            a1 = _inline_word(cpu)
            a2 = _inline_word(cpu)
            a3 = _inline_word(cpu)
            ln, addr = _descriptor(cpu, a1)
            mask = a2 & 0o377
            table = a3
        else:
            ln, addr = st.R[0], st.R[1]
            mask = st.R[4] & 0o377
            table = st.R[5]
        count = 0
        for _ in range(ln):
            c = bus.read_byte(addr)
            if bus.read_byte((table + c) & WORD_MASK) & mask:
                count += 1
            addr = (addr + 1) & WORD_MASK
        st.R[0] = count & WORD_MASK
        st.R[1] = addr
        _set_nz(st.psw, count)
        st.psw.set(PSW.V, False)
        st.psw.set(PSW.C, False)
        return

    if op in _DECIMAL_OPS:                                      # ADDN..CVTLP, CRC
        _decimal(cpu, op, inline)
        return

    traps.reserved_instruction(cpu)                             # unassigned 0o076xxx
