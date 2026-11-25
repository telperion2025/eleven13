"""
Optional tkinter GUI front end (spec §8: the optional pixel/tkinter backend
surface; §19.6 host-I/O boundary — this module and main.py/render_backend.py
are the only host-I/O surfaces).

Launch with `eleven13 --machine 11/03 --gui`. The window has two panes:

- **console** (left): the plain-text DL11 console + ODT/debugger shell. Type
  in the input line at the bottom and press Enter to send a line; the BREAK
  button sends Ctrl-C (0o003, the front-panel BREAK key, §13); Quit closes
  the session cleanly (BREAK + EOF, so a running machine halts into ODT and
  the shell exits instead of dying mid-cycle).
- **instructions** (right): a per-machine "what to do now" cheat sheet built
  by instructions_for() — power-on behavior, how to boot, the ODT command
  table, debugger commands with --debug, and the media you attached.

Threading model: tkinter is not thread-safe, so the emulator runs on a
worker thread and talks to the widgets only through two queue.Queue pipes
(TkGuiBackend). The Tk main loop polls the output queue with after().
tkinter is imported lazily inside run_gui() so importing this module (and
the test suite on headless CI) never requires a display or libtk.

tkinter ships with CPython on macOS/Windows; on Linux install the system
tk package (e.g. python3-tk). No PyPI dependency is added — the core stays
stdlib-only (§18.1).
"""

from __future__ import annotations

import queue
import threading
from argparse import Namespace
from typing import TYPE_CHECKING

from pdp11.config.profiles import CpuCap, MachineProfile
from pdp11.errors import EmulationError
from pdp11.graphics.render_backend import RenderBackend
from pdp11.graphics.terminal import ConsoleTerminal

if TYPE_CHECKING:  # pragma: no cover — typing only
    import tkinter as tk

BREAK_BYTE = 0o003  # Ctrl-C / BREAK key (§13)


def gui_available() -> bool:
    """True when stdlib tkinter can be imported (needs libtk + a display at
    run time; import alone works headless)."""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


ODT_HELP = """\
ODT console (the `@` prompt) — all numbers OCTAL:
  <addr>/      examine word at <addr> (e.g. 1000/)
  <addr>\\      deposit: type the address, press Enter, then type the value
  G [addr]     run from <addr> (default: current address)
  Q            quit the emulator
  (Enter)      re-examine the current address
  bad input    -> "?" (ODT never crashes on a typo)
"""

DEBUGGER_HELP = """\
Extended debugger (`dbg>` prompt, --debug) — an ODT superset:
  b [addr]  toggle/list PC breakpoints      w [addr]  word watchpoints
  s [n]     single-step n with disassembly  r         register + PSW dump
  m         MMU page dump (11/70, 11/94)    i         interrupt-pending display
  d         device statistics               c         attached disk containers
  p         performance counters            S <file>  snapshot now (§15)
  ?         help
"""

BOOT_HELP = {
    "11/03": """\
Power-on: CPU starts at 173000 (boot ROM). With an RX01 image attached
(--disk <256256-byte .img>) the RX11 boot path loads RT-11 — watch the
console for the RT-11 banner. Press BREAK (or Ctrl-C in the input line)
to drop into ODT; `G 173000` resumes the boot ROM.
""",
    "11/20": """\
Power-on: NO boot ROM — CPU starts at 000000 with RAM clear, exactly like
the real KD11-B. Either toggle in a bootstrap by hand (see ODT deposit
above) or attach a raw RK05 image (--disk, exactly 2498048 bytes; a missing
file becomes a blank pack) and Go from its boot block. `G` alone runs the
HALT (000000) already at 000000, which just returns you to `@`.
""",
    "11/70": """\
Power-on: built-in bootstrap at 173000 (non-authentic — supply a real M9312
PROM with --bootrom to run genuine 173000-series boot code). The MMU is OFF
until boot code sets MMR0 MME. On halt the console shows `^ address` first
(the 11/70 front-panel display). Attach an RP06/RP07 image with --disk for
the RH70 Massbus at 176700.
""",
    "11/94": """\
Power-on: J-11 console boot PROM at 173000, `^ address` halt display, Q22-CD
22-bit map at 0o170400, on-chip CIS+FPJ. Attach an RX01 image with --disk or
a SIMH .tap with --tape (TQK11 MSCP at 174500).
""",
}


