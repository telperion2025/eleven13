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
    0o050-0o057, 0o070-0o077, 0o150-0o177    packed-decimal ops + CRC — NOT
    implemented in v1 (loud NotImplementedError, §19.10/§22.4; spec §3 scopes
    CIS to the string ops and §22 R4 stages the decimal set later)

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
_DECIMAL_OPS = frozenset(range(0o050, 0o060)) | frozenset(range(0o070, 0o100))


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
        raise NotImplementedError(
            "§5.4 CIS packed-decimal ops (ADDN/SUBN/CMPN/CVT*/ASH*/ADDP/…/CRC) "
            "— deferred past M6, flagged §22.4 (spec §3 scopes CIS to string ops)")

    traps.reserved_instruction(cpu)                             # unassigned 0o076xxx
