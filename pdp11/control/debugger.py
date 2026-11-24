"""
Extended debugger (spec §9 --debug, §14 instrumentation, §18.3 crash dump) — M7.

The Debugger is an ODT superset (control/front_panel.py): the same `/`
examine, `\\` deposit, `G` go, `Q` quit, under the `dbg>` prompt, plus:

    b [oct]   toggle a breakpoint (PC) / list breakpoints
    w [oct]   toggle a watchpoint (word address) / list watchpoints
    s [n]     single-step n instructions (default 1) with disassembly
    r         register + PSW dump
    m         MMU page dump (6 APR sets + MMR0-3) on has_mmu profiles
    i         interrupt-pending display (per device + arbiter grant vs PSW IPL)
    d         device statistics (name/base/window/DMA/interrupt/drive counters)
    c         container inspection (attached disk images)
    p         performance counters (§14)
    ?         help

All host I/O goes through the TerminalFrontend (§8) — this module never
touches stdin/stdout.

Performance instrumentation (§14) lives here as PURE COUNTERS: §19.6 allows
time.* only in render_backend.py and main.py, so PerfCounters records
instruction and per-opcode-group counts plus charged base cycle costs only;
main.py measures elapsed seconds at the run boundary and passes them into
PerfCounters.report(), which is where instructions-per-second is computed.
Function-level timing dumps are likewise host-measured at the main.py
boundary (M8; flagged §22.4 in docs/acceptance-checklist.md).

The §18.3 EmulationError crash dump (crash_dump) prints registers, PC, the
last 8 dispatched opcodes (CPU.op_trace) and the clock cycle — a pasteable
state for issue reports.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from pdp11.board import SystemBoard
from pdp11.bus.abstract_bus import BusInit
from pdp11.control.front_panel import ODT
from pdp11.control.snapshot import save_snapshot
from pdp11.cpu import interrupts
from pdp11.cpu.core import COSTS, WORD_MASK
from pdp11.cpu.mmu import MD_KER, MD_SUP, MD_USR, SPACE_D, SPACE_I
from pdp11.cpu.registers import PSW, Reg
from pdp11.graphics.terminal import TerminalFrontend

_log = logging.getLogger(__name__)

DBG_PROMPT = "dbg>"

HELP = (
    "debugger commands (octal addresses, §9):\r\n"
    "  <oct>/ examine   <oct>\\ deposit   G [oct] go   Q quit\r\n"
    "  b [oct] breakpoint   w [oct] watchpoint   s [n] step n w/ disassembly\r\n"
    "  r registers   m MMU dump   i interrupts   d devices   c containers\r\n"
    "  p perf counters (§14)   S <file> snapshot (§15)   ? help"
)


class PerfCounters:
    """§14 instrumentation counters — pure counts, NO wall clock (§19.6:
    time.* is allowed only in render_backend.py and main.py). main.py passes
    the host-measured elapsed seconds into report() at the run boundary."""

    def __init__(self) -> None:
        self.instructions = 0
        self.start_cycle = 0
        self.per_group: dict[int, int] = {}      # opcode nibble -> executed count
        self.group_cycles: dict[int, int] = {}   # opcode nibble -> charged base cost

    def begin(self, cycle: int) -> None:
        """Mark the bus-cycle count at debugger entry (run start)."""
        self.start_cycle = cycle

    def record(self, op_word: int, base_cost: int) -> None:
        """Count one dispatched instruction and its table cost (§17.10 step 6)."""
        self.instructions += 1
        g = (op_word >> 12) & 0o17
        self.per_group[g] = self.per_group.get(g, 0) + 1
        self.group_cycles[g] = self.group_cycles.get(g, 0) + base_cost

    def report(self, cycle: int, elapsed_seconds: float | None = None) -> str:
        """Render the counter report; `elapsed_seconds` is host-measured by
        main.py (§19.6) and only used to compute instructions/second."""
        bus_cycles = cycle - self.start_cycle
        if self.instructions:
            head = (f"perf: {self.instructions} instructions in {bus_cycles} bus cycles "
                    f"({bus_cycles / self.instructions:.1f} cycles/instr avg)")
        else:
            head = "perf: no instructions executed"
        if elapsed_seconds is not None and elapsed_seconds > 0:
            head += (f", {self.instructions / elapsed_seconds:.0f} instr/s "
                     "(host-measured at the §19.6 boundary)")
        lines = [head]
        for g in sorted(self.per_group):
            lines.append(f"  group 0o{g:02o}: {self.per_group[g]} instr, "
                         f"{self.group_cycles[g]} base cycles")
        return "\n".join(lines)


# -- disassembler (single-step trace, §9) ---------------------------------------

_REG_NAMES = ("R0", "R1", "R2", "R3", "R4", "R5", "SP", "PC")

_CONTROL_EXACT = {
    0o000000: "HALT", 0o000001: "WAIT", 0o000002: "RTI", 0o000003: "BPT",
    0o000004: "IOT", 0o000005: "RESET", 0o000006: "RTT", 0o000240: "NOP",
}
_CC_OPS = {
    0o000241: "CLC", 0o000242: "CLV", 0o000244: "CLZ", 0o000250: "CLN",
    0o000257: "CCC", 0o000261: "SEC", 0o000262: "SEV", 0o000264: "SEZ",
    0o000267: "CCS", 0o000270: "SEN", 0o000277: "SCC",
}
_LO_BRANCH = {1: "BR", 2: "BNE", 3: "BEQ", 4: "BGE", 5: "BLT", 6: "BGT", 7: "BLE"}
_HI_BRANCH = {0: "BPL", 1: "BMI", 2: "BHI", 3: "BLOS", 4: "BVC", 5: "BVS", 6: "BCC", 7: "BCS"}
_SINGLE_WORD = {
    0o50: "CLR", 0o51: "COM", 0o52: "INC", 0o53: "DEC", 0o54: "NEG", 0o55: "ADC",
    0o56: "SBC", 0o57: "SWAB", 0o60: "ASL", 0o61: "ASR", 0o62: "ROL", 0o63: "ROR",
    0o64: "MARK", 0o65: "MFPI", 0o66: "MTPI", 0o70: "SXT",
}
_SINGLE_BYTE = {
    0o50: "CLRB", 0o51: "COMB", 0o52: "INCB", 0o53: "DECB", 0o54: "NEGB",
    0o55: "ADCB", 0o56: "SBCB", 0o57: "SWAB", 0o60: "ASLB", 0o61: "ASRB",
    0o62: "ROLB", 0o63: "RORB", 0o64: "MTPS", 0o65: "MFPD", 0o66: "MTPD", 0o67: "MFPS",
}
_EIS = {0: "MUL", 1: "DIV", 2: "ASH", 3: "ASHC", 4: "XOR", 5: "FIS", 6: "CIS"}
_DOUBLE = {
    0o1: "MOV", 0o2: "CMP", 0o3: "BIT", 0o4: "BIC", 0o5: "BIS", 0o6: "ADD",
    0o11: "MOV", 0o12: "CMP", 0o13: "BIT", 0o14: "BIC", 0o15: "BIS", 0o16: "SUB",
}


def _fmt_operand(spec: int, pc: int, read: Callable[[int], int]) -> tuple[str, int]:
    """Render one operand specifier whose instruction stream continues at
    `pc`; displacement/literal words are read via `read`. Returns the text
    and the next stream position (PC after the operand's inline words)."""
    mode = (spec >> 3) & 0o7
    reg = spec & 0o7
    name = _REG_NAMES[reg]
    if mode == 0:
        return name, pc
    if mode == 1:
        return f"({name})", pc
    if mode == 2:
        if reg == Reg.PC:                       # immediate: #literal follows
            return f"#{read(pc) & WORD_MASK:06o}", pc + 2
        return f"({name})+", pc
    if mode == 3:
        if reg == Reg.PC:                       # absolute: @#addr follows
            return f"@#{read(pc) & WORD_MASK:06o}", pc + 2
        return f"@({name})+", pc
    if mode == 4:
        return f"-({name})", pc
    if mode == 5:
        return f"@-({name})", pc
    x = read(pc) & WORD_MASK                    # modes 6/7: index word follows
    if x & 0o100000:
        x -= 0o200000                           # signed 16-bit displacement
    if reg == Reg.PC:                           # relative: show the target address
        return f"{(pc + 2 + 2 * x) & WORD_MASK:06o}", pc + 2
    return (f"@({x})({name})" if mode == 7 else f"{x}({name})"), pc + 2


def _branch(name: str, op: int, pc: int) -> str:
    """Branch target: 8-bit signed displacement in words from PC+2 (§5.2)."""
    disp = op & 0o377
    if disp & 0o200:
        disp -= 0o400
    return f"{name} {(pc + 2 + 2 * disp) & WORD_MASK:06o}"


def disassemble(read: Callable[[int], int], pc: int) -> str:
    """Disassemble the instruction word at `pc` (`read` = fetch-word callable).
    Covers the implemented groups — control, branches, JSR/RTS/JMP,
    single/double-operand word+byte, EIS; the FP group shows opcode+AC and CIS
    its opcode (full fields live in cpu/instructions/fp11.py / cis.py)."""
    op = read(pc) & WORD_MASK
    nxt = (pc + 2) & WORD_MASK
    hi = op >> 12
    if op & 0o177400 == 0o104000:
        return f"EMT {op & 0o377:03o}"
    if op & 0o177400 == 0o104400:
        return f"TRAP {op & 0o377:03o}"
    if op in _CONTROL_EXACT:
        return _CONTROL_EXACT[op]
    if op in _CC_OPS:
        return _CC_OPS[op]
    if 0o000400 <= op < 0o004000 and (op >> 8) & 0o17 in _LO_BRANCH:
        return _branch(_LO_BRANCH[(op >> 8) & 0o17], op, pc)
    if 0o100000 <= op < 0o104000 and (op >> 8) & 0o17 in _HI_BRANCH:
        return _branch(_HI_BRANCH[(op >> 8) & 0o17], op, pc)
    if op & 0o177000 == 0o004000:               # JSR R,dd
        dst, _ = _fmt_operand(op & 0o77, nxt, read)
        return f"JSR {_REG_NAMES[(op >> 6) & 0o7]},{dst}"
    if op & 0o177700 == 0o000100:               # JMP dd
        dst, _ = _fmt_operand(op & 0o77, nxt, read)
        return f"JMP {dst}"
    if op & 0o177770 == 0o000200:               # RTS R
        return f"RTS {_REG_NAMES[op & 0o7]}"
    if 0o005000 <= op < 0o010000:               # single-operand word group
        dst, _ = _fmt_operand(op & 0o77, nxt, read)
        return f"{_SINGLE_WORD.get((op >> 6) & 0o77, f'0o{op:06o}')} {dst}"
    if 0o105000 <= op < 0o110000:               # single-operand byte group
        dst, _ = _fmt_operand(op & 0o77, nxt, read)
        return f"{_SINGLE_BYTE.get((op >> 6) & 0o77, f'0o{op:06o}')} {dst}"
    if hi == 0o7:                               # EIS group 0o07xxxx
        key = (op >> 9) & 0o7
        if key == 4:                            # XOR src,dd
            s, p2 = _fmt_operand((op >> 6) & 0o77, nxt, read)
            d, _ = _fmt_operand(op & 0o77, p2, read)
            return f"XOR {s},{d}"
        if key == 6:                            # CIS group
            return f"CIS 0o{op:06o}"
        src, _ = _fmt_operand(op & 0o77, nxt, read)
        return f"{_EIS.get(key, f'0o{op:06o}')} {src}"
    if hi == 0o17:                              # FP group 0o17xxxx
        return f"FP 0o{op:06o} ac={(op >> 6) & 0o7}"
    if hi in _DOUBLE:
        s, p2 = _fmt_operand((op >> 6) & 0o77, nxt, read)
        d, _ = _fmt_operand(op & 0o77, p2, read)
        return f"{_DOUBLE[hi]}{'B' if hi >= 0o10 else ''} {s},{d}"
    return f"0o{op:06o} (reserved)"


# -- crash dump (§18.3) -----------------------------------------------------------

def crash_dump(board: SystemBoard, exc: BaseException) -> str:
    """The §18.3 pasteable EmulationError dump: CPU registers, PC/PSW, the
    last 8 dispatched opcodes (CPU.op_trace), and the clock cycle."""
    st = board.cpu.state
    lines = [
        f"EmulationError: {exc}",
        "registers: " + " ".join(f"R{r}={st.R[r]:06o}" for r in range(8)),
        f"PSW {st.psw.value:06o}  halted={st.halted} waiting={st.waiting}",
        "last 8 opcodes: " + " ".join(f"{op:06o}" for op in board.cpu.op_trace),
        (f"cycle {board.clock.cycle}  cycles_used {board.cycles_used}  "
         f"last_instr_addr {board.cpu.last_instr_addr:06o}"),
    ]
    return "\n".join(lines)


# -- the debugger shell -------------------------------------------------------------

class Debugger(ODT):
    """Extended debugger shell (§9): ODT's examine/deposit/G/Q plus
    breakpoints, watchpoints, single-step disassembly, register/MMU/interrupt/
    device/container dumps and §14 perf counters. supervised_step() runs one
    instruction under breakpoint/watchpoint supervision for main.run's loop."""

    prompt = DBG_PROMPT

    def __init__(self, board: SystemBoard, terminal: TerminalFrontend) -> None:
        super().__init__(board, terminal)
        self.breakpoints: set[int] = set()
        self.watchpoints: set[int] = set()
        self.perf = PerfCounters()
        self.perf.begin(board.clock.cycle)

    # -- run-loop supervision ----------------------------------------------------
    def _peek(self, addr: int) -> int | None:
        """Read a watchpoint address; None when it bus-errors (skipped)."""
        try:
            return self.board.read_word(addr)
        except BusInit:
            return None

    def supervised_step(self) -> bool:
        """Run ONE instruction with breakpoint/watchpoint checks and perf
        counting. Returns False when a stop condition fired (the machine is
        NOT halted — main.run decides); True to keep running. The opcode for
        the perf counters is taken from CPU.last_op_word AFTER the step — the
        debugger adds NO bus reads of its own on the hot path, so supervised
        runs are cycle-identical to plain runs (and reading e.g. a DL11
        register as PC can never fire its side effects here)."""
        cpu = self.board.cpu
        pc = cpu.state.R[Reg.PC]
        if pc in self.breakpoints:
            self._emit(f"breakpoint {pc:06o}\r\n")
            return False
        before = {a: self._peek(a) for a in self.watchpoints}
        cpu.step()
        self.perf.record(cpu.last_op_word,
                         COSTS.get(cpu.last_op_word >> 12, COSTS[0o0]))
        for a, old in before.items():
            now = self._peek(a)
            if old is not None and now != old:
                self._emit(f"watchpoint {a:06o}: {old:06o} -> {now:06o}\r\n")
                return False
        return True

    def report_perf(self, elapsed_seconds: float) -> None:
        """Emit the §14 perf report; elapsed is host-measured by main.py (§19.6)."""
        self._emit(self.perf.report(self.board.clock.cycle, elapsed_seconds) + "\r\n")

    # -- command loop --------------------------------------------------------------
    def _dispatch(self, line: str) -> str | None:
        cmd = line.strip()
        first = cmd.split()[0] if cmd else ""
        if first in ("b", "w"):
            rest = cmd[1:].strip()
            target = self.breakpoints if first == "b" else self.watchpoints
            kind = "breakpoint" if first == "b" else "watchpoint"
            if rest:
                addr = self._oct(rest)
                if addr is None:
                    self._emit("?\r\n")       # octal only (§9) — never crash the shell
                    return None
                if addr in target:
                    target.discard(addr)
                    self._emit(f"{kind} {addr:06o} cleared\r\n")
                else:
                    target.add(addr)
                    self._emit(f"{kind} {addr:06o}\r\n")
            else:
                listing = " ".join(f"{a:06o}" for a in sorted(target)) or "none"
                self._emit(f"{kind}s: {listing}\r\n")
            return None
        if first == "s":
            arg = cmd[1:].strip()
            n = self._oct(arg) if arg else 1
            if n is None:
                self._emit("?\r\n")
                return None
            self._single_step(n)
            return None
        if first == "r":
            self._dump_registers()
            return None
        if first == "m":
            self._dump_mmu()
            return None
        if first == "i":
            self._dump_interrupts()
            return None
        if first == "d":
            self._dump_devices()
            return None
        if first == "c":
            self._dump_containers()
            return None
        if first == "p":
            self._emit(self.perf.report(self.board.clock.cycle) + "\r\n")
            return None
        if first == "S":                        # snapshot at breakpoint/halt (§15)
            path = cmd[1:].strip()
            if not path:
                self._emit("usage: S <file>\r\n")
                return None
            save_snapshot(self.board, path)
            self._emit(f"snapshot saved to {path} (cycle {self.board.clock.cycle})\r\n")
            return None
        if first == "?":
            self._emit(HELP + "\r\n")
            return None
        return super()._dispatch(line)          # ODT: / \ G Q

    def _single_step(self, n: int) -> None:
        """`s [n]`: step n instructions, printing PC/opcode/disassembly each."""
        cpu = self.board.cpu
        for _ in range(n):
            if cpu.state.halted:
                self._emit("halted\r\n")
                return
            pc = cpu.state.R[Reg.PC]
            if pc in self.breakpoints:
                self._emit(f"breakpoint {pc:06o}\r\n")
                return
            try:
                op = self.board.read_word(pc, fetch=True)
            except BusInit:
                op = 0
            try:
                text = disassemble(lambda a: self.board.read_word(a, fetch=True), pc)
            except BusInit:
                text = "???"                    # operand word bus-errored mid-disassembly
            self._emit(f"{pc:06o}: {op:06o}  {text}\r\n")
            before = {a: self._peek(a) for a in self.watchpoints}
            cpu.step()
            self.perf.record(op, COSTS.get(op >> 12, COSTS[0o0]))
            for a, old in before.items():
                now = self._peek(a)
                if old is not None and now != old:
                    self._emit(f"watchpoint {a:06o}: {old:06o} -> {now:06o}\r\n")
                    return

    # -- dumps -----------------------------------------------------------------------
    def _dump_registers(self) -> None:
        st = self.board.cpu.state
        lines = ["registers: " + " ".join(f"R{r}={st.R[r]:06o}" for r in range(8))]
        flags = "".join(nm for bit, nm in ((PSW.N, "N"), (PSW.Z, "Z"), (PSW.V, "V"),
                                           (PSW.C, "C"), (PSW.T, "T")) if st.psw.get(bit))
        lines.append(f"PSW {st.psw.value:06o} (IPL {st.psw.ipl}) flags {flags or '-'} "
                     f"halted={st.halted} waiting={st.waiting}")
        if st.banked:
            lines.append("SP banks K/S/U: " + " ".join(f"{st.sps[m]:06o}" for m in (0, 1, 3)))
        lines.append(f"cycle {self.board.clock.cycle}")
        self._emit("\r\n".join(lines) + "\r\n")

    def _dump_mmu(self) -> None:
        mmu = self.board.mmu
        if mmu is None or not self.board.profile.has_mmu:
            self._emit("no MMU on this profile\r\n")
            return
        lines = [f"MMU enabled={mmu.enabled}  MMR0={mmu.mmr0:06o} MMR1={mmu.mmr1:06o} "
                 f"MMR2={mmu.mmr2:06o} MMR3={mmu.mmr3:06o}"]
        sets = (((SPACE_I, MD_KER), "K I"), ((SPACE_D, MD_KER), "K D"),
                ((SPACE_I, MD_SUP), "S I"), ((SPACE_D, MD_SUP), "S D"),
                ((SPACE_I, MD_USR), "U I"), ((SPACE_D, MD_USR), "U D"))
        for (space, mode), label in sets:
            pairs = " ".join(f"{mmu.par[space][mode][p]:06o}/{mmu.pdr[space][mode][p]:06o}"
                             for p in range(8))
            lines.append(f"  {label} APR0-7 PAR/PDR: {pairs}")
        self._emit("\r\n".join(lines) + "\r\n")

    def _dump_interrupts(self) -> None:
        lines = ["interrupts:"]
        for d in self.board.devices:
            req = d.interrupt_pending()
            if req is not None:
                lines.append(f"  {d.name} @{getattr(d, 'base', 0):06o}: "
                             f"vector {req[0]:06o} BR{req[1]}")
        pend = interrupts.pending(self.board.devices)
        ipl = self.board.cpu.state.psw.ipl
        if pend is not None:
            lines.append(f"  arbiter grant: vector {pend[0]:06o} BR{pend[1]} "
                         f"(PSW IPL {ipl} -> {'taken' if pend[1] > ipl else 'masked'})")
        else:
            lines.append("  none pending")
        self._emit("\r\n".join(lines) + "\r\n")

    def _dump_devices(self) -> None:
        lines = [f"cycle {self.board.clock.cycle}, last-instr bus cycles "
                 f"{self.board.cycles_used}, {len(self.board.devices)} devices:"]
        for d in self.board.devices:
            req = d.interrupt_pending()
            ip = f" vector {req[0]:06o}/BR{req[1]}" if req else ""
            dma = " dma" if d.dma_request() else ""
            extra = []
            drive = getattr(d, "drive", None)
            if drive is not None:
                for attr in ("sectors_read", "sectors_written"):
                    if hasattr(drive, attr):
                        extra.append(f"{attr}={getattr(drive, attr)}")
            tail = (" " + " ".join(extra)) if extra else ""
            lines.append(f"  {d.name} @{getattr(d, 'base', 0):06o} "
                         f"win={d.window_size}{dma}{ip}{tail}")
        self._emit("\r\n".join(lines) + "\r\n")

    def _dump_containers(self) -> None:
        lines = ["containers:"]
        found = False
        for d in self.board.devices:
            img = getattr(d, "image", None)
            if img is None:
                drive = getattr(d, "drive", None)
                img = getattr(drive, "image", None) if drive is not None else None
            if img is not None:
                found = True
                lines.append(f"  {d.name}: {img.path} — {img.sector_count} sectors "
                             f"x {img.sector_size} bytes")
        if not found:
            lines.append("  none attached")
        self._emit("\r\n".join(lines) + "\r\n")
