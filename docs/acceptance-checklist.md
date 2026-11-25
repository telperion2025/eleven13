# Acceptance Checklist — spec §11, current status

Version 1 is complete when all eleven criteria pass. Status after M8:

| # | Criterion (spec §11) | Status |
|---|---|---|
| 1 | All four profiles assemble, enter ODT, run MAINDEC CPU diagnostics to a pass/fail report | profiles assemble, boot ROM path + ODT shell **done in M2**; **M7: the §11.1 harness is in place** (`tests/machine_diagnostics.py`) and reports **4/4 profiles PASS** on the built-in smoke diagnostic (pinned by its tests); real MAINDEC binaries remain operator-supplied artifacts (§22.1) that plug into the same harness |
| 2 | RT-11 boots from an RX01 image on 11/03 and 11/94 | RX11 driver + RX01 container **done in M4**; boot path verified end-to-end on the 11/03 (`test_console.py::test_rx01_boot_sector_end_to_end`); **11/94 profile assembled and boots the built-in bootstrap in M6** (`test_console.py::test_1194_*`). A *real* RT-11 RX01 image awaits the operator (§22.1) |
| 3 | RT-11 boots from an RK05 image on 11/20 | RK11/RK05 driver + containers **done in M3**; the full boot path (I/O-page CSR writes → NPR word-at-a-time DMA → BR5/vector-220 completion interrupt) is verified end-to-end with a hand-built boot block (`test_console.py::test_rk05_boot_block_end_to_end`). Booting a *real* RT-11 image awaits an operator-supplied container (§22.1 — no image is fabricated) |
| 4 | RSTS/E or RSX-11M boots on 11/70 from an RP06 (RH70/Massbus), MMU across kernel/supervisor/user | **M5 done at the machine level**: 11/70 MMU (3-mode I/D APR sets, MMR0-3, aborts via 000250/000004), Unibus map (BME-gated DMA face), RH70+RP06/RP07 Massbus driver, FP11 — `test_mmu.py`, `test_map.py`, `test_fp11.py`, `test_rh70.py`, `test_console.py` (11/70 assembly, RP06 `--disk`, bootstrap + `^` ODT display, MMU-on program path). Booting a *real* RSTS/RSX image awaits an operator-supplied RP06 container (§22.1 — no image is fabricated) |
| 5 | 11/20 no-ops microcoded-only instructions rather than executing them | **done at CPU level** — `test_1120_noops_microcoded_only_opcodes` + `test_eis_raises_notimplemented_on_eis_fitted_machine` (capability gate, spec §5.4); MAINDEC CK41 cross-check per §22.5 R1 |
| 6 | Console DL11 traffic end-to-end with the VT100 frontend on all profiles | **done in M7** — DL11 console + run loop (M2, `device_unit.py`, `test_console.py`) plus the real VT100/VT52 state machines and the LA36 hardcopy (`graphics/vt100.py`, `graphics/la36.py`), wired by `--console` in `main.py`; end-to-end banner/ODT traffic through the VT100 frontend is tested on all four profiles (`test_console.py::test_1103_bootstrap_via_default_vt100_frontend` et al., `test_vt100.py`, `test_la36.py`) |
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

### M5 reconciliations (§12 milestone 5: 11/70 MMU, Unibus map, Massbus, FP11)

13. `profiles.py` `max_ram_words` for 11/70 and 11/94 was 0o4000000; corrected
    to 0o10000000 (4 MB = the full 22-bit space below the I/O page). RAM is
    clipped at the top 8 KB I/O page for 22-bit profiles.
14. The spec's "I/D space via MMR0 bit 3" (§5.7 text) is the **J-11 (11/94)
    convention**; on the 11/70 split I/D is selected by **MMR3 bits 0-2
    (UDS/SDS/KDS)** per the 11/70 Processor Handbook / SIMH pdp11_mmu.c —
    flagged §22.4. The emulator follows the 11/70 hardware.
