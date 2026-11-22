"""
EIS — Extended Instruction Set (spec §3 layout): MUL 0o070rss, DIV 0o071rss,
ASH 0o072rss, ASHC 0o073rss, XOR 0o074rdd, SOB 0o077rdd, plus FIS 0o075xxx
on the LSI-11 (KEF11). SXT 0o0067dd rides this group's capability tag.

M1 implements only SXT. MUL/DIV/ASH/ASHC/XOR/SOB/FIS are registered so the
capability matrix (§5.4) behaves correctly — on the 11/20 (no EIS cap) they
are consumed as no-ops (spec §2), on EIS-fitted machines they raise
NotImplementedError until their milestone lands (M4: 11/03 KEF11; M5: 11/70;
M6: 11/94), per the §19.10 wiring-restraint rule: never a fake implementation.

References: PDP-11 Processor Handbook (EIS section); LSI-11/KEF11 docs.
"""

from __future__ import annotations

from pdp11.cpu.core import CPU, WORD_MASK, opcode, resolve_operand, write_op
from pdp11.cpu.registers import PSW


@opcode(0o070000, mask=0o177000, caps="EIS", fmt="src")   # MUL
def instr_MUL(cpu: CPU) -> None:
    """MUL src,r: 32-bit signed product into r,r+1 (r even). Not on 11/20."""
    raise NotImplementedError("§5.3 MUL — M4 (11/03 KEF11) / M5-M6")


@opcode(0o071000, mask=0o177000, caps="EIS", fmt="src")   # DIV
def instr_DIV(cpu: CPU) -> None:
    """DIV src,r: 32-bit dividend in r,r+1 divided by src. Not on 11/20."""
    raise NotImplementedError("§5.3 DIV — M4 (11/03 KEF11) / M5-M6")


@opcode(0o072000, mask=0o177000, caps="EIS", fmt="src")   # ASH
def instr_ASH(cpu: CPU) -> None:
    """ASH src,r: r shifted arithmetically by src (6-bit signed count)."""
    raise NotImplementedError("§5.3 ASH — M4 (11/03 KEF11) / M5-M6")


@opcode(0o073000, mask=0o177000, caps="EIS", fmt="src")   # ASHC
def instr_ASHC(cpu: CPU) -> None:
    """ASHC src,r: 32-bit combined shift of r,r+1. Not on 11/20."""
    raise NotImplementedError("§5.3 ASHC — M4 (11/03 KEF11) / M5-M6")


@opcode(0o074000, mask=0o177000, caps="EIS", fmt="src_dst")  # XOR 0o074rdd
def instr_XOR(cpu: CPU) -> None:
    """XOR src,dst: dst <- dst XOR src. No-op on the KD11-B (spec §2)."""
    raise NotImplementedError("§2/§5.4 XOR — M5-M6 (no-op on 11/20 via capability gate)")


@opcode(0o075000, mask=0o177000, caps="EIS", fmt="src")   # FIS 0o075xxx (LSI-11)
def instr_FIS(cpu: CPU) -> None:
    """FIS floating-integer instructions (LSI-11 KEF11): FADD etc."""
    raise NotImplementedError("§5.3 FIS — M4 (11/03 KEF11)")


@opcode(0o077000, mask=0o177000, caps="EIS", fmt="dst")   # SOB 0o077rdd
def instr_SOB(cpu: CPU) -> None:
    """SOB r,addr: r <- r-1; branch to addr if r != 0. Not on 11/20."""
    raise NotImplementedError("§2 SOB — M4+ (no-op on 11/20 via capability gate)")


@opcode(0o006700, mask=0o177700, caps="EIS", fmt="dst")   # SXT 0o0067dd
def instr_SXT(cpu: CPU) -> None:
    """SXT dst: dst <- 0o177777 if N else 0 (sign extend). Flags unchanged.
    Present on the LSI-11; consumed as a no-op on the KD11-B (spec §2)."""
    dst_spec = cpu.last_op_word & 0o77
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    ea = resolve_operand(st, bus, dst_spec, False, False)
    result = WORD_MASK if st.psw.get(PSW.N) else 0   # sign of N into the whole word
    write_op(cpu, dst_spec, ea, result, False)
