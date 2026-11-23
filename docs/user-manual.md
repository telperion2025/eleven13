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
```

| Option | Meaning | Milestone |
|---|---|---|
| `--machine {11/03,11/20,11/70,11/94}` | **Required.** Selects the emulated model. There is deliberately no default (spec §4.1). | M1 |
| `--memory OCT_WORDS` | RAM size in **octal** words, overriding the profile ceiling. | M1 (parsed) |
| `--disk IMAGE` | Attach a disk image; format auto-detected from file size per spec §16 (repeatable). Dispatched by container: RK05-sized → RK11 at 177400, RX01-sized → RX11 at 177170, RP06/RP07-sized (512-byte sectors) → RH70 at 176700 on the 11/70 (Massbus). | M3/M4/M5 |
| `--console {vt100,vt52,la36,odt}` | Terminal frontend (default `vt100`). | M2/M7 |
| `--bootrom FILE` | Load an M9312 512-word boot PROM image (spec §13). Without it, a simplified built-in bootstrap is used — documented as **non-authentic**. | M2/M3 |
| `--config FILE.json` | Per-run overrides (memory size, devices, volumes) layered over the profile; see `pdp11/config/config.json` for the schema example. | M2+ |
| `--throttle` | Slow execution to real-machine speed. Default is full speed (`--maxspeed` stance, spec §14). Throttling lives only in `main.py`, never in the machine core. | M2+ |
| `--maxspeed` | Explicit no-throttle (default behavior). | M2+ |
| `--debug` | Extended debugger when the machine halts (spec §9). | M7 |
| `--save FILE` / `--restore FILE` | Session snapshot save/restore (spec §15). | M8 |
| `--powerfail` / `--powerrestore` | Inject the power-fail / power-restore sequence through vector 000024 so OS shutdown/restart code paths are testable (spec §5.5, §11.9). | M8 |

All numeric CLI arguments that denote machine quantities are **octal**
(spec §10.1), e.g. `--memory 0o20000`.

### Current M5 behavior

All four profiles assemble and run. On the **11/70** (`--machine 11/70`)
the machine comes up with the KD11-Z: 22-bit physical memory, the MMU
(off until MMR0 MME is set — boot code maps the kernel APRs first), the
Unibus map, the RH70 Massbus controller at 176700 with an RP06/RP07 drive
when `--disk rp06.dsk` is given, and the FP11. HALT drops to ODT, which on
MMU machines prints the `^ address` display first (spec §13). Example:

```sh
eleven13 --machine 11/70 --disk rp06.dsk
```

## 3. Running a machine and the console

Once M2 lands, the flow is:

1. `eleven13 --machine 11/03 --disk rk0.img` assembles the machine from the
   profile and starts at the profile's power-on PC (spec §13).
2. The DL11 console connects to the selected terminal frontend
   (`--console vt100` default). The VT100 frontend is a real escape-sequence
   state machine, not raw pass-through (spec §7/§8); it is a strictly 7-bit
   ASCII device — host keys with no 7-bit equivalent are dropped with a
   debug-log note, never passed through as multi-byte garbage (spec §8).
3. On HALT (or BREAK), control falls to **ODT** — the `@` prompt: `/`
   examine, `\` deposit, `G` go, octal addresses only (spec §9).

## 4. Debugger (`--debug`, M7)

Breakpoints, watchpoints, MMU page dumps, interrupt-pending display,
single-step trace with disassembly, device statistics, container inspection —
plus the performance instrumentation of spec §14 (instructions/second,
per-module cycle costs, function-level timing dumps). Emulator bugs
(`EmulationError`, spec §18.3) print a pasteable crash dump: registers, PC,
last 8 dispatched opcodes, clock cycle.

## 5. Snapshots (`--save` / `--restore`, M8)

A snapshot captures the full CPU state, MMU/map state (11/70, 11/94), RAM/ROM
contents, every device's registers and internal state (each device implements
`serialize()`/`deserialize()` as part of the `BusDevice` contract, spec §15),
and the scheduler's event queue + cycle counter. Restore is exact: continuing
from a snapshot produces byte-identical subsequent bus traffic (spec §11.8).
Format: JSON wrapper around a binary core image, introspectable in a hex
editor.

## 6. Power-fail / power-restore (`--powerfail` / `--powerrestore`, M8)

Injects the vector-000024 power-fail trap (with ≥ 2 ms simulated runtime
remaining before "death") and the power-restore restart, so RSTS/RSX-style
orderly shutdown and auto-restart are testable without real power events
(spec §5.5, §11.9).

## 7. Exit status

`0` on clean HALT-to-ODT exit; non-zero on user-facing misuse (bad arguments,
unreadable image) with an actionable message. Hardware-signaling conditions
inside the machine (`BusInit`) are never user-visible exceptions — the
machine layer routes them to the trap vectors (spec §18.3).
