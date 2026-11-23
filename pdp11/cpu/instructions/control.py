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
from pdp11.cpu.core import CPU, opcode, read_op, resolve_operand, write_op
from pdp11.cpu.registers import PSW, Reg


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
    """RTI: PSW <- (SP)+, PC <- (SP)+ — return from trap/interrupt. On MMU
    machines the mode switch happens after both pops (the saved SP bank is
    restored via switch_mode, §5.1)."""
    st = cpu.state
    old_cm = (st.psw.value & PSW.Curr_MODE) >> 14
    st.psw.value = traps.pop_word(cpu)
    st.R[Reg.PC] = traps.pop_word(cpu) & 0o177777
    if cpu.profile.has_mmu:
        st.switch_mode((st.psw.value & PSW.Curr_MODE) >> 14, old_cm)


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


@opcode(0o000230, mask=0o177770, caps="MMU", fmt="none")    # SPL 0o00023m (11/45+; no-op on 11/20)
def instr_SPL(cpu: CPU) -> None:
    """SPL m: set interrupt priority level — PSW<7:5> <- m (Lions' commentary:
    "spl set IPL to m 00023m"). On the 11/20 the capability gate no-ops it
    (KD11B_NOOP_HANDLERS, §5.4)."""
    psw = cpu.state.psw
    psw.value = (psw.value & ~PSW.IPL_MASK) | ((cpu.last_op_word & 0o7) << 5)


@opcode(0o106400, mask=0o177700, caps="MMU", fmt="dst")   # MTPS 0o1064dd (no-op on 11/20)
def instr_MTPS(cpu: CPU) -> None:
    """MTPS dd: move BYTE to PSW — PSW<7:0> <- src<7:0> (IPL/T/CC only; the
    mode bits live in the high byte and are not touched here, §5.7)."""
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    spec = cpu.last_op_word & 0o77
    ea = resolve_operand(st, bus, spec, True, True)
    val = read_op(cpu, spec, ea, True)
    psw = st.psw
    psw.value = (psw.value & 0o177400) | (val & 0o377)


@opcode(0o106700, mask=0o177700, caps="MMU", fmt="dst")   # MFPS 0o1067dd (no-op on 11/20)
def instr_MFPS(cpu: CPU) -> None:
    """MFPS dd: move BYTE from PSW — dst<7:0> <- PSW<7:0> (§5.7)."""
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    spec = cpu.last_op_word & 0o77
    ea = resolve_operand(st, bus, spec, False, True)
    write_op(cpu, spec, ea, st.psw.value & 0o377, True)


@opcode(0o006400, mask=0o177700, caps="MMU", fmt="none")  # MARK 0o0064nn (no-op on 11/20)
def instr_MARK_SPL(cpu: CPU) -> None:
    """MARK nn (0o0064nn): SP <- PC + 2*nn ; PC <- R5 ; R5 <- (SP)+ — the
    caller's MARK frame is executed on the stack by the return routine
    (Lions' PDP-11 commentary). On the 11/20: no-op (§2, §5.4)."""
    st = cpu.state
    n = cpu.last_op_word & 0o77
    st.R[Reg.SP] = (st.R[Reg.PC] + 2 * n) & 0o177777
    st.R[Reg.PC] = st.R[5] & 0o177777
    st.R[5] = traps.pop_word(cpu)


def _prev_mode(cpu: CPU) -> int:
    """The PSW previous-mode field <13:12> (11/70/94, §5.1)."""
    return (cpu.state.psw.value & PSW.Prev_MODE) >> 12


def _prev_space_op(cpu: CPU, spec: int, *, to_prev: bool, is_instruction: bool) -> None:
    """MFPI/MTPI/MFPD/MTPD core: move one word between the current address
    space and the PREVIOUS mode's I or D space (§5.7)."""
    st = cpu.state
    bus = cpu.bus
    assert bus is not None
    ea = resolve_operand(st, bus, spec, not to_prev, False)
    prev = _prev_mode(cpu)
    if to_prev:                                # MTPI/MTPD: write previous space
        val = read_op(cpu, spec, ea, False)
        if ea < 0:
            write_op(cpu, spec, ea, val, False)
        else:
            bus.write_word(ea, val, fetch=is_instruction, mode=prev)
    else:                                      # MFPI/MFPD: read previous space
        if ea < 0:
            val = st.R[spec & 0o7]
        else:
            val = bus.read_word(ea, fetch=is_instruction, mode=prev)
        write_op(cpu, spec, ea, val, False)


@opcode(0o006500, mask=0o177700, caps="MMU", fmt="dst")   # MFPI 0o0065dd
def instr_MFPI(cpu: CPU) -> None:
    """MFPI dd: dst <- previous mode's I space (§5.7)."""
    _prev_space_op(cpu, cpu.last_op_word & 0o77, to_prev=False, is_instruction=True)


@opcode(0o006600, mask=0o177700, caps="MMU", fmt="dst")   # MTPI 0o0066dd
def instr_MTPI(cpu: CPU) -> None:
    """MTPI dd: previous mode's I space <- src (§5.7)."""
    _prev_space_op(cpu, cpu.last_op_word & 0o77, to_prev=True, is_instruction=True)


@opcode(0o106500, mask=0o177700, caps="MMU", fmt="dst")   # MTPD 0o1065dd
def instr_MTPD(cpu: CPU) -> None:
    """MTPD dd: previous mode's D space <- src (§5.7)."""
    _prev_space_op(cpu, cpu.last_op_word & 0o77, to_prev=True, is_instruction=False)


@opcode(0o106600, mask=0o177700, caps="MMU", fmt="dst")   # MFPD 0o1066dd
def instr_MFPD(cpu: CPU) -> None:
    """MFPD dd: dst <- previous mode's D space (§5.7)."""
    _prev_space_op(cpu, cpu.last_op_word & 0o77, to_prev=False, is_instruction=False)