15. The Unibus map register window lives at **0o172400** in the emulator's
    16-bit-convention I/O page; the real 11/70 places it at Unibus 772200
    (SIMH uses 770200, aliased in the supervisor APR space). Entries are
    22-bit byte bases stored as two words (low 16 at +0, bits <21:16> at +2,
    4-byte stride), 31 usable entries; page 31 is fixed at 0o17760000+.
    Flagged §22.4 for the address discrepancy.
16. MMU aborts: vector **000250 when MMR0 TENB (bit 10) is set, else 000004**
    (SIMH VEC_MME behavior); MMR0 records NR/RO/PL + mode/space/page, MMR2
    the faulting VA. **MMR1 register-change backout is not modeled** (§22.4);
    ACF 4/5 ("watched" read-write) do not trap (§22.4). A/W bits in the PDR
    are set by hardware on access (writes preserve them).
17. The board exposes **two DMA faces**: `dma_host` (18-bit Unibus address,
    through the Unibus map only when MMR3 BME is set) and `massbus_host`
    (22-bit direct physical, `(BAE<<16)|BA`) — the RH70 does **not** use the
    Unibus map. Each moved word costs one stolen cycle (§5.6).
18. RH70/RP06 details cross-checked against SIMH pdp11_rh.c / pdp11_rp.c:
    window 176700 size 0o54 (RHCS1/RHWC/RHBA/RHCS2/RHDB/RHBAE/RHCS3, drive
    regs RPDS/RPDA/RPER1/RPDC/RPCC), CS1/CS2 bit layouts, function codes
    (NOOP/SEEK/RECAL/RELEASE/OFFSET/GET_STATUS/PACK_ACK/WRITE/WRITE_CHECK/
    READ/READ_HEADER), RPDA surface<9:5>/sector<4:0>, RP06 = 815 cyl × 19
    surf × 22 sect × 512 B (340670 sectors), RP07 = 630×32×43 (866880),
    vector 000254 at BR5. Flagged §22.4: the completion interrupt is
    modeled level-based (DONE&IE plus a CSTB INTR flop approximation) since
    the arbiter is polled, not INTA-driven; data-transfer GO while DONE
    clear sets CS2 PGE; multi-sector transfers auto-increment RPDA per
    sector; READ_HEADER completes without data; RP control function codes
    follow SIMH, not a DEC table.
19. FP11: decode per SIMH pdp11_fp.c (fop = IR<11:8>, ac = IR<7:6>,
    spec = IR<5:0>); F = 24-bit significand in the top 32 bits of a 64-bit
    accumulator word, D = 56-bit; exponent 8-bit excess-128 (bias 0o200) for
    **both** precisions (PDP-11 D is not IEEE-754). Arithmetic is exact
    (`fractions.Fraction`), rounded half-up at store unless FPS T
    (truncate); overflow exp > 0o377, underflow exp < 1. Exceptions trap
    through **000244** when the per-exception IE bit is set and FPS ID is
    clear; FEC records the code, FEA the faulting instruction address.
    Flagged §22.4: reserved-operand (FEC_OP), divide-by-zero and
    undefined-variable abort (NOP) the operation per the FP11; LDEXP range
    checks follow SIMH; FP immediates via PC mode 2 advance PC by one word
    only; in memory an FP operand is stored low-word-first per 32-bit unit.
    On FPU-less profiles the whole 0o17xxxx group traps reserved (000010)
    via the capability gate (§5.4/§5.8).
20. MMU instructions on 11/70/94 (all no-ops on the 11/20 per §5.4):
    SPL 00023m (set IPL), MARK 0064nn (SP ← PC+2·nn; PC ← R5; R5 ← (SP)+),
    MTPS/MFPS 1064dd/1067dd (byte PSW), MFPI/MTPI/MFPD/MTPD
    (0065dd/0066dd/1065dd/1066dd — move between current and previous mode's
    I/D space; vector fetches and trap pushes on MMU machines use kernel D
    space and the banked per-mode SP). RTI restores the mode via
    `switch_mode` after both pops. **Register-set (RS bit 11) banking of
    R0-R5 is stored but not switched** (§22.4).
