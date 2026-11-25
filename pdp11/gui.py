"""
Optional tkinter GUI front end — the complete user interface (spec §8: the
optional pixel/tkinter backend surface; §19.6 host-I/O boundary — this module
and main.py/render_backend.py are the only host-I/O surfaces).

Launch with `eleven13 --gui` (no other flags needed). The window opens on a
**setup form** where every option is chosen in the GUI:

- machine (11/03, 11/20, 11/70, 11/94) — pre-selected if --machine was given
- RAM size (octal words), disk/RL/tape/paper-tape images (browse + add,
  repeatable), boot ROM, snapshot save/restore files
- checkboxes: --throttle, --debug, --vt11; power-fail/restore cycle entries

**Start** assembles the machine from the form (via setup_to_argv → the normal
arg parser, so CLI and GUI share one validation path) and swaps to the
session view:

- **console pane** (left): the plain-text DL11 console + ODT/debugger shell.
  Type in the input line at the bottom, Enter sends a line; the BREAK button
  sends Ctrl-C (0o003, the front-panel BREAK key, §13); Quit closes cleanly.
- **instructions pane** (right): the per-machine "what to do now" cheat sheet
  from instructions_for() — power-on/boot, attached media, ODT table,
  debugger table with --debug, active flags.

Threading model: tkinter is not thread-safe, so the emulator runs on a
worker thread and talks to the widgets only through two queue.Queue pipes
(TkGuiBackend). The Tk main loop polls the output queue with after().
tkinter is imported lazily inside run_gui() so importing this module (and
the test suite on headless CI) never requires a display or libtk.

tkinter ships with CPython on macOS/Windows; on Linux install the system
tk package (e.g. python3-tk). No PyPI dependency is added — the core stays
stdlib-only (§18.1). The plain stdio console remains available headless
(without --gui) for scripts/CI.
"""

from __future__ import annotations

import queue
import threading
from argparse import Namespace
from functools import partial
from typing import TYPE_CHECKING

from pdp11.board import SystemBoard
from pdp11.config.profiles import PROFILES, CpuCap, MachineProfile
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
(256256-byte .img) the RX11 boot path loads RT-11 — watch the console for
the RT-11 banner. Press BREAK (or Ctrl-C in the input line) to drop into
ODT; `G 173000` resumes the boot ROM.
""",
    "11/20": """\
Power-on: NO boot ROM — CPU starts at 000000 with RAM clear, exactly like
the real KD11-B. Either toggle in a bootstrap by hand (see ODT deposit
below) or attach a raw RK05 image (exactly 2498048 bytes; a missing file
becomes a blank pack) and Go from its boot block. `G` alone runs the HALT
(000000) already at 000000, which just returns you to `@`.
""",
    "11/70": """\
Power-on: built-in bootstrap at 173000 (non-authentic — supply a real M9312
PROM in the setup form to run genuine 173000-series boot code). The MMU is
OFF until boot code sets MMR0 MME. On halt the console shows `^ address`
first (the 11/70 front-panel display). Attach an RP06/RP07 image for the
RH70 Massbus at 176700.
""",
    "11/94": """\
Power-on: J-11 console boot PROM at 173000, `^ address` halt display, Q22-CD
22-bit map at 0o170400, on-chip CIS+FPJ. Attach an RX01 image or a SIMH
.tap (TQK11 MSCP at 174500).
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
    lines.append("   machine into ODT. Quit = clean exit (saves the snapshot file")
    lines.append("   if one was set in the setup form, §15).")
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
        lines.append("ATTACHED MEDIA (none — set them in the setup form)")
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
    if getattr(args, "memory", None) is not None:
        flags.append(f"--memory {args.memory:o} words")
    lines.append("  " + (", ".join(flags) if flags else "(defaults: full speed)"))
    lines.append("")
    lines.append("MORE HELP")
    lines.append("  docs/user-manual.md    — full CLI reference")
    lines.append("  docs/operator-manual.md — per-machine power-on/boot behavior")
    lines.append("  DESIGN.md              — the binding specification")
    return "\n".join(lines) + "\n"


