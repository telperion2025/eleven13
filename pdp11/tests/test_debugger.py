"""
Extended debugger tests (spec §9 --debug, §14, §18.3 — M7): the disassembler,
breakpoints/watchpoints under supervised stepping, the command shell
(single-step trace, register/MMU/interrupt/device/container dumps, perf
counters), the ODT commands inherited from front_panel, the --debug wiring in
main.run, and the §18.3 EmulationError crash dump.
"""

from __future__ import annotations

from pdp11.board import SystemBoard
from pdp11.control.debugger import Debugger, crash_dump, disassemble
from pdp11.cpu.core import CPU
from pdp11.cpu.registers import Reg
from pdp11.errors import EmulationError
from pdp11.graphics.terminal import ConsoleTerminal
from pdp11.main import main, run
from pdp11.tests.cpu_golden import make_machine, step_n
from pdp11.tests.test_console import FakeBackend


def make_debugger(program: list[int], input_bytes: bytes = b"Q\n",
                  profile_key: str = "11/20", pc: int = 0o000100
                  ) -> tuple[Debugger, FakeBackend, SystemBoard]:
    """Fresh machine with `program` at `pc`, a scripted host backend, and a
    Debugger shell over it."""
    cpu, board = make_machine(profile_key=profile_key, program=program, pc=pc)
    backend = FakeBackend(input_bytes)
    return Debugger(board, ConsoleTerminal(backend)), backend, board


# -- disassembler (§9 single-step trace) --------------------------------------------

def test_disassemble_groups():
    program = [
        0o012700, 0o000005,   # 0o100 MOV #5,R0
        0o013001,             # 0o104 MOV @(R0)+,R1
        0o105037, 0o002000,   # 0o106 CLRB @#0o2000
        0o001375,             # 0o112 BNE 0o106 (disp -3 words)
        0o004137, 0o002000,   # 0o114 JSR R1,@#0o2000
        0o000207,             # 0o120 RTS R7
        0o000137, 0o002000,   # 0o122 JMP @#0o2000
        0o070201,             # 0o126 MUL R1
        0o074302,             # 0o130 XOR -(R3),R2
        0o172437, 0o002000,   # 0o132 FP group word
        0o076470,             # 0o136 CIS group
        0o000000,             # 0o140 HALT
    ]
    _cpu, board = make_machine(program=program, pc=0o100)
    read = lambda a: board.read_word(a, fetch=True)  # noqa: E731
    assert disassemble(read, 0o100) == "MOV #000005,R0"
    assert disassemble(read, 0o104) == "MOV @(R0)+,R1"
    assert disassemble(read, 0o106) == "CLRB @#002000"
    assert disassemble(read, 0o112) == "BNE 000106"
    assert disassemble(read, 0o114) == "JSR R1,@#002000"
    assert disassemble(read, 0o120) == "RTS PC"
    assert disassemble(read, 0o122) == "JMP @#002000"
    assert disassemble(read, 0o126) == "MUL R1"
    assert disassemble(read, 0o130) == "XOR -(R3),R2"
    assert disassemble(read, 0o132).startswith("FP 0o172437")
    assert disassemble(read, 0o136).startswith("CIS 0o076470")
    assert disassemble(read, 0o140) == "HALT"


# -- breakpoints / watchpoints under supervised stepping (§9) ------------------------

def test_breakpoint_stops_supervised_step():
    dbg, backend, board = make_debugger([0o012700, 0o000005, 0o005300, 0o000000])
    dbg.breakpoints.add(0o000104)                 # the DEC
    assert dbg.supervised_step() is True          # MOV at 0o100 runs
    assert dbg.supervised_step() is False         # breakpoint at 0o104 fires
    assert "breakpoint 000104" in backend.text()
    assert board.cpu.state.R[Reg.PC] == 0o000104  # not executed yet


def test_watchpoint_fires_on_store():
    dbg, backend, _board = make_debugger([0o012737, 0o000001, 0o002000, 0o000000])
    dbg.watchpoints.add(0o002000)
    assert dbg.supervised_step() is False         # MOV R0,@#0o2000 changed the word
    assert "watchpoint 002000: 000000 -> 000001" in backend.text()


def test_supervised_step_counts_perf():
    dbg, _backend, _board = make_debugger([0o012700, 0o000005, 0o000000])
    assert dbg.supervised_step() is True
    assert dbg.supervised_step() is True
    assert dbg.perf.instructions == 2
    assert 0o01 in dbg.perf.per_group             # MOV group charged


# -- command shell ---------------------------------------------------------------------

def test_single_step_prints_disassembly():
    dbg, backend, _board = make_debugger([0o012700, 0o000005, 0o000000], b"s 2\nQ\n")
    assert dbg.run() == "quit"
    out = backend.text()
    assert "dbg>" in out
    assert "000100: 012700  MOV #000005,R0" in out
    assert "000104: 000000  HALT" in out
    assert dbg.perf.instructions == 2


