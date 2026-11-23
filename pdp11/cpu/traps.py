"""
Trap dispatch: vector lookup, PSW/PC save and restore (spec §5.5).

A trap is the PDP-11's subroutine-call-to-the-operating-system mechanism:
the CPU pushes the current PC and PSW on the stack (R6), then loads the
new PC from the vector word and the new PSW from the vector word + 2.
On machines without modes (11/03, 11/20) there is a single stack and no
mode switch; the 11/70/94 three-mode behavior (per-mode SP, MMR0 abort
recording) lands with M5/M6 behind profile.has_mmu (spec §5.7).

Standard vectors (spec §5.5; PDP-11 Processor Handbook, trap vector table):
    000004  bus error / Unibus timeout
    000010  illegal / reserved instruction
    000014  T-bit trace trap (shared with BPT on the 11/20)
    000020  IOT
    000024  power fail / power restore (spec §5.5, §13)
    000030  EMT
    000034  TRAP
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdp11.cpu.mmu import MD_KER
from pdp11.cpu.registers import PSW, Reg
from pdp11.errors import EmulationError

if TYPE_CHECKING:  # pragma: no cover — typing only, avoids a runtime cycle
    from pdp11.cpu.core import CPU, MemoryInterface


def _bus(cpu: CPU) -> MemoryInterface:
    """The CPU's memory interface; traps cannot run without a bus attached."""
    bus = cpu.bus
    if bus is None:
        raise EmulationError("trap with no bus attached: board must inject MemoryInterface")
    return bus

# Vector table — octal literals per §10.1 (spec §5.5).
VECTOR_BUS_ERROR = 0o000004
VECTOR_RESERVED = 0o000010
VECTOR_TRACE = 0o000014      # T-bit; BPT shares this vector on the 11/20
VECTOR_IOT = 0o000020
VECTOR_POWER_FAIL = 0o000024
VECTOR_EMT = 0o000030
VECTOR_TRAP = 0o000034


def push_word(cpu: CPU, value: int) -> None:
    """Push a word on the stack: -(SP) then store — address mode 4 with R6."""
    st = cpu.state
    sp = (st.R[Reg.SP] - 2) & 0o177777       # SP pre-decrements by one word (2 bytes)
    st.R[Reg.SP] = sp
    _bus(cpu).write_word(sp, value & 0o177777)


def pop_word(cpu: CPU) -> int:
    """Pop a word from the stack: load (SP)+ — address mode 2 with R6."""
    st = cpu.state
    sp = st.R[Reg.SP]
    value = _bus(cpu).read_word(sp)
    st.R[Reg.SP] = (sp + 2) & 0o177777
    return value


def enter_vector(cpu: CPU, vector: int, pc: int, psw: int) -> None:
    """
    Common trap/interrupt entry (§5.5): vector through `vector`, pushing the
    old PC then PSW (RTI pops symmetrically).

    On MMU machines (11/70/94) the vector is fetched from KERNEL D space
    (§5.7), the previous-mode field records the old current mode, and the
    old PC/PSW are pushed on the NEW mode's stack — R6 is banked per mode
    (§5.1 "separate SP per mode"). On machines without modes the mode bits
    in the loaded PSW are ignored (11/20: they read back 0 — PSW docstring).
    """
    st = cpu.state
    bus = _bus(cpu)
    if cpu.profile.has_mmu:
        old_cm = (st.psw.value & PSW.Curr_MODE) >> 14
        new_pc = bus.read_word(vector & 0o177777, mode=MD_KER) & 0o177777
        new_psw = bus.read_word((vector + 2) & 0o177777, mode=MD_KER) & 0o177777
        # previous mode <- old current mode; current mode from the vector PSW
        st.psw.value = (new_psw & ~PSW.Prev_MODE) | ((old_cm << 12) & PSW.Prev_MODE)
        st.switch_mode((st.psw.value & PSW.Curr_MODE) >> 14, old_cm)
        push_word(cpu, pc)                    # old PC saved first (deepest)
        push_word(cpu, psw)                   # then old PSW on top
        st.R[Reg.PC] = new_pc
    else:
        push_word(cpu, pc)
        push_word(cpu, psw)
        st.R[Reg.PC] = bus.read_word(vector) & 0o177777
        st.psw.value = bus.read_word((vector + 2) & 0o177777)
        st.psw.set(PSW.Curr_MODE | PSW.Prev_MODE, False)


def trap(cpu: CPU, vector: int, saved_pc: int | None = None) -> None:
    """
    Dispatch a trap through `vector` (octal).

    saved_pc defaults to the current PC; bus errors pass the address of the
    instruction that was executing when the bus faulted, because the PC has
    already advanced past the instruction word (spec §17.7 saves instr_addr
    for exactly this reason).

    The stack order is fixed by hardware: PC first, then PSW, so that RTI
    (which pops PSW then PC) returns symmetrically.
    """
    st = cpu.state
    pc = st.R[Reg.PC] if saved_pc is None else saved_pc
    enter_vector(cpu, vector, pc, st.psw.value)


def bus_error(cpu: CPU, saved_pc: int) -> None:
    """Unibus/Q-Bus timeout: nonexistent or unmapped access (§5.5).
    Routed here with the faulting instruction's address."""
    trap(cpu, VECTOR_BUS_ERROR, saved_pc)


def reserved_instruction(cpu: CPU) -> None:
    """Illegal/reserved opcode: vector 000010 (§5.5). The PC pushed is the
    address AFTER the reserved word — hardware has already fetched it."""
    trap(cpu, VECTOR_RESERVED)