def setup_to_argv(*, machine: str, memory: str = "", disks: list[str] | None = None,
                  rls: list[str] | None = None, tapes: list[str] | None = None,
                  paper_tapes: list[str] | None = None, bootrom: str = "",
                  throttle: bool = False, debug: bool = False, vt11: bool = False,
                  save: str = "", restore: str = "",
                  powerfail: tuple[bool, str] = (False, ""),
                  powerrestore: tuple[bool, str] = (False, "")) -> list[str]:
    """Map the setup-form fields to the normal CLI argv, so the GUI and the
    stdio console share one parsing/validation path (spec §4.1). Pure
    function — unit tested headless. Octal fields are passed through as
    strings (the parser reads them as octal, §10.1)."""
    argv = ["--machine", machine, "--gui"]
    if memory.strip():
        argv += ["--memory", memory.strip()]
    for d in disks or []:
        argv += ["--disk", d]
    for r in rls or []:
        argv += ["--rl", r]
    for t in tapes or []:
        argv += ["--tape", t]
    for p in paper_tapes or []:
        argv += ["--papertape", p]
    if bootrom.strip():
        argv += ["--bootrom", bootrom.strip()]
    if save.strip():
        argv += ["--save", save.strip()]
    if restore.strip():
        argv += ["--restore", restore.strip()]
    if throttle:
        argv.append("--throttle")
    if debug:
        argv.append("--debug")
    if vt11:
        argv.append("--vt11")
    if powerfail[0]:
        argv.append("--powerfail")
        if powerfail[1].strip():
            argv.append(powerfail[1].strip())
    if powerrestore[0]:
        argv.append("--powerrestore")
        if powerrestore[1].strip():
            argv.append(powerrestore[1].strip())
    return argv


