# eleven13 User Manual

How to run the emulator: command line, machine selection, console, debugger,
snapshots and fault injection. The binding specification is
[DESIGN.md](../DESIGN.md); this manual documents the user-facing surface.

## 1. Installation

```sh
uv sync --extra dev     # Python >=3.11,<3.13 pinned via .python-version + uv.lock
```

The emulator core has **no runtime dependencies** (stdlib only, spec §18.1).
Optional extras: `dev` (pytest, ruff, mypy — needed for the quality gates) and
`pixel` (pygame, for the optional pixel render backend, spec §8).

## 2. Command line

```
eleven13 --machine {11/03,11/20,11/70,11/94} [options]
python -m pdp11.main --machine 11/20 ...     # identical, module form
eleven13 --gui                               # GUI only: everything chosen in the window (§7)
```

| Option | Meaning | Milestone |
|---|---|---|
| `--machine {11/03,11/20,11/70,11/94}` | Selects the emulated model. Deliberately no default (spec §4.1); **required unless `--gui` is given** — in the GUI the machine is picked in the setup form. | M1 |
| `--memory OCT_WORDS` | RAM size in **octal** words, overriding the profile ceiling. | M1 (parsed) |
| `--disk IMAGE` | Attach a disk image; format auto-detected from file size per spec §16 (repeatable). Dispatched by container: RK05-sized → RK11 at 177400, RX01-sized → RX11 at 177170, RP06/RP07-sized (512-byte sectors) → RH70 at 176700 on the 11/70 (Massbus). | M3/M4/M5 |
| `--rl IMAGE` | Attach an RL01/RL02 image to the RL11 at 174400 (repeatable). Raw 256-byte sectors; any 256-aligned file up to RL02 capacity autosizes and zero-pads (SIMH AUTOSIZE, §16). RL images take this flag, not `--disk`, so partial RK05 files stay unambiguous. | post-M8 |
| `--tape TAP` | Attach a SIMH `.tap` magtape (§16) to the TM11 at 172520 (Unibus) or the TQK11 MSCP controller at 174500 (Q-Bus, 11/94 default). Repeatable; first tape goes to drive 0. | post-M8 |
| `--papertape PT` | Attach a PC11 paper tape (SIMH `.pt` = raw byte stream, read-only in v1, §16) to the PC11 at 177550. | post-M8 |
| `--console {vt100,vt52,la36,odt}` | Terminal frontend (default `vt100`): a real VT100/VT52 escape-sequence state machine, or the LA36 hardcopy; `odt` is the plain text console. | M7 |
| `--gui` | Launch the optional tkinter GUI **with no other flags**: a setup form (machine, RAM, disk/tape/paper-tape/boot-ROM files, snapshot save/restore, throttle/debug/vt11, power-fail cycles) pre-filled from any CLI flags, then the console + instructions panes. Needs stdlib tkinter — see §7. | post-M8 |
| `--vt11` | Attach a VT11-class vector display at 176600 (DPC/MPR/XPR/YPR + display-list engine) rendering into a pixel framebuffer for the optional pixel backend (§8). | post-M8 |
| `--bootrom FILE` | Load an M9312 512-word boot PROM image (spec §13). Without it, a simplified built-in bootstrap is used — documented as **non-authentic**. | M2/M3 |
| `--config FILE.json` | Per-run overrides (memory size, devices, volumes) layered over the profile; see `pdp11/config/config.json` for the schema example. | M2+ |
| `--throttle` | Slow execution to real-machine speed. Default is full speed (`--maxspeed` stance, spec §14). Throttling lives only in `main.py`, never in the machine core. | M2+ |
| `--maxspeed` | Explicit no-throttle (default behavior). | M2+ |
| `--debug` | Extended debugger: supervises RUN (breakpoints/watchpoints) and owns the HALT shell (spec §9). | M7 |
| `--save FILE` / `--restore FILE` | Session snapshot save/restore (spec §15) — saved on exit, restored after assembly. | M8 |
| `--powerfail [OCT_CYCLES]` / `--powerrestore [OCT_CYCLES]` | Inject the power-fail / power-restore trap through vector 000024 at the given bus cycle (default 0o100000 / 0o200000), so OS shutdown/restart code paths are testable (spec §5.5, §11.9). | M8 |

