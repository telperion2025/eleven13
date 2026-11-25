# eleven13 — a modular DEC PDP-11 emulator

`eleven13` emulates four classic PDP-11 machines — the **PDP-11/03 (LSI-11)**, **PDP-11/20**, **PDP-11/70** and **PDP-11/94** — from selectable machine profiles. The name is the machine's number in its own base: 13 octal = 11 decimal. The distribution/CLI name is `eleven13`; the Python package root is `pdp11`.

The full engineering specification lives in **[DESIGN.md](DESIGN.md)** (Part A §§1–17: engineering spec; Part B §§18–22: tooling, conventions, submission protocol). This README is the short version.

## Status

**All eight milestones M1–M8 are implemented** (see [docs/acceptance-checklist.md](docs/acceptance-checklist.md) for the spec §11 acceptance-criteria status after M8):

- **M1** — the complete package tree of DESIGN.md §3 with the §17 scaffolding, the four machine profiles (`pdp11/config/profiles.py`) as the single source of truth for per-model differences, the deterministic system clock / event scheduler (`pdp11/system_clock.py`, spec §5.6), the I/O page router (`pdp11/memory/io_page.py`), and the **11/20 (KD11-B) BASE instruction set**: full address-mode machinery (all 8 modes with the PC special cases), single/double-operand word+byte groups, all 16 branches, JMP/JSR/RTS, EMT/TRAP/BPT/IOT, HALT/WAIT/RESET/RTI/RTT, condition-code operators, SWAB, traps (bus error 000004, reserved 000010, T-bit 000014, IOT 000020, EMT 000030, TRAP 000034), interrupt priority logic, and the §5.4 capability gate (the 11/20 consumes microcoded-only opcodes as no-ops) — pinned by golden CPU tests including the hand-assembled decrement-loop transcript (spec §22.2);
- **M2** — Unibus arbitration (NPR/BR daisy chain, cycle stealing), DL11 console, ODT front panel, M9312 boot-ROM loader, and the full machine run loop;
- **M3** — KW11-L line clock, RK11/RK05 reference driver, raw disk containers (RT-11 boot path on the 11/20 verified end-to-end with a hand-built boot block);
- **M4** — 11/03 Q-Bus profile (BIAK/BDMG), EIS on the KEF11, DZ11, RX11/RX01 (RX01 boot path on the 11/03 verified end-to-end);
- **M5** — 11/70 MMU (3-mode I/D APR sets, MMR0–3), Unibus map, Massbus (RH70 + RP06/RP07), FP11;
- **M6** — 11/94 (DCJ-11): CIS string ops, Q22-CD 22-bit map, J-11 MMU flavor, FPJ;
- **M7** — VT100/VT52 terminal frontends and the LA36 hardcopy wired via `--console`, extended debugger, and the §11.1 diagnostic harness (4/4 profiles PASS on the built-in smoke diagnostic);
- **M8** — session snapshots with byte-identical restore, power-fail/restore (vector 000024), SIMH `.tap`/raw container round-trips, performance instrumentation (~225k instructions/s measured on the 11/20 spin loop, `pdp11/tests/test_determinism.py`), and the CI coverage gates.

No `NotImplementedError` stubs remain — every instruction in scope is implemented (the former §19.10 loud stubs, devices and CIS CVT* ops, all landed; unused opcodes trap reserved as hardware does). Real DEC distribution media (RT-11 RX01/RK05/RL01 disks, RSX-11D kits and `.TPC` tapes) are checked in under `images/` for use with `--disk`.

## Install and run

Requires Python ≥ 3.11, < 3.13 and [uv](https://docs.astral.sh/uv/). The emulator core is **stdlib-only** (hard rule, spec §18.1); dev tools come from the `dev` extra.

```sh
uv sync --extra dev        # create the venv, install pytest/ruff/mypy
uv run eleven13 --machine 11/20          # CLI entry point
uv run python -m pdp11.main --machine 11/20   # same thing, module form
uv run eleven13 --gui                          # tkinter GUI: machine + all options in the setup form, then console + instructions panes
```

`--machine {11/03,11/20,11/70,11/94}` is **required** — there is no default, because behavior differs fundamentally per machine (spec §4.1). See [docs/user-manual.md](docs/user-manual.md) for the full CLI reference and [docs/operator-manual.md](docs/operator-manual.md) for per-machine power-on/boot behavior.

> The full machine run loop (SystemBoard + bus + DL11 console + ODT) is live on all four profiles. Attach a disk container with `--disk` (format auto-detected per DESIGN.md §16 — real RT-11/RSX media are under `images/`), or supply a real M9312 boot PROM with `--bootrom`. Snapshots (`--save`/`--restore`) and power-fail injection (`--powerfail`/`--powerrestore`) are available per §15/§5.5.

## Quality gates (spec §18.2)

```sh
make test     # uv run pytest            — 362 tests green
make lint     # uv run ruff check        — zero findings
make type     # uv run mypy --strict ... — clean on cpu/ bus/ memory/ config/ + core modules
make check    # all of the above
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs `make check` on every PR and push to `main`, plus the per-package coverage floors of spec §18.2 (`cpu/` and `bus/` ≥ 90%, `devices/` ≥ 70%).

## Documentation

| Document | Contents |
|---|---|
| [docs/user-manual.md](docs/user-manual.md) | CLI reference, running a machine, console & ODT, debugger, snapshots, power-fail injection |
| [docs/operator-manual.md](docs/operator-manual.md) | per-machine power-on behavior, front-panel semantics, the 11/20 console-switch bin-load ritual, boot ROMs |
| [docs/architecture.md](docs/architecture.md) | system assembly, the single MemoryInterface, deterministic timing model, dispatch design, error taxonomy |
| [docs/developer-manual.md](docs/developer-manual.md) | mandatory comment/type standard, coding conventions, how to add an instruction or a device, testing guide, milestones |
| [docs/instruction-reference.md](docs/instruction-reference.md) | the implemented BASE instruction set with opcodes, flag behavior and citations |
| [docs/acceptance-checklist.md](docs/acceptance-checklist.md) | spec §11 acceptance criteria with current status |
| [docs/glossary.md](docs/glossary.md) | spec §20 glossary (APR, NPR, BIAK/BDMG, ODT, …) |
| [docs/references.md](docs/references.md) | spec §21 authoritative references |

## Repository layout

```text
pdp11/            the emulator package (layout fixed by DESIGN.md §3)
  config/         MachineProfile + the four profiles + per-run overrides
  cpu/            core, registers/PSW, instructions/, MMU, FP11, traps, interrupts
  memory/         RAM, ROM, I/O page, bus map registers
  bus/            BusDevice contract + Unibus / Q-Bus / Massbus
  devices/        DL11, DZ11, RK05/RK11, RX11, RL11, RH70/RP, TM11, TQK11, PC11, clocks
  graphics/       terminal frontends (VT100/VT52/LA36) + render backends
  disk_images/    SIMH-compatible containers (.tap, RK05/RX01/RP raw, PC11)
  control/        ODT front panel, debugger, snapshots, ROM loader
  tests/          skeleton tests + cpu_golden.py + device/diagnostic suites
docs/             the manuals above
DESIGN.md         the binding specification
```

## License / provenance

Behavioral claims cite the DEC references listed in [docs/references.md](docs/references.md); where the spec and this implementation had to be reconciled during M1–M8, the deviation is noted in the PR description and in [docs/acceptance-checklist.md](docs/acceptance-checklist.md).
