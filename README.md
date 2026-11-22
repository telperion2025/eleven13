# eleven13 — a modular DEC PDP-11 emulator

`eleven13` emulates four classic PDP-11 machines — the **PDP-11/03 (LSI-11)**, **PDP-11/20**, **PDP-11/70** and **PDP-11/94** — from selectable machine profiles. The name is the machine's number in its own base: 13 octal = 11 decimal. The distribution/CLI name is `eleven13`; the Python package root is `pdp11`.

The full engineering specification lives in **[DESIGN.md](DESIGN.md)** (Part A §§1–17: engineering spec; Part B §§18–22: tooling, conventions, submission protocol). This README is the short version.

## Status

**Milestone M1 is implemented** (see [docs/acceptance-checklist.md](docs/acceptance-checklist.md)):

- the complete package tree of DESIGN.md §3, with the §17 scaffolding in place;
- the four machine profiles (`pdp11/config/profiles.py`) — single source of truth for per-model differences;
- a deterministic system clock / event scheduler (`pdp11/system_clock.py`, spec §5.6);
- the I/O page router (`pdp11/memory/io_page.py`);
- the **11/20 (KD11-B) BASE instruction set** on flat memory: full address-mode machinery (all 8 modes with the PC special cases), single/double-operand word+byte groups, all 16 branches, JMP/JSR/RTS, EMT/TRAP/BPT/IOT, HALT/WAIT/RESET/RTI/RTT, condition-code operators, SWAB, traps (bus error 000004, reserved 000010, T-bit 000014, IOT 000020, EMT 000030, TRAP 000034), interrupt priority logic, and the §5.4 capability gate (the 11/20 consumes microcoded-only opcodes as no-ops);
- golden CPU tests including the hand-assembled decrement-loop transcript (spec §22.2);
- every M2–M8 module present as a documented stub raising `NotImplementedError` with its spec section and milestone (spec §19.10) — never a silently-wrong fake.

M2 (Unibus + DL11 console + ODT) through M8 (snapshots, power-fail/restore, SIMH container round-trips) are fenced per DESIGN.md §22 and land in their own milestones.

## Install and run

Requires Python ≥ 3.11, < 3.13 and [uv](https://docs.astral.sh/uv/). The emulator core is **stdlib-only** (hard rule, spec §18.1); dev tools come from the `dev` extra.

```sh
uv sync --extra dev        # create the venv, install pytest/ruff/mypy
uv run eleven13 --machine 11/20          # CLI entry point
uv run python -m pdp11.main --machine 11/20   # same thing, module form
```

`--machine {11/03,11/20,11/70,11/94}` is **required** — there is no default, because behavior differs fundamentally per machine (spec §4.1). See [docs/user-manual.md](docs/user-manual.md) for the full CLI reference and [docs/operator-manual.md](docs/operator-manual.md) for per-machine power-on/boot behavior.

> M1 note: the CLI currently assembles the profile and prints its skeleton notice; the full machine run loop (SystemBoard + DL11 + ODT) lands with M2, per the milestone fence. The CPU core is fully exercised by the test suite against flat RAM via `pdp11.board.SystemBoard`.

## Quality gates (spec §18.2)

```sh
make test     # uv run pytest            — 30 tests green at M1
make lint     # uv run ruff check        — zero findings
make type     # uv run mypy --strict ... — clean on cpu/ bus/ memory/ config/ + core modules
make check    # all of the above
```

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

Behavioral claims cite the DEC references listed in [docs/references.md](docs/references.md); where the spec and this implementation had to be reconciled during M1, the deviation is noted in the PR description and in [docs/acceptance-checklist.md](docs/acceptance-checklist.md).
