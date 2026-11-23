# eleven13 Developer Manual

How to work on this codebase. The non-negotiable conventions are DESIGN.md
§10 (comment standard) and §19 (agent conventions); where they conflict with
something you see here, DESIGN.md wins. Milestone fences are §22: work one
milestone at a time; stubs beat silent guesses (§19.10).

## 1. Tooling and gates

```sh
uv sync --extra dev
make test      # pytest (pdp11/tests)
make lint      # ruff check — zero findings
make type      # mypy --strict on pdp11/config pdp11/cpu pdp11/bus pdp11/memory
               #   + pdp11/system_clock.py pdp11/board.py pdp11/main.py
```

CI gates (spec §18.2): ruff zero findings; mypy --strict clean on the gated
modules (device modules may carry targeted ignores with a linked TODO);
pytest all green, **no skip markers without a linked issue ID**; coverage
cpu/ bus/ ≥ 90 %, devices ≥ 70 %; a determinism job replays a scripted
10 M-instruction run cycle-for-cycle.

## 2. Mandatory code standard (spec §10, §19)

1. **Octal-first.** All addresses, opcodes, vectors and bitmasks are Python
   octal literals (`0o177570`). Decimal only for off-metal quantities (sector
   sizes, host-side counts).
2. **Every module** starts with a docstring header: purpose, register map
   where applicable, references (the §21 list). **Every class and public
   method** has a docstring (purpose, args, side effects, hardware notes).
   **Non-trivial lines** (bitfield extraction, overflow traps, quirk
   emulation) get a trailing comment tying them to hardware behavior.
3. **Type hints on all public interfaces**; mypy --strict clean on gated
   modules. No `# type: ignore` without a linked issue (§18.2).
4. **Registers are data**: a device CSR window is an ordered list/array with
   module-level UPPER_OCTAL mask constants (§19.4) — no string-packed state;
   every device implements `serialize()`/`deserialize()` (§15).
5. **No threading inside the machine** (§19.5); **no wall-clock calls**
   except `main.py` and `graphics/render_backend.py` (§19.6).
6. **No model checks outside `config/profiles.py`** (§19.1): behavioral
   differences flow through profile fields/capability tags.
7. **No cross-imports between `cpu/` and `devices/`** (§19.2) — only
   `bus/abstract_bus.py` interfaces.
8. **Wiring restraint** (§19.10): if a spec section isn't reachable in the
   current milestone, create the module with a full docstring and
   `NotImplementedError("§x … — Mx")` raisers. Never a fake implementation.
9. **Naming** (§19.9): modules lowercase_underscore (`rk11_rk05.py`), classes
   PascalCase (`RK11`), register constants UPPER_OCTAL (`RKCS`, `DONE =
   0o000200`).

## 3. Adding an instruction (the §5.3/§17.7 pattern)

1. In the right `pdp11/cpu/instructions/*.py` module, write a handler
   `def instr_XXX(cpu: CPU) -> None:` with a docstring citing the Processor
   Handbook (instruction, page/table) and registering with
   `@opcode(0o…., caps="BASE"|"EIS"|…, mask=0o…, fmt=…)`.
   Operand specifiers of single/double-operand and EIS group-0 formats are
   **embedded in the opcode word**; fetch extra words only inside
   `resolve_operand` (immediate/absolute/index displacement).
2. Add the per-machine cycle cost to `COSTS` in `cpu/core.py` at the same
   time (§17.10.6 — timing is never retrofitted), with a comment citing the
   handbook timing table.
3. If the opcode is capability-gated, pick the right `caps` tag and add the
   handler name to `KD11B_NOOP_HANDLERS` in `instructions/__init__.py` when
   the 11/20 must no-op it (spec §2 list).
4. Add golden tests to `pdp11/tests/cpu_golden.py`: hand-assembled words,
   register/PSW assertions, and address-mode cases. Run `make check`.

## 4. Adding a device (spec §6.1, §7, §15)

1. New module `pdp11/devices/<name>.py` with the full header docstring:
   register map (octal offsets, reset values, R/W masks) verbatim-style from
   the PDP-11 Peripherals Handbook, plus references.
2. Implement `BusDevice`: `read_word/write_word/read_byte/write_byte` over
   the window, `interrupt_pending()`, `reset()`, `tick()`/scheduled events via
   `SystemClock.schedule` (never wall clock), DMA via `dma_request()` if
   applicable, and `serialize()`/`deserialize()` (validate keys; wrong
   version → `ValueError`).
3. Attach in the profile's `default_devices` and via
   `SystemBoard.attach_device(address, device)`; slot order = daisy-chain
   priority (§6.1).
4. Tests in `pdp11/tests/device_unit.py` (PRNG-seeded state-machine tests);
   the RK05 driver is the reference implementation including an end-to-end
   "boot RT-11 from RK05" transcript (§7).

## 5. Determinism and snapshots (spec §5.6, §15)

- Never read the wall clock or randomness inside the machine core; device
  latencies are scheduled in bus cycles on the `SystemClock`.
- A failing session should be capturable via snapshot and replayed in a unit
  test byte-identically (§15, §11.8).

## 6. Milestones (spec §12) and where they land

| Milestone | Deliverable | Status |
|---|---|---|
| M1 | Skeleton + 11/20 BASE CPU on flat memory (§17 scaffolding) | **done** — see docs/acceptance-checklist.md |
| M2 | Unibus + I/O page + DL11 console + ODT shell | in progress — Unibus arbitration done (`bus/unibus.py`, `test_unibus.py`); DL11/ODT/boot next |
| M3 | KW11-L + RK05/RK11 + containers; RT-11 boots on 11/20 | stubbed |
| M4 | 11/03 Q-Bus + EIS (KEF11); DZ11 | stubbed |
| M5 | 11/70: MMU, Unibus map, Massbus RH70+RP06, FP11 | stubbed |
| M6 | 11/94: DCJ-11, CIS, 22-bit Q-Bus map, FPJ | stubbed |
| M7 | VT100/LA36 frontends, debugger polish, diagnostic pass rates | stubbed |
| M8 | Snapshots, power-fail/restore, SIMH round-trips, perf instrumentation | stubbed |

### The M1 golden transcript (§22.2)

Hand-assembled decrement loop at octal 0, pinned exactly by
`pdp11/tests/cpu_golden.py::test_decrement_loop_golden_transcript`:

```
000000  012700  MOV #5,R0
000002  000005        (literal 5)
000004  005300  DEC R0
000006  001376  BNE 000004      (offset 0o376 = -2 words)
000010  000000  HALT
```

Execution trace (PC, opcode): `000000/012700`, then `000004/005300` +
`000006/001376` four times with the branch taken, a fifth `000004/005300` +
`000006/001376` with the branch **not** taken (Z set), then `000010/000000`
HALT. Final state: R0 = 0, PSW Z=1 N=0, machine halted; two fresh runs
produce identical traces and identical bus-cycle counts.