def instructions_for(profile: MachineProfile, args: Namespace) -> str:
    """The right-hand 'what to do now' panel: machine facts, attached media,
    boot instructions, and the ODT(/debugger) cheat sheet. Pure text — unit
    tested headless (no tkinter needed)."""
    lines: list[str] = []
    lines.append(f"eleven13 — PDP-{profile.key} ({profile.cpu_name})")
    lines.append(f"bus: {profile.bus.name}, physical: {profile.physical_bits} bits, "
                 f"MMU: {'yes' if profile.has_mmu else 'no'}, "
                 f"FPU: {'yes' if profile.has_fpu else 'no'}, "
                 f"FIS: {'yes' if profile.has_fis else 'no'}, CIS: "
                 f"{'yes' if profile.has(CpuCap.CIS) else 'no'}")
    lines.append("")
    lines.append("QUICK START")
    lines.append("1. Watch the console pane (left). The machine starts running")
    lines.append("   immediately at its power-on PC — see below.")
    lines.append("2. When you see the `@` prompt (ODT), type a command in the")
    lines.append("   input line at the bottom and press Enter.")
    lines.append("3. BREAK button = front-panel BREAK (Ctrl-C): halts a running")
    lines.append("   machine into ODT. Quit = clean exit (saves --save snapshot).")
    lines.append("")
    media: list[str] = []
    for flag in ("disk", "rl", "tape", "papertape"):
        for p in (getattr(args, flag, None) or []):
            media.append(f"  --{flag} {p}")
    if getattr(args, "bootrom", None):
        media.append(f"  --bootrom {args.bootrom}")
    if media:
        lines.append("ATTACHED MEDIA")
        lines.extend(media)
    else:
        lines.append("ATTACHED MEDIA (none — add --disk/--rl/--tape/--papertape)")
        lines.append("  (none)")
    lines.append("")
    lines.append("BOOT / POWER-ON")
    lines.append(BOOT_HELP[profile.key].rstrip())
    lines.append("")
    lines.append("CONSOLE COMMANDS")
    lines.append(ODT_HELP.rstrip())
    if getattr(args, "debug", False):
        lines.append("")
        lines.append(DEBUGGER_HELP.rstrip())
    lines.append("")
    lines.append("SESSION FLAGS IN EFFECT")
    flags = []
    if getattr(args, "throttle", False):
        flags.append("--throttle (real-machine speed)")
    if getattr(args, "save", None):
        flags.append(f"--save {args.save} (snapshot on exit, §15)")
    if getattr(args, "restore", None):
        flags.append(f"--restore {args.restore}")
    if getattr(args, "powerfail", None) is not None:
        flags.append(f"--powerfail {args.powerfail} (vector 000024, §5.5)")
    if getattr(args, "powerrestore", None) is not None:
        flags.append(f"--powerrestore {args.powerrestore}")
    if getattr(args, "vt11", False):
        flags.append("--vt11 (vector display at 176600)")
    lines.append("  " + (", ".join(flags) if flags else "(default full speed)"))
    lines.append("")
    lines.append("MORE HELP")
    lines.append("  docs/user-manual.md    — full CLI reference")
    lines.append("  docs/operator-manual.md — per-machine power-on/boot behavior")
    lines.append("  DESIGN.md              — the binding specification")
    return "\n".join(lines) + "\n"


class TkGuiBackend(RenderBackend):
    """RenderBackend over two queue.Queue pipes — safe to call from the
    emulator worker thread; the Tk main thread only touches the queues (via
    push_input/drain_output/close) and the widgets. Constructing it needs no
    tkinter, so the queue logic is unit-testable headless."""

    def __init__(self) -> None:
        self._in: queue.Queue[int] = queue.Queue()
        self._out: queue.Queue[int] = queue.Queue()
        self._closed = False
        self._lock = threading.Lock()

    # --- called from the Tk main thread -----------------------------------
    def push_input(self, data: bytes) -> None:
        """Feed host key bytes (input line, BREAK button) to the emulator."""
        for b in data:
            if b <= 0o177:
                self._in.put(b)

    def drain_output(self) -> list[int]:
        """All guest output bytes pending display (called from after() poll)."""
        out: list[int] = []
        while True:
            try:
                out.append(self._out.get_nowait())
            except queue.Empty:
                return out

    def close(self) -> None:
        """Window closed / Quit: host input is exhausted once the queue drains."""
        with self._lock:
            self._closed = True

    # --- RenderBackend interface, called from the emulator worker thread ---
    def getch(self) -> int | None:
        try:
            return self._in.get_nowait()
        except queue.Empty:
            return None

    def putch(self, byte: int) -> None:
        self._out.put(byte & 0o177)

    def flush(self) -> None:
        pass  # the Tk side polls drain_output() via after()

    def at_eof(self) -> bool:
        with self._lock:
            return self._closed and self._in.empty()