All numeric CLI arguments that denote machine quantities are **octal**
(spec §10.1), e.g. `--memory 0o20000`.

### Current M5/M6 behavior

All four profiles assemble and run. On the **11/70** (`--machine 11/70`)
the machine comes up with the KD11-Z: 22-bit physical memory, the MMU
(off until MMR0 MME is set — boot code maps the kernel APRs first), the
Unibus map, the RH70 Massbus controller at 176700 with an RP06/RP07 drive
when `--disk rp06.dsk` is given, and the FP11. On the **11/94**
(`--machine 11/94`) the J-11 (DCJ-11) brings on-chip CIS string instructions
and the FPJ (FP11-compatible), an MMU with I/D split via MMR0 bit 3, and the
Q22-CD 22-bit Q-Bus map at 0o170400. HALT drops to ODT, which on MMU
machines prints the `^ address` display first (spec §13). Examples:

```sh
eleven13 --machine 11/70 --disk rp06.dsk
eleven13 --machine 11/94 --disk rt11.rx01
```

### M7: terminal frontends and the debugger

`--console` selects the virtual terminal the DL11 console talks to (spec §8):
`vt100` (default — a real VT100 state machine: CSI cursor/erase sequences,
24-line scroll, ANSI/VT52 mode switching), `vt52` (the reduced VT52 variant),
`la36` (a DECwriter hardcopy: output is buffered line-by-line and printed on
LF/FF, not shown on a screen), or `odt` (plain text console). All are strictly
7-bit ASCII devices; host keys with no 7-bit equivalent are dropped with a
debug-log note (§8). `--debug` turns the HALT shell into the extended
debugger and supervises RUN with breakpoints/watchpoints (see §4).

## 3. Running a machine and the console

The flow is:

1. `eleven13 --machine 11/03 --disk rk0.img` assembles the machine from the
   profile and starts at the profile's power-on PC (spec §13).
2. The DL11 console connects to the selected terminal frontend
   (`--console vt100` default). The VT100 frontend is a real escape-sequence
   state machine, not raw pass-through (spec §7/§8); it is a strictly 7-bit
   ASCII device — host keys with no 7-bit equivalent are dropped with a
   debug-log note, never passed through as multi-byte garbage (spec §8).
