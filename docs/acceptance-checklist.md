# Acceptance Checklist — spec §11, current status

Version 1 is complete when all eleven criteria pass. Status at the M1
submission:

| # | Criterion (spec §11) | Status |
|---|---|---|
| 1 | All four profiles assemble, enter ODT, run MAINDEC CPU diagnostics to a pass/fail report | **blocked on M2/M7** — profiles assemble and are unit-tested; ODT + MAINDEC runs land with M2 (ODT) and M8 (`tests/machine_diagnostics.py`) |
| 2 | RT-11 boots from an RX01 image on 11/03 and 11/94 | M3/M4 (RX11 driver + container) |
| 3 | RT-11 boots from an RK05 image on 11/20 | M3 (RK11/RK05 reference driver) |
| 4 | RSTS/E or RSX-11M boots on 11/70 from an RP06 (RH70/Massbus), MMU across kernel/supervisor/user | M5 |
| 5 | 11/20 no-ops microcoded-only instructions rather than executing them | **done at CPU level** — `test_1120_noops_microcoded_only_opcodes` + `test_eis_raises_notimplemented_on_eis_fitted_machine` (capability gate, spec §5.4); MAINDEC CK41 cross-check per §22.5 R1 |
| 6 | Console DL11 traffic end-to-end with the VT100 frontend on all profiles | M2/M7 |
| 7 | Timing tests: KW11-L ticks, Q-Bus DMA, Unibus NPR priority interact causally (not wall clock) | scheduler core **done** (`test_clock_is_deterministic_across_replays`, `test_clock_events_drain_during_run`); Unibus NPR/BR arbitration **done in M2** (`test_unibus.py`: NPR outranks BR, daisy-chain slot order, word-granularity cycle stealing); device-level scenarios M2–M6 |
| 8 | Mid-boot snapshot restores byte-identically and continues identically | M8 (snapshot.py; device serialize contract already in place) |
| 9 | Power-fail/restore (vector 000024) exercised with orderly shutdown + auto-restart | M8 (`--powerfail`/`--powerrestore`) |
| 10 | Every container format (incl. SIMH `.tap`) round-trips a SIMH-created image | M3/M8 (disk_images/) |
| 11 | Per-machine bus-error semantics diverge correctly (Unibus timeout vs LSI-11 tolerant vs DCJ-11 strict) | 11/20 Unibus timeout **done** (`test_bus_error_odd_word_traps_to_000004`, `test_nonexistent_memory_traps_to_000004`); LSI11_TOLERANT/DCJ11_STRICT are M4/M6 stubs by design (§19.10) |

## M1 definition of done (spec §22.2) — all met

- tree per §3 + §17 scaffolding in place; `pytest pdp11` green (30 tests,
  including the 3 §17.9 skeleton tests);
- `ruff check` zero findings; `mypy --strict` clean on the gated modules;
- 11/20 BASE instructions decode and execute on flat RAM
  (`pdp11/tests/cpu_golden.py`);
- cost table filled for every implemented opcode (`COSTS` in `cpu/core.py`,
  exercised from the first instruction);
- golden transcript: the hand-assembled decrement loop with exact (PC, opcode)
  trace and register effects — `test_decrement_loop_golden_transcript`
  (transcript reproduced in docs/developer-manual.md §6).

## Known reconciliations with the spec text (spec §22.1 — reported, not improvised)

1. §17.2's `BusErrorModel` enum named its members only in the docstring; the
   members (`UNIBUS_TIMEOUT`, `LSI11_TOLERANT`, `DCJ11_STRICT`) were added so
   the verbatim profile table works.
2. §17.3's `SystemClock.advance` yields callbacks ("callers must run events
   to completion"); §17.9's skeleton test drained the generator without
   calling them. The test loop now calls the yielded callbacks — both texts
   otherwise unchanged.
3. §17.5's `io_page.py` imported `Optional` without using it; removed so the
   ruff zero-findings gate (§18.2) holds. §17.6's semicolon-joined enum lines
   were split for the same reason (E702).
4. On the 11/20 no-op path (spec §5.4 "advance PC, no trap"), operands are
   still resolved/consumed so PC lands past the whole instruction (an
   immediate literal is fetched and skipped) — the only self-consistent
   reading for a machine with embedded addressing modes. Flagged for the
   SIMH cross-check loop (§22.4) against a MAINDEC CK41 run (§22.5 R1).
5. `pyproject.toml` adds `python_files` for the §3-fixed test module names
   (`cpu_golden.py`, `device_unit.py`, `machine_diagnostics.py`) so pytest
   collects them alongside `test_*.py`.
