# Acceptance Checklist — spec §11, current status

Version 1 is complete when all eleven criteria pass. Status after M3:

| # | Criterion (spec §11) | Status |
|---|---|---|
| 1 | All four profiles assemble, enter ODT, run MAINDEC CPU diagnostics to a pass/fail report | profiles assemble, boot ROM path + ODT shell **done in M2** (`test_console.py`: 11/03 built-in bootstrap prints via DL11 and HALTs to ODT; 11/20 HALTs at 000000 into ODT); MAINDEC runs land M8 (`tests/machine_diagnostics.py`) |
| 2 | RT-11 boots from an RX01 image on 11/03 and 11/94 | RX11 driver + RX01 container **done in M4**; the boot path (ready poll → READ via RXDB/TR handshake → BR5/264 interrupt → EMPTY 128 bytes to RAM) is verified end-to-end on the 11/03 (`test_console.py::test_rx01_boot_sector_end_to_end`). A *real* RT-11 RX01 image awaits the operator (§22.1); 11/94 profile lands M6 |
| 3 | RT-11 boots from an RK05 image on 11/20 | RK11/RK05 driver + containers **done in M3**; the full boot path (I/O-page CSR writes → NPR word-at-a-time DMA → BR5/vector-220 completion interrupt) is verified end-to-end with a hand-built boot block (`test_console.py::test_rk05_boot_block_end_to_end`). Booting a *real* RT-11 image awaits an operator-supplied container (§22.1 — no image is fabricated) |
| 4 | RSTS/E or RSX-11M boots on 11/70 from an RP06 (RH70/Massbus), MMU across kernel/supervisor/user | M5 |
| 5 | 11/20 no-ops microcoded-only instructions rather than executing them | **done at CPU level** — `test_1120_noops_microcoded_only_opcodes` + `test_eis_raises_notimplemented_on_eis_fitted_machine` (capability gate, spec §5.4); MAINDEC CK41 cross-check per §22.5 R1 |
| 6 | Console DL11 traffic end-to-end with the VT100 frontend on all profiles | DL11 console + run loop **done in M2** (`device_unit.py`, `test_console.py`); VT100 state machine M7 |
| 7 | Timing tests: KW11-L ticks, Q-Bus DMA, Unibus NPR priority interact causally (not wall clock) | scheduler core **done** (`test_clock_is_deterministic_across_replays`, `test_clock_events_drain_during_run`); Unibus NPR/BR arbitration **done in M2** (`test_unibus.py`); KW11-L 16 667-cycle tick + vector 000104/BR6 and RK11 seek/transfer scheduling **done in M3** (`device_unit.py`: `test_kw11l_*`, `test_rk11_seek_*`, `test_rk11_read_sector_via_npr_cycle_stealing`); Q-Bus DMA M4 |
| 8 | Mid-boot snapshot restores byte-identically and continues identically | M8 (snapshot.py; device serialize contract already in place) |
| 9 | Power-fail/restore (vector 000024) exercised with orderly shutdown + auto-restart | M8 (`--powerfail`/`--powerrestore`) |
| 10 | Every container format (incl. SIMH `.tap`) round-trips a SIMH-created image | RK05/RX01 raw containers round-trip byte-exactly **done in M3** (`test_containers.py`, incl. format sniffing by file size); `.tap` magtape and `.hp` headers land M8 with tm11/rh70, and the SIMH cross-read test is the M8 gate |
| 11 | Per-machine bus-error semantics diverge correctly (Unibus timeout vs LSI-11 tolerant vs DCJ-11 strict) | 11/20 Unibus timeout **done**; 11/03 LSI11_TOLERANT **done in M4**: odd-address word access splits into two byte transfers (no trap), nonexistent memory traps cleanly through vector 000004 (`test_qbus.py`) — the backplane-dependent nonexistent-memory variants are flagged §22.1; DCJ11_STRICT remains an M6 stub by design (§19.10) |

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
6. RK11/RK05 hardware details the spec names but does not bit-pin
   (§10.2 lists the registers only) follow the PDP-11 Peripherals Handbook
   best recollection and are **flagged for the SIMH cross-check (§22.4)**:
   RKCS function codes 0/1/2/3/4/5/6/7 = reset/pack-ack/seek/release/read/
   read-header/write/write-check; RKDA layout drive <15:13>, cylinder
   <12:5>, surface <4>, sector <3:0>; RKCS GO reads back 0; interrupt is
   level-based while DONE & IE at vector 000220/BR5; the simplified
   RKDS/RKER bit sets (`RKDS_DRY`, `RKER_ERROR/NO_PACK/BAD_ADDR`); seek
   time = 5000 + 800·Δcyl bus cycles; writes are read-modify-write per
   sector; write-check is modeled as write (verify pass not modeled);
   v1 models drive unit 0 only. If SIMH disagrees on any bit, the driver
   docstring and this list are the single place to change.
7. §16 states "4879 sectors total (2 surfaces × 203 cylinders × 12
   sectors)" — 2×203×12 = 4872, so the container keeps the spec's 4879
   sectors (4872 geometry-addressable + spare sectors); geometry
   addressing covers 0..4871 (`rk05_sector_address(202,1,11) == 4871`).
8. §11.3 "RT-11 boots from an RK05 image on the 11/20 profile" needs a real
   RT-11 container, which is not fabricated here (§22.1): the milestone's
   boot *path* is proven with a hand-built boot block; supplying the image
   is with the operator (Andrew, who has SIMH images).
9. M4 added `"dz11"` to the 11/03 profile's `default_devices` (§17.2 table
   lists it only implicitly via §7 P1 "03, 70, 94" + §12 M4) — recorded
   here as a deliberate, documented extension of the starter profile.
10. DZ11/RX11 register bit layouts are cross-checked against SIMH
    (pdp11_dz.c, pdp11_rx.c) rather than guessed: DZ11 at 160100 with
    CSR/RBUF-LPR/TCR/MSR-TDR, RX vector 000300 / TX 000304, BR5 on Unibus
    and BIRQ4 on Q-Bus; RX11 at 177170 with RXCS/RXDB, functions in bits
    3-1 (FILL/EMPTY/WRITE/READ/RXES/WRDEL/ECODE), DONE bit 5, TR bit 7,
    vector 000264 at BR5, RX01 sectors numbered 1-26. Remaining flagged
    corners (§22.4): DZ11 silo-alarm edge behavior, modem status bits,
    RX11 timing constants (SIMH's cwait/swait/xwait in bus cycles).
11. EIS condition codes (MUL/DIV/ASH/ASHC/XOR/SOB) follow the Processor
    Handbook as cross-checked against SIMH pdp11_cpu.c: MUL C = signed
    product outside 16 bits; DIV V on div-by-zero / -2^31÷-1 / quotient
    overflow, C only on div-by-zero, pair unchanged on error; ASH V =
    shifted-out bits differ from result sign, right shifts never overflow;
    register pairs hold the HIGH word in the lower register, odd register
    stores low word only. PRNG-seeded cross-check in `test_eis.py`.
12. LSI11_TOLERANT (§5.5) is implemented as: odd-address word access →
    split byte pair; nonexistent memory → clean Q-Bus timeout (vector
    000004). The spec's "depends on backplane configuration" variants are
    NOT settled by the spec text — flagged §22.1 for Andrew/SIMH.