def test_odt_commands_inherited():
    dbg, backend, board = make_debugger([], b"200/\n200\\\n777\nG 100\n")
    board.ram.write_word(0o000200, 0o123456)
    assert dbg.run() == "go"
    out = backend.text()
    assert "000200/ 123456" in out
    assert board.ram.read_word(0o000200) == 0o777
    assert board.cpu.state.R[Reg.PC] == 0o000100


def test_register_interrupt_device_dumps():
    dbg, backend, _board = make_debugger([], b"r\ni\nd\np\n?\nQ\n")
    dbg.run()
    out = backend.text()
    assert "registers: R0=000000" in out and "PSW" in out
    assert "interrupts:" in out and "none pending" in out
    assert "devices:" in out
    assert "perf: no instructions executed" in out
    assert "debugger commands" in out             # '?' help


def test_mmu_dump_on_1170_and_flat_on_1120():
    dbg70, be70, _ = make_debugger([], b"m\nQ\n", profile_key="11/70")
    dbg70.run()
    assert "MMR0=" in be70.text() and "K I" in be70.text() and "U D" in be70.text()
    dbg20, be20, _ = make_debugger([], b"m\nQ\n", profile_key="11/20")
    dbg20.run()
    assert "no MMU" in be20.text()


def test_container_inspection(tmp_path):
    from pdp11.disk_images.container import open_rx01
    from pdp11.main import assemble, build_arg_parser
    img = str(tmp_path / "rt11.rx01")
    open_rx01(img).flush()
    args = build_arg_parser().parse_args(["--machine", "11/03", "--disk", img])
    backend = FakeBackend(b"c\nd\nQ\n")
    board = assemble(args, ConsoleTerminal(backend))
    dbg = Debugger(board, ConsoleTerminal(backend))
    dbg.run()
    out = backend.text()
    assert "containers:" in out and "rt11.rx01" in out and "2002 sectors" in out
    assert "RX11" in out and "DL11" in out       # device stats list the slots


# -- perf counters + crash dump (§14, §18.3) --------------------------------------------

def test_perf_report_with_elapsed():
    dbg, _backend, _board = make_debugger([0o012700, 0o000005, 0o000000])
    dbg.supervised_step()
    dbg.supervised_step()
    text = dbg.perf.report(dbg.board.clock.cycle, elapsed_seconds=0.5)
    assert "2 instructions" in text and "4 instr/s" in text
    assert "group 0o01" in text


def test_crash_dump_has_state():
    cpu, board = make_machine(program=[0o012700, 0o000001, 0o000000], pc=0o100)
    step_n(cpu, 1)
    dump = crash_dump(board, EmulationError("synthetic invariant break"))
    assert "synthetic invariant break" in dump
    assert "012700" in dump                        # last-8 opcodes ring (§18.3)
    assert "cycle" in dump and "R7=" in dump and "PSW" in dump


# -- main.py wiring (--debug, §18.3 crash path) -------------------------------------------

def test_main_debug_flag_uses_debugger_shell():
    backend = FakeBackend(b"r\nQ\n")
    rc = main(["--machine", "11/20", "--debug"], backend=backend)
    out = backend.text()
    assert rc == 0
    assert "dbg>" in out and "PSW" in out
    assert "@" not in out                          # ODT prompt not used


def test_run_debug_breakpoint_during_run():
    """--debug: a breakpoint set in the HALT shell stops the resumed RUN (§9)."""
    cpu, board = make_machine(program=[0o012700, 0o000005, 0o005300, 0o001376, 0o000000],
                              pc=0o100)
    backend = FakeBackend(b"b 104\nG 100\nQ\n")
    rc = run(board, ConsoleTerminal(backend), debug=True)
    assert rc == 0
    assert "breakpoint 000104" in backend.text()
    assert cpu.state.halted


def test_main_emulation_error_crashes_with_dump(monkeypatch):
    """§18.3: an EmulationError escaping the machine prints the pasteable dump
    (registers, PC, last 8 opcodes, cycle) and exits 1 — never swallowed."""
    def boom(self: CPU) -> None:
        raise EmulationError("synthetic invariant break")

    monkeypatch.setattr(CPU, "step", boom)
    backend = FakeBackend(b"")
    rc = main(["--machine", "11/20"], backend=backend)
    out = backend.text()
    assert rc == 1
    assert "EmulationError: synthetic invariant break" in out
    assert "last 8 opcodes" in out and "cycle" in out


def test_assembled_board_debugger_devices(tmp_path):
    """The debugger shell over a fully assembled machine (devices + counters)."""
    from pdp11.disk_images.container import open_rk05
    from pdp11.main import assemble, build_arg_parser
    img = str(tmp_path / "d.rk05")
    open_rk05(img).flush()
    backend = FakeBackend(b"d\nc\nQ\n")
    args = build_arg_parser().parse_args(["--machine", "11/20", "--disk", img])
    board = assemble(args, ConsoleTerminal(backend))
    dbg = Debugger(board, ConsoleTerminal(backend))
    assert dbg.run() == "quit"
    out = backend.text()
    assert "RK11" in out and "containers:" in out and "d.rk05" in out
    assert "KW11L" in out
