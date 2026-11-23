# Acceptance Checklist — spec §11, current status

Version 1 is complete when all eleven criteria pass. Status after M3:

| # | Criterion (spec §11) | Status |
|---|---|---|
| 1 | All four profiles assemble, enter ODT, run MAINDEC CPU diagnostics to a pass/fail report | profiles assemble, boot ROM path + ODT shell **done in M2** (`test_console.py`: 11/03 built-in bootstrap prints via DL11 and HALTs to ODT; 11/20 HALTs at 000000 into ODT); MAINDEC runs land M8 (`tests/machine_diagnostics.py`) |
| 2 | RT-11 boots from an RX01 image on 11/03 and 11/94 | RX11 driver + RX01 container **done in M4**; boot path verified end-to-end on the 11/03 (`test_console.py::test_rx01_boot_sector_end_to_end`); **11/94 profile assembled and boots the built-in bootstrap in M6** (`test_console.py::test_1194_*`). A *real* RT-11 RX01 image awaits the operator (§22.1) |
| 3 | RT-11 boots from an RK05 image on 11/20 | RK11/RK05 driver + containers **done in M3**; the full boot path (I/O-page CSR writes → NPR word-at-a-time DMA → BR5/vector-220 completion interrupt) is verified end-to-end with a hand-built boot block (`test_console.py::test_rk05_boot_block_end_to_end`). Booting a *real* RT-11 image awaits an operator-supplied container (§22.1 — no image is fabricated) |
| 4 | RSTS/E or RSX-11M boots on 11/70 from an RP06 (RH70/Massbus), MMU across kernel/supervisor/user | **M5 done at the machine level**: 11/70 MMU (3-mode I/D APR sets, MMR0-3, aborts via 000250/000004), Unibus map (BME-gated DMA face), RH70+RP06/RP07 Massbus driver, FP11 — `test_mmu.py`, `test_map.py`, `test_fp11.py`, `test_rh70.py`, `test_console.py` (11/70 assembly, RP06 `--disk`, bootstrap + `^` ODT display, MMU-on program path). Booting a *real* RSTS/RSX image awaits an operator-supplied RP06 container (§22.1 — no image is fabricated) |
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
