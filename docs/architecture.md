# eleven13 Architecture

Source: DESIGN.md §4 (core architecture), §5 (CPU), §6 (bus), §18.3 (error
taxonomy). This document describes how the M1 implementation realizes them.

## 1. Assembly: profile → SystemBoard

```
main.py (CLI) ──► config/profiles.py (MachineProfile) ──► board.SystemBoard
                                                              │  ┌─────────┐
                                                              ├──┤ CPU     │──► instructions/
                                                              ├──┤ RAM/ROM │
                                                              ├──┤ IOPPage │──► devices (M2+)
                                                              └──┤ SystemClock (deterministic, §5.6)
```

`SystemBoard` (`pdp11/board.py`) connects CPU + memory + I/O page per the
profile and acts as the **single `MemoryInterface`** the CPU talks to
(spec §4.2). In M1 that interface is flat 16-bit (11/20): RAM below
`profile.iopage_base` (0o177600), the I/O page above it, the PSW register
window at 0o177776–0o177777 attached to the I/O page. In M5/M6 the same class
becomes the MMU-mapped 22-bit interface, selected by `profile.has_mmu` — never
by a `profile.key == "11/70"` check (spec §19.1).

## 2. Bus-device contract

Everything on a bus implements `BusDevice` (`pdp11/bus/abstract_bus.py`,
spec §6.1): word/byte `read_*`/`write_*` on **offsets into the device's own
window** (never absolute addresses), optional `dma_request()` /
`interrupt_pending()` / `reset()` / `tick()`, and the mandatory
`serialize()`/`deserialize()` snapshot pair (spec §15). Devices attach via
`IOPage.attach(address, device)`, which rejects overlapping windows at boot
(a backplane short circuit is a wiring error). The CPU never imports
`devices/`; devices never import `cpu/` — both depend only on
`bus/abstract_bus.py` (spec §19.2).

## 3. CPU core and dispatch

`pdp11/cpu/core.py` implements fetch/decode/execute:

- **State**: `CPUState` — R0–R7 (R6=SP, R7=PC) in an `array("H")`, the `PSW`
  (N/Z/V/C/T, IPL, mode bits; mode bits read 0 without an MMU), `halted`,
  `waiting` (spec §5.1).
- **Address modes**: `resolve_operand(state, bus, spec, is_source, is_byte)`
  (spec §5.2) handles all 8 modes with the PC special cases: mode 2 with R7
  is the immediate literal (always a full word), mode 3 with R7 is the
  absolute address, modes 6/7 with R7 are relative with a **signed** word
  displacement. Mode 2 byte mode increments by 1 (except PC). Side effects
  happen exactly once per operand specifier.
- **Dispatch**: the `@opcode(pattern, mask=…, caps=…, fmt=…)` decorator
  registers handlers into a module registry; `CPU.__init__` renders it into
  per-nibble buckets (bits 15–12, spec §5.3) with masked-pattern scans —
  a prebuilt table instead of method resolution in the hot path (spec §14).
  Word/byte opcode pairs (bit 15 free) share a bucket keyed on bits 14–12.
- **Capability gate** (spec §5.4): if the profile lacks a handler's tag, the
  KD11-B no-op set of spec §2 (MUL/DIV/ASH/ASHC/XOR/SOB/MARK/SXT/MTPS/MFPS/SPL)
  is consumed as a no-op (operands still resolve, PC advances past the whole
  instruction, no trap); other missing capabilities (FPU/CIS/MMU-only) trap as
  reserved through 000010 (spec §5.8). Truly unassigned opcodes always trap
  000010.
- **Traps/interrupts** (spec §5.5): `cpu/traps.py` (vector table, push PC/PSW,
  load new PC/PSW; bus errors pass the faulting instruction address) and
  `cpu/interrupts.py` (IPL comparison, sampled between instructions; among
  equal levels, bus slot order wins — the daisy chain).

## 4. Deterministic timing model (spec §5.6)

- The quantum is **one bus cycle** (~150 ns Unibus / ~300 ns Q-Bus).
- `SystemClock` keeps a monotonic cycle counter plus a heap of
  `(due_cycle, seq, callback)` events. Devices schedule (never poll); the
  CPU drains all due events after each instruction. **No wall-clock reads
  inside the machine** — `time.*` is allowed only in `main.py` (throttle
  boundary) and `graphics/render_backend.py` (spec §19.6).
- Per-instruction cost: `COSTS[op_word >> 12]` base cycles (11/20 column of
  the Processor Handbook timing table, filled per opcode at implementation
  time — spec §17.10.6) **plus** one cycle per actual bus transfer, counted
  by the board (`cycles_used`). This makes timing deterministic by
  construction and replay-identical (verified by
  `test_decrement_loop_replays_identically`).

## 5. Error taxonomy (spec §18.3)

Three layers, never mixed:

1. `BusInit` — hardware signaling inside devices/bus (e.g. Unibus timeout);
   caught only by the machine layer (CPU core), routed to vector 000004
   (Unibus model) — the LSI-11/DCJ-11 quirk sets are M4/M6 and raise
   `NotImplementedError` until then (spec §19.10).
2. `EmulationError` (`pdp11/errors.py`) — emulator bugs (broken invariants);
   crash loudly with a state dump, never swallowed.
3. Plain Python exceptions — user-facing misuse (bad CLI args, unreadable
   image) with actionable messages.

Logging: stdlib `logging` rooted at `"pdp11"`; INFO carries configuration and
boot milestones only, DEBUG carries device register access behind per-device
gates, custom TRACE (level 5) the fetch/decode path (spec §18.3).

## 6. Module map

| Path | Role | M1 status |
|---|---|---|
| `pdp11/main.py` | CLI shell (spec §4.1) | M1 notice; full assembly M2 |
| `pdp11/config/profiles.py` | 4 profiles, capability tags, bus-error models | done (§17.2) |
| `pdp11/system_clock.py` | deterministic scheduler | done (§17.3) |
| `pdp11/board.py` | SystemBoard / MemoryInterface | done (flat 16-bit) |
| `pdp11/cpu/core.py` | fetch/decode/execute, dispatch, cost table | done (M1 scope) |
| `pdp11/cpu/registers.py` | R0–R7, PSW | done (§17.6) |
| `pdp11/cpu/instructions/` | BASE handlers + gated EIS/FPU stubs | BASE done; EIS/FPU/CIS stubs |
| `pdp11/cpu/traps.py`, `interrupts.py` | vectors, IPL logic | done (flat mode) |
| `pdp11/cpu/mmu.py`, `cpu/fp11.py` | MMU, FP11 engine | stubs (M5/M6) |
| `pdp11/memory/` | RAM, ROM, I/O page, map registers | RAM/ROM/IOPage done; maps stub |
| `pdp11/bus/` | BusDevice ABC; Unibus/Q-Bus/Massbus | ABC + Unibus arbitration done (M2); Q-Bus/Massbus stubs (M4/M6, M5) |
| `pdp11/devices/` | DL11…KW11-P | documented stubs (M2–M6) |
| `pdp11/graphics/` | terminals + render backends | stubs (M7) |
| `pdp11/disk_images/` | SIMH-compatible containers | stubs (M3/M8) |
| `pdp11/control/` | ODT, debugger, snapshots, ROM loader | stubs (M2/M7/M8) |
| `pdp11/tests/` | skeleton + golden + device/diagnostic suites | M1 tests green |