22. M6 (11/94, DCJ-11): (a) the spec assigns the 11/94 a Q-Bus with the
    Q22-CD 22-bit map, while the real 11/94 is a Unibus machine (the 11/93
    is the Q-Bus J-11) — the spec is followed and the divergence is flagged
    §22.1. (b) Q22-CD map implemented as 16 entries × 256 KB blocks
    (block = qa<21:18>), window at 0o170400 (22-bit mirror 0o17770400),
    always in use for Q-Bus DMA (no enable bit, unlike the 11/70's MMR3
    BME); applied on `board.dma_host` for 22-bit Q-Bus profiles.
    (c) J-11 I/D split is via **MMR0 bit 3 (DSE)**, the convention spec §5.7
    names — the 11/70 keeps MMR3 UDS/SDS/KDS (§22.4); MMR0 bit 3 is writable
    only on the J-11 flavor. (d) DCJ11_STRICT now traps nonexistent memory
    AND odd-address word access cleanly through vector 000004 (no LSI-11
    byte-split); the J-11 micro-trap format differences vs the 11/70 are
    flagged §22.4. (e) CIS: character-string ops implemented per SIMH
    pdp11_cis.c (0o076xxx + inline variants: MOVC/MOVRC/MOVTC, LOCC/SKPC,
    SCANC/SPANC, CMPC, MATC, L2DR/L3DR); packed-decimal ops (ADDN…/CRC)
    raise NotImplementedError — deferred per spec §3/§22 R4, flagged §22.4;
    CIS runs to completion (no FPD restart state, §22.4). (f) FPJ is
    FP11-compatible at the ISA level — the existing 0o17xxxx handlers serve
    the 11/94 via the FPU cap (`test_console.py::test_1194_fpj_instructions`).