class _GuiCtx:
    """Mutable session context shared between the Tk main thread and the
    emulator worker thread (plain attributes; Tk widgets are only touched on
    the main thread, queues are thread-safe)."""

    def __init__(self) -> None:
        self.backend: TkGuiBackend | None = None
        self.board: SystemBoard | None = None
        self.code = 0
        self.done = True
        self.started = False


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
    """Launch the tkinter front end. Opens the setup form (pre-filled from any
    CLI flags already given), then on Start assembles the machine and runs it
    in the session view. Blocks until the session ends; returns the emulator
    exit code. Imports tkinter lazily; raises SystemExit with an actionable
    message when libtk/display is missing."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise SystemExit(
            "eleven13 --gui needs stdlib tkinter (libtk). On Linux install the "
            "system package (e.g. apt install python3-tk); on macOS/Windows it "
            "ships with Python. Without --gui the plain stdio console works "
            "everywhere."
        ) from exc

    from pdp11.control.debugger import crash_dump
    from pdp11.main import _oct_cycles, assemble, build_arg_parser, run

    root = tk.Tk()
    root.title("eleven13 — setup")
    root.minsize(760, 560)

    ctx = _GuiCtx()

    # ------------------------- setup form ----------------------------------
    form = tk.Frame(root, padx=8, pady=8)
    form.pack(fill=tk.BOTH, expand=True)

    tk.Label(form, text="eleven13 — machine setup", font=("Helvetica", 14, "bold")).grid(
        row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 8))

    machine_var = tk.StringVar(value=args.machine or sorted(PROFILES)[0])
    tk.Label(form, text="Machine:").grid(row=1, column=0, sticky=tk.W)
    tk.OptionMenu(form, machine_var, *sorted(PROFILES)).grid(row=1, column=1, sticky=tk.W)

    memory_var = tk.StringVar(value=f"{args.memory:o}" if args.memory is not None else "")
    tk.Label(form, text="RAM size (octal words, blank = profile max):").grid(
        row=2, column=0, sticky=tk.W)
    tk.Entry(form, textvariable=memory_var, width=14).grid(row=2, column=1, sticky=tk.W)

    def media_row(row: int, label: str, initial: list[str] | None) -> tk.Listbox:
        tk.Label(form, text=label).grid(row=row, column=0, sticky=tk.NW)
        lb = tk.Listbox(form, height=3, width=52)
        lb.grid(row=row, column=1, columnspan=2, sticky=tk.W)
        for item in initial or []:
            lb.insert(tk.END, item)
        return lb

    disk_lb = media_row(3, "Disk images (--disk):", args.disk)
    rl_lb = media_row(4, "RL01/RL02 images (--rl):", args.rl)
    tape_lb = media_row(5, "Magtapes .tap (--tape):", args.tape)
    pt_lb = media_row(6, "Paper tapes .pt (--papertape):", args.papertape)

    def add_to(lb: tk.Listbox, title: str) -> None:
        path = filedialog.askopenfilename(title=title)
        if path:
            lb.insert(tk.END, path)

    def remove_from(lb: tk.Listbox) -> None:
        selection = lb.curselection()  # type: ignore[no-untyped-call]  # typeshed gap
        for i in reversed(selection):
            lb.delete(i)

    for row, lb in ((3, disk_lb), (4, rl_lb), (5, tape_lb), (6, pt_lb)):
        tk.Button(form, text="Browse…", command=partial(add_to, lb, "attach image")).grid(
            row=row, column=3, sticky=tk.W)
        tk.Button(form, text="Remove", command=partial(remove_from, lb)).grid(
            row=row, column=4, sticky=tk.W)

    bootrom_var = tk.StringVar(value=args.bootrom or "")
    tk.Label(form, text="Boot ROM (M9312, --bootrom):").grid(row=7, column=0, sticky=tk.W)
    tk.Entry(form, textvariable=bootrom_var, width=40).grid(row=7, column=1, columnspan=2,
                                                             sticky=tk.W)
    tk.Button(form, text="Browse…",
              command=lambda: bootrom_var.set(
                  filedialog.askopenfilename(title="boot PROM image") or bootrom_var.get())
              ).grid(row=7, column=3, sticky=tk.W)

    save_var = tk.StringVar(value=args.save or "")
    restore_var = tk.StringVar(value=args.restore or "")
    tk.Label(form, text="Snapshot save (--save):").grid(row=8, column=0, sticky=tk.W)
    tk.Entry(form, textvariable=save_var, width=40).grid(row=8, column=1, columnspan=2, sticky=tk.W)
    tk.Button(form, text="Browse…",
              command=lambda: save_var.set(
                  filedialog.asksaveasfilename(title="snapshot file") or save_var.get())
              ).grid(row=8, column=3, sticky=tk.W)
    tk.Label(form, text="Snapshot restore (--restore):").grid(row=9, column=0, sticky=tk.W)
    tk.Entry(form, textvariable=restore_var, width=40).grid(row=9, column=1, columnspan=2,
                                                            sticky=tk.W)
    tk.Button(form, text="Browse…",
              command=lambda: restore_var.set(
                  filedialog.askopenfilename(title="snapshot file") or restore_var.get())
              ).grid(row=9, column=3, sticky=tk.W)

    throttle_var = tk.BooleanVar(value=args.throttle)
    debug_var = tk.BooleanVar(value=args.debug)
    vt11_var = tk.BooleanVar(value=args.vt11)
    for col, (var, label) in enumerate(((throttle_var, "--throttle (real speed)"),
                                        (debug_var, "--debug (debugger shell)"),
                                        (vt11_var, "--vt11 (vector display)"))):
        tk.Checkbutton(form, text=label, variable=var).grid(row=10, column=col, sticky=tk.W)

    pf_on = tk.BooleanVar(value=args.powerfail is not None)
    pf_var = tk.StringVar(value="" if args.powerfail is None else f"{args.powerfail:o}")
    pr_on = tk.BooleanVar(value=args.powerrestore is not None)
    pr_var = tk.StringVar(value="" if args.powerrestore is None else f"{args.powerrestore:o}")
    tk.Checkbutton(form, text="--powerfail at octal cycles (vector 000024):", variable=pf_on).grid(
        row=11, column=0, sticky=tk.W)
    tk.Entry(form, textvariable=pf_var, width=12).grid(row=11, column=1, sticky=tk.W)
    tk.Checkbutton(form, text="--powerrestore at octal cycles:", variable=pr_on).grid(
        row=12, column=0, sticky=tk.W)
    tk.Entry(form, textvariable=pr_var, width=12).grid(row=12, column=1, sticky=tk.W)

    # ------------------------- session view --------------------------------
    session = tk.Frame(root)
    pane = tk.PanedWindow(session, orient=tk.HORIZONTAL)
    pane.pack(fill=tk.BOTH, expand=True)
    left = tk.Frame(pane)
    console = tk.Text(left, wrap=tk.NONE, state=tk.DISABLED, bg="black", fg="green",
                      insertbackground="green", font=("Menlo", 12))
    console.pack(fill=tk.BOTH, expand=True)
    entry = tk.Entry(left, font=("Menlo", 12))
    entry.pack(fill=tk.X, side=tk.BOTTOM)
    pane.add(left, width=620)
    right = tk.Frame(pane)
    tk.Label(right, text="Instructions", font=("Helvetica", 12, "bold")).pack(anchor=tk.W)
    help_text = tk.Text(right, wrap=tk.WORD, state=tk.DISABLED, width=46, font=("Helvetica", 10))
    help_text.pack(fill=tk.BOTH, expand=True)
    pane.add(right)
    status = tk.Label(session, text="starting…", anchor=tk.W)
    status.pack(fill=tk.X, side=tk.BOTTOM)
    buttons = tk.Frame(session)
    buttons.pack(fill=tk.X, side=tk.BOTTOM)

    pending_cr = False

    def pump() -> None:
        nonlocal pending_cr
        be, bd = ctx.backend, ctx.board
        if be is None or bd is None:
            return
        for b in be.drain_output():
            ch = chr(b)
            if ch == "\r":
                pending_cr = True
                continue
            if ch == "\n" and pending_cr:
                pending_cr = False
            elif pending_cr:
                pending_cr = False
                ch = "\n"                    # lone CR: new line in the pane
            console.configure(state=tk.NORMAL)
            console.insert(tk.END, ch)
            console.see(tk.END)
            console.configure(state=tk.DISABLED)
        state = "HALT — ODT `@` prompt (type below, Enter sends)" if bd.cpu.state.halted \
            else "RUN"
        status.configure(text=f"{state}   machine PDP-{bd.profile.key}   cycle {bd.clock.cycle:o}")
        if ctx.done:
            root.destroy()
            return
        root.after(30, pump)

    def worker(board: SystemBoard, terminal: ConsoleTerminal, sargs: Namespace) -> None:
        try:
            ctx.code = run(board, terminal, throttle=sargs.throttle, debug=sargs.debug,
                           powerfail=_oct_cycles(sargs.powerfail),
                           powerrestore=_oct_cycles(sargs.powerrestore),
                           save=sargs.save)
        except EmulationError as exc:            # §18.3 crash dump into the console pane
            for ch in crash_dump(board, exc):
                terminal.write(ord(ch))
            ctx.code = 1
        finally:
            ctx.done = True

    def start() -> None:
        argv = setup_to_argv(
            machine=machine_var.get(), memory=memory_var.get(),
            disks=list(disk_lb.get(0, tk.END)), rls=list(rl_lb.get(0, tk.END)),
            tapes=list(tape_lb.get(0, tk.END)), paper_tapes=list(pt_lb.get(0, tk.END)),
            bootrom=bootrom_var.get(), throttle=throttle_var.get(), debug=debug_var.get(),
            vt11=vt11_var.get(), save=save_var.get(), restore=restore_var.get(),
            powerfail=(pf_on.get(), pf_var.get()), powerrestore=(pr_on.get(), pr_var.get()))
        sargs = build_arg_parser().parse_args(argv)
        sargs.gui = False                      # we are already inside the GUI
        backend = TkGuiBackend()
        terminal = ConsoleTerminal(backend)
        board = assemble(sargs, terminal)
        ctx.backend = backend
        ctx.board = board
        ctx.code = 0
        ctx.done = False
        ctx.started = True
        form.pack_forget()
        session.pack(fill=tk.BOTH, expand=True)
        root.title(f"eleven13 — PDP-{board.profile.key}")
        help_text.configure(state=tk.NORMAL)
        help_text.insert(tk.END, instructions_for(board.profile, sargs))
        help_text.configure(state=tk.DISABLED)

        def send_line(_event: tk.Event[tk.Misc]) -> None:
            line = entry.get()
            entry.delete(0, tk.END)
            backend.push_input(line.encode("ascii", "ignore") + b"\r")

        entry.bind("<Return>", send_line)
        entry.focus_set()
        tk.Button(buttons, text="BREAK (Ctrl-C)",
                  command=lambda: backend.push_input(bytes([BREAK_BYTE]))).pack(side=tk.LEFT)

        def quit() -> None:
            backend.push_input(bytes([BREAK_BYTE]))   # halt a running machine first (§13)
            backend.close()
            root.destroy()

        tk.Button(buttons, text="Quit", command=quit).pack(side=tk.LEFT)
        threading.Thread(target=worker, args=(board, terminal, sargs), daemon=True).start()
        root.after(30, pump)

    def on_close() -> None:
        if ctx.started and ctx.backend is not None:
            ctx.backend.push_input(bytes([BREAK_BYTE]))  # halt a running machine first (§13)
            ctx.backend.close()
            root.after(100, lambda: root.destroy() if ctx.done else on_close())
        else:
            root.destroy()

    tk.Button(form, text="Start", font=("Helvetica", 12, "bold"), command=start).grid(
        row=13, column=0, pady=10, sticky=tk.W)
    tk.Button(form, text="Quit", command=on_close).grid(row=13, column=1, pady=10, sticky=tk.W)
    tk.Label(form, text="All numbers octal (§10.1). Media formats auto-detect by size (§16); "
                         "RL images use the --rl list.").grid(row=14, column=0, columnspan=4,
                                                              sticky=tk.W)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return ctx.code


__all__ = ["BOOT_HELP", "DEBUGGER_HELP", "ODT_HELP", "TkGuiBackend", "gui_available",
           "instructions_for", "run_gui", "setup_to_argv"]
