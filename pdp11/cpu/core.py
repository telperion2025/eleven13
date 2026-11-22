"""
CPU core: fetch, decode, execute loop. M1 scope: 11/20 BASE instructions
over flat memory only (§12 milestone M1) — no MMU, no EIS, byte and word
modes complete on the single- and double-operand groups.

Every instruction has a per-machine cycle COST used to advance the
SystemClock (§5.6). Costs follow the 11/20 column of the Processor
Handbook timing tables; the 11/03/70/94 columns arrive in M4-M6.

Dispatch design (§5.3, §14): the @opcode decorator registers handlers in a
module-level registry; CPU.__init__ renders it into per-nibble buckets
(op_word >> 12) so the fetch path is a dict lookup plus a short masked scan
— a prebuilt table beats method resolution in the hot loop. Group 0o0xxx
(control, single-operand, branches, JSR/RTS, EIS) is the large secondary
bucket decoded by masked patterns, exactly the hardware's two-level decode.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple, Protocol

from pdp11.bus.abstract_bus import BusDevice, BusInit
from pdp11.config.profiles import CpuCap, MachineProfile
from pdp11.cpu import interrupts, traps
from pdp11.cpu.registers import PSW, CPUState, Reg
from pdp11.errors import EmulationError
from pdp11.system_clock import SystemClock

# Operand-specifier bit fields (§5.2) — octal literals per §10.1.
DEC_ADDR_MODE = 0o70  # mask covering mode bits <5:3> of an operand specifier
DEC_REG_NUM = 0o07    # mask covering register bits <2:0> of an operand specifier

WORD_MASK = 0o177777  # PDP-11 arithmetic is always modulo 0o200000 (16-bit)
BYTE_MASK = 0o377
SIGN_BIT = 0o100000   # bit 15: the sign of a 16-bit word

# Per-instruction BASE bus-cycle cost keyed by opcode group (op_word >> 12),
# per §17.10 step 6 — filled at implementation time, never retrofitted.
# Values: PDP-11/20 column of the Processor Handbook timing table expressed
# in bus cycles (1 Unibus cycle ≈ 150 ns); each operand memory transfer is
# counted separately by the bus (one cycle per word/byte transfer), so these
# are the CPU-side base costs only. Refine against SIMH traces per §22.4.
COSTS: dict[int, int] = {
    0o00: 0o3,  # group 0: control, condition codes, branches, single-operand, JSR/RTS
    0o01: 0o3, 0o11: 0o3,   # MOV / MOVB
    0o02: 0o3, 0o12: 0o3,   # CMP / CMPB
    0o03: 0o3, 0o13: 0o3,   # BIT / BITB
    0o04: 0o3, 0o14: 0o3,   # BIC / BICB
    0o05: 0o3, 0o15: 0o3,   # BIS / BISB
    0o06: 0o3,              # ADD (word only on the 11/20)
    0o07: 0o5,              # EIS group 0o07xxxx (capability-gated, §5.4)
    0o10: 0o3,              # byte single-operand group + hi branches + EMT/TRAP
    0o16: 0o3,              # SUB (word only)
    0o17: 0o5,              # FP11 group 0o17xxxx (capability-gated, §5.8)
}


class MemoryInterface(Protocol):
    """The single memory/I/O face the CPU talks to (§4.2). Flat 16-bit mode
    in M1 (SystemBoard); MMU-mapped 22-bit mode is selected here in M5/M6."""

    cycles_used: int  # bus transfers since begin_instruction() — the operand cost

    def begin_instruction(self) -> None: ...
    def read_word(self, addr: int) -> int: ...
    def write_word(self, addr: int, value: int) -> None: ...
    def read_byte(self, addr: int) -> int: ...
    def write_byte(self, addr: int, value: int) -> None: ...


Handler = Callable[["CPU"], None]


class OpEntry(NamedTuple):
    """One registered opcode: masked pattern, handler, capability tag, and
    the operand format used to skip the instruction when the profile lacks
    the capability (§5.4 no-op path consumes operands, it never executes)."""
    mask: int
    pattern: int
    fn: Handler
    caps: str   # CpuCap name: BASE / EIS / CIS / FPU / MMU (§5.4)
    fmt: str    # operand format: none | dst | src | src_dst | branch | jsr | rts
    name: str


_REGISTRY: list[OpEntry] = []


def opcode(number: int, caps: str = "BASE", mask: int = 0o177777,
           fmt: str = "none") -> Callable[[Handler], Handler]:
    """
    Decorator: declare a function as the handler for octal opcode `number`
    (matched as op_word & mask == number). The dispatcher renders the
    registry into jump buckets at __init__ time — per §14 (performance):
    a prebuilt table beats method resolution in the fetch path.
    """
    def register(fn: Handler) -> Handler:
        _REGISTRY.append(
            OpEntry(mask & WORD_MASK, number & mask & WORD_MASK, fn, caps, fmt, fn.__name__))
        return fn
    return register


def resolve_operand(state: CPUState, bus: MemoryInterface, operand_halfword: int,
                    is_source: bool, is_byte: bool) -> int:
    """
    Evaluate an operand's effective address, applying auto-increment/
    decrement, indexing, and indirection relative to Rn or PC (§5.2).
    The PDP-11 has no separate load/store instructions: every instruction
    embeds its addressing scheme in the operand specifier itself.

    Returns the effective byte address for modes 1-7, or -1 for mode 0
    (register direct — the caller reads/writes Rn itself). Side effects
    (register auto-inc/dec) happen here, exactly once per operand specifier;
    is_source is carried for the M5/M6 I/D-space split (§5.7), where
    instruction fetch must not touch the D-space map.
    """
    mode = (operand_halfword & DEC_ADDR_MODE) >> 3   # mode 0-7
    reg = operand_halfword & DEC_REG_NUM              # register 0-7
    _ = is_source  # flat mode: source/data fetches are identical (I/D split is M5)
    if mode == 0:
        return -1                                     # operand is in Rn itself
    if mode == 1:                                     # (Rn) register-deferred
        return state.R[reg]
    if mode == 2:                                     # (Rn)+ autoincrement
        addr = state.R[reg]
        # byte mode increments by 1 — EXCEPT for PC, whose literal is always
        # a full word: mode 2 with R7 is the immediate constant (§5.2)
        inc = 1 if (is_byte and reg != Reg.PC) else 2
        state.R[reg] = (addr + inc) & WORD_MASK
        return addr
    if mode == 3:                                     # @(Rn)+ autoinc-deferred
        addr = state.R[reg]
        state.R[reg] = (addr + 2) & WORD_MASK         # indirection is always a word
        return bus.read_word(addr)                    # PC special-case: absolute address
    if mode == 4:                                     # -(Rn) autodecrement (push on SP)
        addr = (state.R[reg] - 2) & WORD_MASK
        state.R[reg] = addr
        return addr
    if mode == 5:                                     # @-(Rn) autodec-deferred (pop on SP)
        addr = (state.R[reg] - 2) & WORD_MASK
        state.R[reg] = addr
        return bus.read_word(addr)
    # mode 6/7: X(Rn) index — the displacement word is fetched with (PC)+
    pc = state.R[Reg.PC]
    index = bus.read_word(pc)
    state.R[Reg.PC] = (pc + 2) & WORD_MASK
    if index & SIGN_BIT:                              # displacement is a SIGNED word
        index -= 0o200000
    base = (state.R[reg] + index) & WORD_MASK         # PC: relative address (§5.2)
    if mode == 6:
        return base
    return bus.read_word(base)                       # mode 7: @X(Rn) index-deferred


def read_op(cpu: CPU, spec: int, ea: int, is_byte: bool) -> int:
    """Read an operand whose EA was produced by resolve_operand (ea == -1
    means mode 0, register direct; byte ops use the register's low byte)."""
    bus = cpu.bus
    if bus is None:
        raise EmulationError("read_op with no bus attached")
    if ea < 0:
        value = cpu.state.R[spec & DEC_REG_NUM]
        return value & BYTE_MASK if is_byte else value
    return bus.read_byte(ea) if is_byte else bus.read_word(ea)


def write_op(cpu: CPU, spec: int, ea: int, value: int, is_byte: bool) -> None:
    """Write an operand result back through its (already resolved) EA;
    mode 0 writes the register, byte mode only replaces the low byte."""
    bus = cpu.bus
    if bus is None:
        raise EmulationError("write_op with no bus attached")
    if ea < 0:
        reg = spec & DEC_REG_NUM
        if is_byte:
            cpu.state.R[reg] = (cpu.state.R[reg] & 0o177400) | (value & BYTE_MASK)  # keep bits 15-8
        else:
            cpu.state.R[reg] = value & WORD_MASK
    elif is_byte:
        bus.write_byte(ea, value)
    else:
        bus.write_word(ea, value)


class CPU:
    """PDP-11 processor, machine-independent; behavior is parameterized
    entirely by the MachineProfile + functions on memory/io_bus."""

    def __init__(self, profile: MachineProfile, clock: SystemClock) -> None:
        import pdp11.cpu.instructions  # noqa: F401 — importing registers every handler
        self.profile = profile
        self.clock = clock                  # deterministic scheduler (§5.6)
        self.state = CPUState()
        self.bus: MemoryInterface | None = None   # injected by the board (§4.2)
        self.devices: list[BusDevice] = []           # injected: bus-ordered device list
        self.last_op_word: int = 0                   # opcode of the current/last instruction
        # dispatch buckets: opcode nibble -> masked entries, most specific first
        buckets: dict[int, list[OpEntry]] = {}
        for entry in _REGISTRY:
            # entries whose mask frees bit 15 (word/byte op pairs) live in the
            # bits-14-12 bucket so e.g. MOVB (nibble 0o11) finds MOV (nibble 0o01)
            key = entry.pattern >> 12 if entry.mask & 0o100000 else (entry.pattern >> 12) & 0o7
            buckets.setdefault(key, []).append(entry)
        for entries in buckets.values():
            entries.sort(key=lambda e: -bin(e.mask).count("1"))
        self._dispatch: dict[int, list[OpEntry]] = buckets

    def _lookup(self, op_word: int) -> OpEntry | None:
        """Two-level decode: nibble bucket (§5.3), then masked patterns.
        Falls back to the bits-14-12 bucket for word/byte opcode pairs."""
        nib = op_word >> 12
        for entry in self._dispatch.get(nib, ()):
            if op_word & entry.mask == entry.pattern:
                return entry
        alt = nib & 0o7
        if alt != nib:
            for entry in self._dispatch.get(alt, ()):
                if op_word & entry.mask == entry.pattern:
                    return entry
        return None

    # -- hot path (everything below is per-instruction, no allocations) --
    def step(self) -> None:
        """
        Execute exactly one instruction — the unit of granularity for
        single-step debugging and for the §11 acceptance tests.
        """
        st = self.state
        if self.bus is None:
            raise EmulationError("CPU has no bus: the board must inject MemoryInterface")
        bus = self.bus
        bus.begin_instruction()
        if st.halted:
            return                            # parked: ODT owns the machine (§9)
        if interrupts.service(self, self.devices):
            # interrupt taken between instructions (§5.5): vector fetch cost only
            cost = COSTS[0o00]
        elif st.waiting:
            # WAIT: park until an interrupt outranks the PSW IPL (§5.5); the
            # clock still advances one cycle so scheduled events can fire.
            cost = 1
        else:
            instr_addr = st.R[Reg.PC]
            op_word = 0
            trapped = False
            try:
                op_word = self.fetch_word()   # increments PC by 2, per handbook
                self.last_op_word = op_word   # handlers decode byte/group from it
                entry = self._lookup(op_word)
                if entry is None:
                    # Reserved opcode -> trap through 000010. The KD11-B
                    # (11/20) instead NO-OPs the microcoded-only opcodes —
                    # the capability matrix (§5.4) decides, not a model check.
                    traps.reserved_instruction(self)
                    trapped = True
                elif entry.caps != "BASE" and not self.profile.has(CpuCap[entry.caps]):
                    self._missing_cap(op_word, entry)
                    trapped = True
                else:
                    entry.fn(self)
            except BusInit:
                # bus timeout (§5.5): the machine layer catches the hardware
                # signal here and routes it to vector 000004
                traps.bus_error(self, instr_addr)
                trapped = True
            if not trapped and not st.halted and st.psw.get(PSW.T):
                traps.trap(self, traps.VECTOR_TRACE)   # T-bit: trace after instruction
            cost = COSTS.get(op_word >> 12, COSTS[0o00]) + bus.cycles_used
        for event in self.clock.advance(cost):         # drain due device events (§5.6)
            event()

    def _missing_cap(self, op_word: int, entry: OpEntry) -> None:
        """
        Capability-matrix gate (§5.4): the profile lacks entry's tag. The
        KD11-B no-op set (spec §2: MUL, DIV, ASH, ASHC, XOR, SOB, MARK, SXT,
        MTPS/MFPS, SPL) is consumed as a no-op — operands still resolve so PC
        lands past the whole instruction — anything else traps as reserved
        (§5.8: FP opcodes trap on machines without the FPU).
        """
        from pdp11.cpu.instructions import KD11B_NOOP_HANDLERS  # local: avoids import cycle
        if entry.name in KD11B_NOOP_HANDLERS:
            self._skip_operands(op_word, entry.fmt)
        else:
            traps.reserved_instruction(self)

    def _skip_operands(self, op_word: int, fmt: str) -> None:
        """Consume (but do not execute) the operand(s) of a no-op'ed
        instruction: address-mode side effects still happen (e.g. an
        immediate literal is read and PC passes it), matching a skipped
        fetch. Group-0 formats (EIS/XOR/MARK/SXT/SOB/single-operand) embed
        their specifier in the opcode word; JSR-style formats fetch one."""
        st = self.state
        bus = self.bus
        assert bus is not None
        if fmt == "src_dst":                        # XOR 0o074rdd: src in bits 11-6
            resolve_operand(st, bus, (op_word >> 6) & 0o77, True, False)
            resolve_operand(st, bus, op_word & 0o77, False, False)
        elif fmt == "src":                          # MUL/DIV/ASH/ASHC 0o070rss: src = bits 5-0
            resolve_operand(st, bus, op_word & 0o77, True, False)
        elif fmt in ("dst", "rts"):                 # SOB/MARK/SXT/single-operand
            resolve_operand(st, bus, op_word & 0o77, False, False)
        elif fmt == "jsr":                          # JSR 0o004rdd: dest word follows
            resolve_operand(st, bus, self.fetch_word(), False, False)
        # fmt "none" / "branch": no operand words to consume

    def fetch_word(self) -> int:
        """
        Read one instruction word and advance PC. On real hardware this is
        a normal read at (PC)+ auto-increment — a critical §10.4 quirk:
        guard-mode/cached fetch differs from data fetch once the MMU splits
        I/D space (M5); this method is the single place to change then.
        """
        st = self.state
        if self.bus is None:
            raise EmulationError("CPU has no bus: the board must inject MemoryInterface")
        addr = st.R[Reg.PC]
        st.R[Reg.PC] = (addr + 2) & WORD_MASK   # PC increments by 2 (word), 16-bit wrap
        return self.bus.read_word(addr)