def run_gui(args: Namespace) -> int:
    """Launch the tkinter front end for the already-parsed args and block until
    the session ends. Returns the emulator exit code. Imports tkinter lazily;
    raises SystemExit with an actionable message when libtk/display is missing."""
    try:
        import tkinter as tk
    except ImportError as exc:
        raise SystemExit(
            "eleven13 --gui needs stdlib tkinter (libtk). On Linux install the "
            "system package (e.g. apt install python3-tk); on macOS/Windows it "
            "ships with Python. Without --gui the plain stdio console works "
            "everywhere."
        ) from exc

    from pdp11.control.debugger import crash_dump
    from pdp11.main import _oct_cycles, assemble, run

    backend = TkGuiBackend()
    terminal = ConsoleTerminal(backend)          # plain text in the GUI pane (§8)
    board = assemble(args, terminal)
    profile = board.profile

    root = tk.Tk()
    root.title(f"eleven13 — PDP-{profile.key}")
    root.minsize(900, 480)

    pane = tk.PanedWindow(root, orient=tk.HORIZONTAL)
    pane.pack(fill=tk.BOTH, expand=True)

    left = tk.Frame(pane)
    console = tk.Text(left, wrap=tk.NONE, state=tk.DISABLED, bg="black", fg="green",
                      insertbackground="green", font=("Menlo", 12))
    console.pack(fill=tk.BOTH, expand=True)
    entry = tk.Entry(left, font=("Menlo", 12))
    entry.pack(fill=tk.X, side=tk.BOTTOM)
    entry.focus_set()
    pane.add(left, width=620)

    right = tk.Frame(pane)
    tk.Label(right, text="Instructions", font=("Helvetica", 12, "bold")).pack(anchor=tk.W)
    help_text = tk.Text(right, wrap=tk.WORD, state=tk.DISABLED, width=46,
                        font=("Helvetica", 10))
    help_text.pack(fill=tk.BOTH, expand=True)
    help_text.configure(state=tk.NORMAL)
    help_text.insert(tk.END, instructions_for(profile, args))
    help_text.configure(state=tk.DISABLED)
    pane.add(right)

    status = tk.Label(root, text="starting…", anchor=tk.W)
    status.pack(fill=tk.X, side=tk.BOTTOM)
    buttons = tk.Frame(root)
    buttons.pack(fill=tk.X, side=tk.BOTTOM)
    tk.Button(buttons, text="BREAK (Ctrl-C)",
              command=lambda: backend.push_input(bytes([BREAK_BYTE]))).pack(side=tk.LEFT)

    def quit() -> None:
        backend.push_input(bytes([BREAK_BYTE]))      # halt a running machine first (§13)
        backend.close()
        root.destroy()

    tk.Button(buttons, text="Quit", command=quit).pack(side=tk.LEFT)

    def send_line(_event: tk.Event[tk.Misc]) -> None:
        line = entry.get()
        entry.delete(0, tk.END)
        backend.push_input(line.encode("ascii", "ignore") + b"\r")

    entry.bind("<Return>", send_line)

    result = {"code": 0, "done": False}

    def worker() -> None:
        try:
            result["code"] = run(board, terminal, throttle=args.throttle, debug=args.debug,
                                 powerfail=_oct_cycles(args.powerfail),
                                 powerrestore=_oct_cycles(args.powerrestore),
                                 save=args.save)
        except EmulationError as exc:            # §18.3 crash dump into the console pane
            for ch in crash_dump(board, exc):
                terminal.write(ord(ch))
            result["code"] = 1
        finally:
            result["done"] = True

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    pending_cr = False

    def pump() -> None:
        nonlocal pending_cr
        for b in backend.drain_output():
            ch = chr(b)
            if ch == "\r":
                pending_cr = True
                continue
            if ch == "\n" and pending_cr:
                pending_cr = False
                ch = "\n"
            elif pending_cr:
                pending_cr = False
                ch = "\n"                        # lone CR: start a new line in the pane
            console.configure(state=tk.NORMAL)
            console.insert(tk.END, ch)
            console.see(tk.END)
            console.configure(state=tk.DISABLED)
        state = "HALT — ODT `@` prompt (type below, Enter sends)" if board.cpu.state.halted \
            else "RUN"
        status.configure(text=f"{state}   machine PDP-{profile.key}   "
                              f"cycle {board.clock.cycle:o}")
        if result["done"]:
            root.destroy()
            return
        root.after(30, pump)

    def on_close() -> None:
        backend.push_input(bytes([BREAK_BYTE]))  # halt a running machine first (§13)
        backend.close()
        root.after(100, lambda: root.destroy() if result["done"] else root.after(100, on_close))

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(30, pump)
    root.mainloop()
    thread.join(timeout=5)
    return result["code"]


__all__ = ["BOOT_HELP", "DEBUGGER_HELP", "ODT_HELP", "TkGuiBackend", "gui_available",
           "instructions_for", "run_gui"]
