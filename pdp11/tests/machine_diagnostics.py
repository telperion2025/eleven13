"""
DEC MAINDEC diagnostic runs (spec §3 layout, §11.1) — M7 harness + report.

§11.1 wants each profile to run DEC MAINDEC CPU diagnostics to a pass/fail
report. The real MAINDEC binaries (CKBO/CK41/CPU11/… from SIMH kits /
bit.savers) are operator-supplied artifacts (§22.1 — no binary is fabricated
here); when an image arrives it plugs into the same harness: load at its
load address, run to HALT within MAX_STEPS, and compare the pass/fail flag
word. Until then the harness runs the BUILT-IN per-profile smoke diagnostic
below — a hand-assembled BASE-only program (MOV/ADD/CMP/DEC/branch/
absolute-store/HALT, so it runs on every profile including the bare 11/20)
that sums 1..12 and stores 1 (pass) or 2 (fail) at FLAG_ADDR — and
format_report() documents the pass rate per profile.

Current documented pass rate: 4/4 profiles PASS on the built-in diagnostic
(pinned by the tests at the bottom of this module). Real-MAINDEC pass rates
are recorded in docs/acceptance-checklist.md once the operator supplies the
binaries (§22.1).

Hand-assembled program (octal, loaded at LOAD_ADDR):
    000400  012701 000014   MOV #0o14,R1      ; counter = 12
    000404  012702 000000   MOV #0,R2         ; sum = 0
    000410  060102           loop: ADD R1,R2
    000412  005301           DEC R1
    000414  001375           BNE loop          ; disp -3 words
    000416  022702 000116   CMP #0o116,R2     ; 12+11+..+1 = 78 = 0o116
    000422  001005           BNE fail         ; disp +5 words -> 0o436
    000424  012700 000001   MOV #1,R0
    000430  010037 002000   MOV R0,@#0o2000   ; PASS flag
    000434  000000           HALT
    000436  012700 000002   fail: MOV #2,R0
    000442  010037 002000   MOV R0,@#0o2000   ; FAIL flag
    000446  000000           HALT
"""

from __future__ import annotations

from dataclasses import dataclass

from pdp11.board import SystemBoard
from pdp11.config.profiles import PROFILES
from pdp11.cpu.registers import Reg
from pdp11.system_clock import SystemClock

LOAD_ADDR = 0o000400
FLAG_ADDR = 0o002000
MAX_STEPS = 100000          # decimal instruction budget (off-metal, §10.3)

DIAGNOSTIC_PROGRAM = [
    0o012701, 0o000014,   # MOV #0o14,R1
    0o012702, 0o000000,   # MOV #0,R2
    0o060102,             # loop: ADD R1,R2
    0o005301,             # DEC R1
    0o001375,             # BNE loop
    0o022702, 0o000116,   # CMP #0o116,R2
    0o001005,             # BNE fail
    0o012700, 0o000001,   # MOV #1,R0
    0o010037, FLAG_ADDR,  # MOV R0,@#FLAG  (pass)
    0o000000,             # HALT
    0o012700, 0o000002,   # fail: MOV #2,R0
    0o010037, FLAG_ADDR,  # MOV R0,@#FLAG  (fail)
    0o000000,             # HALT
]


@dataclass
class DiagnosticResult:
    """One diagnostic run's pass/fail report line (§11.1)."""
    profile: str
    name: str
    passed: bool
    instructions: int
    cycles: int
    detail: str


def run_diagnostic(profile_key: str, program: list[int] | None = None,
                   max_steps: int = MAX_STEPS, ram_words: int = 0o010000
                   ) -> DiagnosticResult:
    """Run one diagnostic on a fresh board of `profile_key` until HALT (pass)
    or max_steps (fail); the word at FLAG_ADDR must read 1 for a pass.
    A real MAINDEC image, once operator-supplied (§22.1), is loaded the same
    way — pass criterion becomes the diagnostic's own error halt/flag.
    `ram_words` is clipped small: the smoke diagnostic only touches low memory,
    and 22-bit profiles would otherwise allocate their full 4 MB ceiling."""
    profile = PROFILES[profile_key]
    board = SystemBoard(profile, SystemClock(), ram_words=ram_words)
    prog = DIAGNOSTIC_PROGRAM if program is None else program
    for i, w in enumerate(prog):
        board.ram.write_word(LOAD_ADDR + 2 * i, w)
    cpu = board.cpu
    cpu.state.R[Reg.PC] = LOAD_ADDR
    cpu.state.R[Reg.SP] = 0o017776
    steps = 0
    while not cpu.state.halted and steps < max_steps:
        cpu.step()
        steps += 1
    flag = board.ram.read_word(FLAG_ADDR) if len(board.ram.data) * 2 > FLAG_ADDR else 0
    passed = cpu.state.halted and flag == 1
    detail = f"halted={cpu.state.halted} flag={flag:06o} after {steps} instructions"
    return DiagnosticResult(profile_key, "builtin-smoke", passed, steps,
                            board.clock.cycle, detail)


def run_all() -> list[DiagnosticResult]:
    """Run the built-in diagnostic on every profile (sorted by key)."""
    return [run_diagnostic(key) for key in sorted(PROFILES)]


def format_report(results: list[DiagnosticResult]) -> str:
    """The §11.1 pass/fail report text, including the documented pass rate."""
    lines = ["MAINDEC-style diagnostic report (§11.1 harness; real MAINDEC binaries "
             "await the operator, §22.1):"]
    for r in results:
        lines.append(f"  {r.profile:8s} {r.name:16s} {'PASS' if r.passed else 'FAIL'}  "
                     f"{r.instructions} instr, {r.cycles} cycles — {r.detail}")
    npass = sum(1 for r in results if r.passed)
    lines.append(f"pass rate: {npass}/{len(results)} profiles")
    return "\n".join(lines)


# -- tests (this module is collected per pyproject python_files, §3) ---------------

def test_builtin_diagnostic_passes_on_all_profiles():
    """§11.1 harness: every profile runs the built-in diagnostic to a PASS."""
    results = run_all()
    assert len(results) == 4
    assert all(r.passed for r in results), format_report(results)
    assert all(r.instructions > 0 and r.cycles > 0 for r in results)


def test_report_documents_pass_rate():
    report = format_report(run_all())
    assert "pass rate: 4/4" in report
    assert report.count("PASS") == 4


def test_sabotaged_diagnostic_fails():
    """A wrong expected sum takes the BNE fail path -> flag 2 -> FAIL report."""
    bad = list(DIAGNOSTIC_PROGRAM)
    bad[8] = 0o000117                          # CMP #0o117 instead of 0o116
    r = run_diagnostic("11/20", program=bad)
    assert not r.passed
    assert "flag=000002" in r.detail


def test_diagnostic_halt_required():
    """A program that never HALTs (infinite branch) fails the run budget."""
    looping = [0o000777]                       # BR .-2 (disp -1 word: branch to self)
    r = run_diagnostic("11/03", program=looping, max_steps=1000)
    assert not r.passed
    assert r.instructions == 1000
