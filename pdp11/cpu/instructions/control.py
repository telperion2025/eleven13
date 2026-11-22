"""
Control instructions (spec §3 layout; Processor Handbook group 0):
HALT 0o000000, WAIT 0o000001, RTI 0o000002, BPT 0o000003, IOT 0o000004,
RESET 0o000005, RTT 0o000006, the condition-code operators 0o000240-0o000277
(NOP = 0o000240), plus the MMU-gated MTPS 0o1064dd / MFPS 0o1067dd which the
11/20 consumes as no-ops (spec §2, §5.4).

RTI/RTT pop the saved PSW then PC (reverse of the trap push order, §5.5).
BPT traps through 000014 (shared with the T-bit on the 11/20), IOT through
000020. RESET asserts bus init: every attached device's reset() runs (§6.1);
on the 11/20 the PSW itself is not modified. HALT parks the CPU (halted flag);
the ODT console takes over in M2 (§9). WAIT parks until an interrupt (§5.5).
"""

from __future__ import annotations

from pdp11.cpu import traps
from pdp11.cpu.core import CPU, opcode


@opcode(0o000000, fmt="none")
def instr_HALT(cpu: CPU) -> None:
    """HALT: stop the CPU; on real hardware control falls to the console
    (ODT, §9 — wired up in M2). M1: set the halted flag only."""
    cpu.state.halted = True


@opcode(0o000001, fmt="none")
def instr_WAIT(cpu: CPU) -> None:
    """WAIT: park the CPU until an interrupt outranks the PSW IPL (§5.5);
    interrupts.service() in CPU.step wakes it."""
    cpu.state.waiting = True


@opcode(0o000002, fmt="none")
def instr_RTI(cpu: CPU) -> None:
    """RTI: PSW <- (SP)+, PC <- (SP)+ — return from trap/interrupt."""
    from pdp11.cpu.registers import Reg
    st = cpu.state
    st.psw.value = traps.pop_word(cpu)
    st.R[Reg.PC] = traps.pop_word(cpu) & 0o177777


@opcode(0o000003, fmt="none")
def instr_BPT(cpu: CPU) -> None:
    """BPT: breakpoint trap through vector 000014 (shared with T-bit)."""
    traps.trap(cpu, traps.VECTOR_TRACE)


@opcode(0o000004, fmt="none")
def instr_IOT(cpu: CPU) -> None:
    """IOT: I/O trap through vector 000020 (§5.5)."""
    traps.trap(cpu, traps.VECTOR_IOT)


@opcode(0o000005, fmt="none")
def instr_RESET(cpu: CPU) -> None:
    """RESET: bus init — reset every attached device (§6.1); the PSW is
    unchanged on the 11/20. The ~10 ms bus-init delay is modeled by the bus
    layer's cycle accounting in M2+ (§5.6), not by a wall clock."""
    for dev in cpu.devices:
        dev.reset()


@opcode(0o000006, fmt="none")
def instr_RTT(cpu: CPU) -> None:
    """RTT: like RTI on the 11/20 (restores PSW including the T bit, then PC)."""
    instr_RTI(cpu)


@opcode(0o000200, mask=0o177700, fmt="none")      # 0o000240-0o000277: NOP + condition codes
def instr_COND(cpu: CPU) -> None:
    """
    Condition-code operators. Bits 3-0 of the low byte select N/Z/V/C;
    bit 5 selects set (1) vs clear (0). 0o000240 is NOP (no flag bits).
    Processor Handbook, condition code instruction table.
    """
    word = cpu.last_op_word
    flags = word & 0o17                           # 0o01=C 0o02=V 0o04=Z 0o10=N
    if flags == 0:                                # NOP (0o000240)
        return
    set_op = bool(word & 0o40)                    # 0o0002xx clears, 0o0002[6|7]x sets
    cpu.state.psw.set(flags, set_op)


@opcode(0o106400, mask=0o177700, caps="MMU", fmt="dst")   # MTPS (11/45+; no-op on 11/20, §2)
def instr_MTPS(cpu: CPU) -> None:
    """MTPS: move to PSW — MMU-era instruction (M5/M6, §5.7). On the 11/20 the
    capability gate no-ops it (KD11B_NOOP_HANDLERS, §5.4)."""
    raise NotImplementedError("§5.7 MTPS — M5/M6 (11/70, 11/94)")


@opcode(0o106700, mask=0o177700, caps="MMU", fmt="dst")   # MFPS (11/45+; no-op on 11/20, §2)
def instr_MFPS(cpu: CPU) -> None:
    """MFPS: move from PSW — MMU-era instruction (M5/M6, §5.7). On the 11/20
    the capability gate no-ops it (§5.4)."""
    raise NotImplementedError("§5.7 MFPS — M5/M6 (11/70, 11/94)")


@opcode(0o006400, mask=0o177700, caps="MMU", fmt="dst")   # MARK/SPL share 0o0064xx; split in M5/M6
def instr_MARK_SPL(cpu: CPU) -> None:
    """MARK 0o0064dd / SPL 0o0064xx — 11/45+ instructions sharing the 0o0064
    space; the per-machine split (KD11-Z vs DCJ-11) lands with M5/M6 per the
    per-machine handbooks (§21). On the 11/20: no-op (spec §2, §5.4)."""
    raise NotImplementedError("§2 MARK/SPL 0o0064xx — M5/M6 (no-op on 11/20 via capability gate)")


@opcode(0o006500, mask=0o177700, caps="MMU", fmt="dst")   # MFPI 0o0065dd
def instr_MFPI(cpu: CPU) -> None:
    """MFPI: move from previous I/D space — MMU-only (§5.7)."""
    raise NotImplementedError("§5.7 MFPI — M5/M6 (11/70, 11/94)")


@opcode(0o006600, mask=0o177700, caps="MMU", fmt="dst")   # MTPI 0o0066dd
def instr_MTPI(cpu: CPU) -> None:
    """MTPI: move to previous I/D space — MMU-only (§5.7)."""
    raise NotImplementedError("§5.7 MTPI — M5/M6 (11/70, 11/94)")


@opcode(0o106500, mask=0o177700, caps="MMU", fmt="dst")   # MTPD 0o1065dd
def instr_MTPD(cpu: CPU) -> None:
    """MTPD: move to previous D space — MMU-only (§5.7)."""
    raise NotImplementedError("§5.7 MTPD — M5/M6 (11/70, 11/94)")


@opcode(0o106600, mask=0o177700, caps="MMU", fmt="dst")   # MFPD 0o1066dd
def instr_MFPD(cpu: CPU) -> None:
    """MFPD: move from previous D space — MMU-only (§5.7)."""
    raise NotImplementedError("§5.7 MFPD — M5/M6 (11/70, 11/94)")