21. M5 review pass (PR #3): (a) a page fault while fetching the abort vector
    (double fault) now **halts the CPU** in `core.step` instead of escaping
    as an uncaught `MMUAbort` — matches the real 11/70 stop (§22.4);
    `test_mmu.py::test_double_fault_halts_cpu`. (b) RH70 multi-sector
    transfers now auto-increment RPDA with sector→surface wrap
    (`RPDrive.next_rpda`) and error NED past the cylinder end instead of
    crashing (`test_rh70.py::test_rh70_multisector_wraps_track_boundary`);
    CS2 CLR now clears the error bits too
    (`test_rh70.py::test_rh70_clr_clears_error_bits`). (c) Noted convention:
    on 22-bit profiles the flat alias 0o160000-0o200000 shadows RAM there for
    ALL physical accesses incl. Massbus — consistent with the M2 I/O-page
    convention, flagged §22.4.

### M7 reconciliations (§12 milestone 7: VT100/LA36 frontends, debugger, diagnostics)

23. VT100 wrap is **immediate** (a printable at column 80 linefeeds first);
    the real VT100 defers the wrap by one character via a pending-wrap flag.
    Flagged §22.4; visible only in a full 80-column line boundary case.
24. VT100 scope ceiling held per §8/R5: graphics character sets (ESC ( x)
    are consumed and ignored, SGR (CSI m) is consumed with no rendition,
    scroll regions (CSI r) are not modeled, and the VT52 ESC Z identify
    answer (an input byte) is flagged, not modeled. Cross-check against SIMH
    vt100.c in the §22.4 loop.
25. LA36 DECwriter: 132-column line, hardcopy overwrite cursor, CR does not
    print, LF prints the line (+CR LF), FF adds a form feed, bytes past
    column 132 are dropped with a bell. "Line pacing" is NOT a wall clock:
    byte pacing comes from the DL11 TX schedule (TX_CYCLES per byte,
    devices/dl11.py) and the DECwriter buffers whole lines — deterministic
    per §5.6/§19.6. Timing constants cross-checked against SIMH la36.c (§22.4).
26. §14 performance instrumentation is **counters only** inside
    `control/debugger.py` (PerfCounters: instructions, per-opcode-group
    counts and charged base cycles); instructions/second is computed by
    `main.py` at the run boundary because §19.6 bars `time.*` from the
    machine modules — function-level timing dumps remain M8 work (§22.4).
27. The §11.1 diagnostic harness (`tests/machine_diagnostics.py`) runs a
    built-in BASE-only smoke diagnostic on all four profiles and reports
    **pass rate 4/4 PASS** (pinned by tests in the module). Real MAINDEC
    binaries are operator-supplied (§22.1 — none fabricated) and plug into
    the same `run_diagnostic()` harness (load, run to HALT within budget,
    check the flag word); their pass rates will be recorded here per §22.2.
28. Debugger command set (§9 --debug) is line-based like ODT under the
    `dbg>` prompt: `b`/`w` breakpoints/watchpoints, `s [n]` single-step with
    disassembly, `r` registers, `m` MMU APRs+MMR0-3, `i` interrupt-pending,
    `d` device stats, `c` container inspection, `p` perf counters — plus the
    inherited ODT `/` `\` `G` `Q`. The §18.3 EmulationError crash dump
    (registers, PC, last 8 opcodes via `CPU.op_trace`, clock cycle) prints
    through the terminal and exits 1 (`main.py`, `test_debugger.py`).
29. M7 review pass (PR #5): (a) ODT/debugger octal fields that fail to parse
    now answer `?` instead of crashing on `ValueError`, and host EOF mid-deposit
    quits cleanly instead of `None.strip()` (`front_panel.py`,
    `test_debugger.py::test_garbage_octal_args_answer_question_mark`,
    `::test_deposit_eof_mid_line_quits_cleanly`); (b) the VT100 parser now
    executes C0 controls immediately inside ESC/CSI sequences and lets ESC
    abort a pending CSI, matching the DEC state machine
    (`test_vt100.py::test_c0_control_executes_inside_csi` et al.); (c) the
    debugger's hot path (`supervised_step`) no longer performs its own bus
    reads — perf counting uses `CPU.last_op_word` after the step, so
    supervised runs are cycle-identical to plain runs
    (`test_debugger.py::test_supervised_run_is_cycle_identical_to_plain_run`);
    disassembly survives a bus-erroring operand word (`???` placeholder);
    (d) `BackendTerminal`'s input queue is a `deque` (O(1) popleft); (e) the
    diagnostic harness clips RAM small instead of allocating the 22-bit
    profiles' full 4 MB ceiling.

### M8 reconciliations (§12 milestone 8: snapshots, power-fail/restore, SIMH round-trips, perf)

30. Snapshot format follows §15: JSON wrapper (`format`/`version`/`profile`/
    `created` header) around a zlib+base64 **binary core** (the JSON state
    dict). The core is deterministic — identical machine state yields
    identical core bytes; only the header carries a timestamp. The scheduler
    queue is serialized as absolute `(due, seq)` plus a callback reference
    (`{"obj": "cpu"|address, "name": method}`); device event lambdas were
    refactored to bound methods (DL11/RK11/RH70/DZ11 pending-transfer/seek
    args now live in serialized device fields), and a non-serializable
    callback fails loudly with `EmulationError` at save time (§18.3). The seq
    allocator is snapshotted too — restoring `max(seq)+1` instead of the
    clock's own `_seq` made restored runs allocate different future sequence
    numbers (found by `test_snapshot.py`, fixed in `SystemClock.load_queue`).
31. Power-fail/restore (§5.5 vector 000024): `--powerfail/--powerrestore
    [OCT_CYCLES]` schedule `CPU.power_fail_trap` on the deterministic clock —
    the trap fires between instructions with the usual pushed PC/PSW. The
    ">= 2 ms simulated remaining" grace is the cycle gap between the two
    flags (operator-chosen); documented in `traps.power_fail`.
32. SIMH `.tap` framing follows the §16 text: LE32 length + payload + LE32
    trailer (verified on read), bare zero = tape mark, final bare zero = EOF
    mark, physical EOF = EOM. A zero-length *data* record is indistinguishable
    from a tape mark (inherent to the framing) — flagged §22.4. Round-trips are
    byte-identical for everything this emulator writes (`test_simh_containers.py`);
    the SIMH cross-read of an operator-supplied real `.tap` remains §22.1.
33. M8 coverage pass for the §18.2 gate found a real M5 bug: **CLRf with a
    register operand (mode 0) never cleared the AC** — `_write_fp` returns
    early for mode 0 and the CLRf branch didn't store (`cpu/instructions/fp11.py`,
    fixed; `test_fp11_sweep.py::test_abs_neg_tst_clr_and_cfcc` pins it). The sweep
    also lifted cpu/ to 90% (bus 97%, devices 87%); `pytest-cov` joined the dev
    extras in M8 (the §18.1 "no new deps before M8" allowance) and ci.yml now
    runs the per-package coverage gates plus the determinism tests
    (`test_determinism.py`) inside `make check`.
34. First measured throughput (§14): **~225,000 instructions/s** (1,000,000
    instructions in 4.45 s = 4,000,000 bus cycles) for the 11/20 spin loop on
    the sandbox host (CPython 3.12, uv venv, no throttle), recorded by
    `test_determinism.py::test_measured_throughput`. `--maxspeed` (the default)
    is now an explicit CLI flag; `--debug` reports host-measured instr/s at exit.

### Post-M8 device/CIS pass (TM11, RL11, PC11, KW11-P, CIS packed-decimal)

35. TM11/TU10 (`devices/tm11.py`) follows SIMH pdp11_tm.c: registers
    MTS/MTC/MTBRC/MTCMA/MTD/MTRD at 172520 (the M1 stub docstring's
    MTTC/MTDS/... naming was approximate — SIMH's is authoritative), vector
    000224/BR5, byte-granular NPR stealing (DMAHost gained read_byte/
    write_byte; the Unibus supports byte transfers, §5.5). Tape commands
    UNLOAD/READ/WRITE/WREOF/SPACEF/SPACER/WREXT/REWIND per SIMH tm_go/tm_svc;
    record latency/rewind constants scaled from SIMH tm_time — flagged §22.4.
    v1 models unit 0 only. Verified end-to-end through the I/O page with a
    SIMH-framed .tap (bytes land in RAM via service_dma, DONE + BR5/224).
36. RL11 (`devices/rl11.py`) follows SIMH pdp11_rl.c: RLCS/RLBA/RLDA/RLMP/
    RLBAE at 174400, vector 000160/BR5, relative seeks, GSTA status queue,
    word-at-a-time DMA, WCHK compare, 256-byte sectors, RL01=256/RL02=512
    cylinders (rl_sector_address = (cyl*2+head)*40+sect, SIMH GET_DA). RL
    containers autosize (SIMH AUTOSIZE): any 256-aligned file up to RL02
    capacity pads to the drive (`open_rl`); they attach via `--rl`, NOT
    `--disk`, because partial RK05-sized RT-11 files would be ambiguous.
    Full RLDS error set / header CRC simplified — flagged §22.4.
37. PC11 (`devices/pc11.py`) follows SIMH pdp11_pt.c: PRS/PRB/PPS/PPB at
    177550, reader vector 000070 / punch 000074 at BR4; reader streams a
    .pt raw byte container, punch is in-memory only (§16 read-only container
    in v1 — the container is never written). Frame timing ~1000 cycles/frame
    (PC05 1000 cps at the repo quantum) — flagged §22.4.
38. KW11-P (`devices/kw11p.py`) follows SIMH pdp11_pclk.c: CSR/CSB/CNT at
    172540 (the M6 stub said "170744 region" — SIMH puts the KW11-P at
    172540; 177544/104/BR7 is the KW11-W watchdog, a different device),
    vector 000104/BR6, rate table 100kHz/10kHz/line/10Hz scaled to the
    §5.6 quantum like the KW11-L's 16 667-cycle tick — flagged §22.4.
39. CIS packed-decimal arithmetic implemented in `cpu/instructions/cis.py`
    (ADDN/SUBN/CMPN/ADDP/SUBP/CMPP/MULP/DIVP/ASHN/ASHP + inline variants;
    opcode map cross-checked against binutils pdp11-opc.c and SIMH
    pdp11_cis.c opntab). All 8 decimal data types (zoned/overpunch/separate/
    packed) read/write via (sign, big-int) conversion; overpunch tables from
    SIMH overbin/binover. CVT*/CRC remain the last loud NotImplementedError
    stub (§19.10) — see item 42's note on FIS, now implemented.
40. TQK11 (TK50/TU81 via Q-Bus 174500, 11/94) is implemented: IP/SA semantics,
    the 4-step UQSSP init handshake, and the MSCP command/response-ring
    layer, cross-checked against SIMH pdp11_tq.c + pdp11_uqssp.h +
    pdp11_mscp.h (IP read = poll ring, IP write = re-init; ring descriptors
    OWN/F/22-bit-addr; 4-byte UQ header before each packet; TMSCP CID).
    Tape opcodes RD/WR/WTM/ERG/ERS/POS run against SIMH .tap containers
    byte-compatibly (TapImage, §16); control set SCC/GUS/ONL/AVL/ABO/GCS.
    Interrupt vector is programmed in init step 1 (SA_S1H_VEC<<2), BR5.
    Pinned by pdp11/tests/test_tqk11.py (16 tests).
41. VT11-class vector display (graphics/framebuffer.py, regs at 176600,
    window 0o40) is implemented: DPC/MPR/XPR/YPR register file (VS60-only
    offsets NXM as in SIMH), the display-list engine (mode set / DNOP /
    DSTOP / JMPA control words; point, short+long vector, graphplot X/Y,
    relative point, char modes) rendering into a 1024x1024 (VR17) or
    1024x768 (VR14) 1-bit framebuffer, one display-file word per stolen
    NPR cycle; STOP interrupt at vector base 0320, BR4. Cross-checked
    against SIMH pdp11_vt.c + display/vt11.c and DEC EK-VT11-TM-001
    (bitsavers.org/pdf/dec/graphics/VT11). Attached via `--vt11`.
    Pinned by pdp11/tests/test_vt11.py (15 tests).
42. FIS (0o075xxx, KEF11) is implemented in `cpu/instructions/eis.py`
    (instr_FIS), gated on the new profile.has_fis flag (11/03 only; on
    11/70/94 and the 11/20 the opcode traps reserved, as SIMH does without
    OPT_FIS). Semantics pinned against SIMH pdp11_fp.c fis11() + binutils
    pdp11-opc.c (FADD/FSUB/FMUL/FDIV = 0o075000/0o075010/0o075020/0o075030|r,
    mask 0xfff8): IR<2:0> is a stack pointer — fsrc F-float at R[reg],
    accumulator at R[reg]+4, result stored at R[reg]+4, R[reg] pops by 4;
    R7 reads I-space; IR & 0o000740 (bits 8-5) traps 000010 after a read of
    R[reg]; arithmetic is 32-bit F via the cpu/fp11.py engine with -0
    cleaned to +0; FDIV by zero sets N=V=C=1 and traps 000244 (FEC stays 0);
    overflow/underflow set V (N too on underflow) + FEC/FEA and trap 000244
    (fis11 forces FPS = IU|IV). Pinned by pdp11/tests/test_fis.py (16 tests).
