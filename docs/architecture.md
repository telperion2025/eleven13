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
   (Unibus model); M4 lands the LSI-11 quirk (odd word access splits into
   byte transfers, nonexistent memory still traps) — DCJ-11 strict is M6 and raises
   `NotImplementedError` until then (spec §19.10).
2. `EmulationError` (`pdp11/errors.py`) — emulator bugs (broken invariants);
   crash loudly with a state dump, never swallowed.
3. Plain Python exceptions — user-facing misuse (bad CLI args, unreadable
   image) with actionable messages.

Logging: stdlib `logging` rooted at `"pdp11"`; INFO carries configuration and
boot milestones only, DEBUG carries device register access behind per-device
gates, custom TRACE (level 5) the fetch/decode path (spec §18.3).

## 6. M5: 11/70 MMU, Unibus map, Massbus, FP11 (spec §5.7/§5.8/§6/§7)

- **MMU** (`pdp11/cpu/mmu.py`): 6 APR sets (I/D × kernel/supervisor/user × 8
  pages) plus MMR0-3 as I/O-page windows (APR blocks at 172200-177660,
  MMR0-2 at 177572, MMR3 at 172516). `MMU.translate(va, mode, …)` picks the
  space (fetches always I; data uses D when MMR3's UDS/SDS/KDS bit for the
  mode is set), checks ACF (0/3/7 no-access, 1/2 read-only, 4-6 RW), the page
  length field (expand-up/down), sets the A/W bits, and returns
  `PA = (PAR<<6)|VA<12:0>` masked to 22 bits. Aborts raise `MMUAbort` (a
  `BusInit` subclass carrying the vector: 000250 with MMR0 TENB, else
  000004) after recording NR/RO/PL + mode/space/page in MMR0 and the VA in
  MMR2. With MMR0 MME clear everything is flat. `SystemBoard` routes CPU
  accesses through `_va_to_pa`; on 22-bit machines the top 8 KB
  (0o17760000+) is the I/O page with a flat alias at 0o160000-0o200000 for
  MMU-off boot code.
- **Unibus map** (`pdp11/memory/map_registers.py`): 32 22-bit entries,
  window at 0o172400 (4-byte stride: low word, then bits <21:16>), page 31
  fixed to the I/O page. Applied only on the `dma_host` DMA face and only
  when MMR3 BME is set; `massbus_host` is direct 22-bit physical
  (`(BAE<<16)|BA`) because the RH70 does not use the Unibus map.
- **Modes**: `CPUState(banked=profile.has_mmu)` keeps a per-mode SP bank
  (`sps[]`, `switch_mode`); trap/interrupt entry (`traps.enter_vector`)
  fetches the vector from kernel D space, records the previous mode, and
  pushes on the new mode's stack; RTI switches back after the pops. The PSW
  window at 0o177776 drops the mode bits without an MMU (11/20).
- **FP11** (`pdp11/cpu/fp11.py` + `cpu/instructions/fp11.py`): six 64-bit
  accumulators (F in the top 32 bits), FPS/FEC/FEA, exact-Fraction
  arithmetic rounded half-up at store (FPS T truncates), conversions
  LDCif/STCfi/LDEXP/STEXP, and the full 0o17xxxx decode (fop = IR<11:8>).
  Exceptions trap through 000244 when enabled; OP/DZRO/UNDFV NOP the
  operation. FP operand EAs are length-aware (F=4, D=8 bytes) with the
  mode 6/7 displacement fetched from I space.
- **RH70/RP** (`pdp11/bus/massbus.py` constants, `pdp11/devices/rh70_rp.py`):
  register window at 176700 (size 0o54), GO dispatch with the PGE rule,
  seek via `SystemClock.schedule`, NPR word-at-a-time `tick()` through
  `massbus_host`, completion interrupt at vector 000254/BR5 (level-based
  approximation, §22.4), RP06/RP07 geometry (815×19×22, 630×32×43 sectors
  of 512 B) over raw containers.

## 7. Module map

| Path | Role | M1 status |
|---|---|---|
| `pdp11/main.py` | CLI shell (spec §4.1) | M1 notice; full assembly M2 |
| `pdp11/config/profiles.py` | 4 profiles, capability tags, bus-error models | done (§17.2) |
| `pdp11/system_clock.py` | deterministic scheduler | done (§17.3) |
| `pdp11/board.py` | SystemBoard / MemoryInterface | done (flat 16-bit) |
| `pdp11/cpu/core.py` | fetch/decode/execute, dispatch, cost table | done (M1 scope) |
| `pdp11/cpu/registers.py` | R0–R7, PSW | done (§17.6) |
| `pdp11/cpu/instructions/` | BASE handlers + gated EIS/FPU/CIS | BASE done; EIS (M4), **CIS string + decimal + CVT* (M6/post-M8)**, FPU (M5) done |
| `pdp11/cpu/traps.py`, `interrupts.py` | vectors, IPL logic | done (flat mode) |
| `pdp11/cpu/mmu.py`, `cpu/fp11.py` | MMU, FP11 engine | **done (M5)** — see §7 |
| `pdp11/memory/` | RAM, ROM, I/O page, map registers | RAM/ROM/IOPage done; **Unibus map (M5) + Q22-CD map (M6)** |
| `pdp11/bus/` | BusDevice ABC; Unibus/Q-Bus/Massbus | ABC + Unibus (M2) + Q-Bus BIAK/BDMG (M4, 22-bit on 11/94 M6) + **Massbus constants done (M5)** |
| `pdp11/devices/dl11.py` | console DL11 | done (M2); other devices stubbed to their milestones |
| `pdp11/control/front_panel.py`, `rom_loader.py` | ODT + boot PROMs | done (M2); **debugger done (M7)**; snapshot stub (M8) |
| `pdp11/control/debugger.py` | extended debugger + §14 counters + §18.3 dump | **done (M7)** |
| `pdp11/graphics/` | terminal interfaces | ConsoleTerminal + StdioBackend (M2); **VT100/VT52 + LA36 done (M7)**; curses/pixel backends post-v1 |
| `pdp11/main.py` | CLI + assembly + run loop | done (M2) |
| `pdp11/devices/` | DL11…KW11-P | DL11, KW11-L, RK11/RK05 (M2/M3), DZ11, RX11 (M4), **RH70+RP06/RP07 (M5)** done; rest stubbed to their milestones |
| `pdp11/graphics/` | terminals + render backends | StdioBackend (M2); **vt100.py/la36.py frontends done (M7)**; curses/pixel post-v1 |
| `pdp11/control/` | ODT, debugger, snapshots, ROM loader | ODT+ROM loader (M2), debugger (M7), **snapshots (M8)** done |
| `pdp11/disk_images/` | SIMH-compatible containers | RK05/RX01 raw (M3), RP06/RP07 (M5); **`.tap`/`PtImage` + simh_compat exporters (M8)**; SIMH cross-read of operator-supplied images §22.1 |
| `pdp11/tests/` | skeleton + golden + device/diagnostic suites | 205 tests green through M7 (`test_vt100.py`, `test_la36.py`, `test_debugger.py`, `machine_diagnostics.py` §11.1 harness) |