3. On HALT (or BREAK), control falls to **ODT** — the `@` prompt: `/`
   examine, `\` deposit, `G` go, octal addresses only (spec §9) — or to the
   extended debugger with `--debug`.

## 4. Debugger (`--debug`, M7)

The debugger is an ODT superset under the `dbg>` prompt (all addresses octal):

| Command | Action |
|---|---|
| `<oct>/`, `<oct>\`, `G [oct]`, `Q` | ODT examine / deposit / go / quit (§9) |
| `b [oct]` | toggle a PC breakpoint / list breakpoints |
| `w [oct]` | toggle a word-address watchpoint / list watchpoints |
| `s [n]` | single-step n instructions with disassembly |
| `r` | register + PSW dump |
| `m` | MMU page dump (6 APR sets + MMR0-3) on 11/70, 11/94 |
| `i` | interrupt-pending display (per device + arbiter grant vs PSW IPL) |
| `d` | device statistics (name/base/window/DMA/interrupt/drive counters) |
| `c` | container inspection (attached disk images) |
| `p` | performance counters (§14: instructions, bus cycles, per-opcode-group costs) |
| `?` | help |

Breakpoints and watchpoints also fire during RUN (the debugger supervises
each instruction). Performance counters are pure counts inside the emulator;
instructions/second is computed at the `main.py` boundary, the only place
wall-clock is allowed next to the render backend (§19.6). Emulator bugs
(`EmulationError`, spec §18.3) print a pasteable crash dump — registers, PC,
last 8 dispatched opcodes, clock cycle — and exit 1.

## 5. Snapshots (`--save` / `--restore`, M8)

A snapshot captures the full CPU state (incl. FP11 and per-mode SP banks),
MMU/map state (11/70, 11/94), RAM/ROM contents, every device's registers and
internal state (each device implements `serialize()`/`deserialize()` as part
of the `BusDevice` contract, spec §15), and the scheduler's event queue +
cycle counter + sequence allocator. Restore is exact: continuing from a
snapshot produces byte-identical subsequent bus traffic (spec §11.8), pinned
by `test_snapshot.py` and `test_determinism.py`. Format: a JSON wrapper
(`format`/`version`/`profile`/`created` header) around a zlib+base64 binary
core image — still introspectable in a hex editor. In the debugger, `S <file>`
snapshots at the current breakpoint/halt (§15).

```sh
eleven13 --machine 11/70 --disk rp06.dsk --save boot.snap.json
# ... later:
eleven13 --machine 11/70 --disk rp06.dsk --restore boot.snap.json
```

Restoring a snapshot into a different profile (or RAM size) is rejected with
an actionable `ValueError` (§18.3 layer 3).

## 6. Power-fail / power-restore (`--powerfail` / `--powerrestore`, M8)

`--powerfail OCT_CYCLES` schedules the vector-000024 power-fail trap on the
deterministic clock; `--powerrestore OCT_CYCLES` schedules the restore
restart the same way. The handler at 000024 sees the usual pushed PC/PSW; the
"≥ 2 ms simulated runtime remaining" of the real machine is the cycle gap you
choose between the two flags. RSTS/RSX-style orderly shutdown and auto-restart
code paths become testable without real power events (spec §5.5, §11.9) —
`test_console.py::test_powerfail_and_restore_trap_vector_024` shows the shape.

## 7. Graphical front end (`--gui`)

```sh
eleven13 --gui                  # needs stdlib tkinter (libtk + display); no other flags needed
```

`--gui` opens a tkinter window (the optional tkinter host surface sanctioned by
spec §8). It opens on a **setup form** — every option is chosen in the GUI:

- **Machine** dropdown (11/03, 11/20, 11/70, 11/94; pre-selected if `--machine`
  was also given), **RAM size** in octal words (blank = profile max)
- **Media**: repeatable browse/add lists for `--disk`, `--rl`, `--tape`,
  `--papertape`; a boot-ROM picker (`--bootrom`); snapshot **save**/**restore**
  file pickers (`--save`/`--restore`, §15)
- **Checkboxes**: `--throttle`, `--debug`, `--vt11`; power-fail / power-restore
  cycle entries (`--powerfail`/`--powerrestore`, vector 000024, §5.5)

**Start** assembles the machine from the form (via `pdp11.gui.setup_to_argv` →
the normal CLI parser, so GUI and console share one validation path) and swaps
to the session view:

- **Left pane — console:** the plain-text DL11 console / ODT / debugger shell.
  Type in the input line at the bottom and press Enter to send a line (Ctrl-C
  in the line sends the BREAK byte). A status bar shows RUN vs `HALT — @`
  plus the bus-cycle counter.
- **Right pane — instructions:** a per-machine cheat sheet (built by
  `pdp11.gui.instructions_for`): power-on PC and boot behavior for the chosen
  machine, the attached media, the ODT command table (`<addr>/` examine,
  `<addr>\` deposit, `G` go, `Q` quit), the debugger table when `--debug` is
  on, and the session flags in effect.
- **Buttons:** `BREAK (Ctrl-C)` sends the front-panel BREAK key (halts a
  running machine into ODT, §13); `Quit` sends BREAK then closes cleanly
  (a `--save` snapshot still lands on exit).

The emulator runs on a worker thread and talks to the widgets only through
queues, so the deterministic core is untouched; the module imports tkinter
lazily and the headless test suite (`test_gui.py`) needs no display. On a
machine without libtk, `--gui` exits with an actionable message. The plain
stdio console (everything above) remains for headless/scripted use.

## 8. Exit status

`0` on clean HALT-to-ODT exit; non-zero on user-facing misuse (bad arguments,
unreadable image) with an actionable message. Hardware-signaling conditions
inside the machine (`BusInit`) are never user-visible exceptions — the
machine layer routes them to the trap vectors (spec §18.3).
