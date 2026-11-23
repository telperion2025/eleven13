# eleven13 Operator's Manual

What each machine does from power-on, how the console/panel behaves, and how
to bootstrap. Source: DESIGN.md §13 (power-on state and boot sequence) and
§9 (ODT). Numbers are octal throughout (spec §10.1).

## 1. Power-on behavior per machine

The CPU core has an explicit **power-on state** (registers cleared, PSW = 0,
memory zeroed except diagnostic RAM-test mode, PC from the profile) —
distinct from the HALT state. Assembled by the profile, never hard-coded.

| Machine | Power-on behavior |
|---|---|
| **11/03** | KD11-F starts at octal **173000** (boot ROM). Console ODT is reachable with the BREAK key / halt switch. |
| **11/20** | **No ROM at all**: starts at octal **000000** in whatever RAM the operator toggles in via the console switches (in the emulator: manual deposit via ODT, or auto-load from a script file). See §3 below for the original bin-load ritual. |
| **11/70** | Microcode-loads from the boot ROMs (M9312 secondary ROM at octal 173000; console ROM at 165xxx per the 11/70 handbook). M5: the emulator boots the built-in (non-authentic) bootstrap at 173000, reproduces the `^ address` display on halt — ODT prints `^` + PC before the `@` prompt on MMU machines — and models the MMU (off at power-on until MMR0 MME is set, as on real hardware). |
| **11/94** | Console microcode boot from the 2 KB boot PROM at octal **173000**, with the extended 4-slot state save (spec §5.4/§5.5). M6: J-11 (DCJ-11) with on-chip CIS+FPJ, MMU with I/D via MMR0 bit 3, Q22-CD 22-bit map at 0o170400; the built-in bootstrap boots and ODT shows `^ address`. (Real 11/94 hardware is Unibus — the spec's Q22-CD assignment is followed and flagged §22.1.) |

`--bootrom <file>` supplies a **512-word M9312 boot PROM image** so any of the
four profiles can run real DEC 173000-series boot code (XXDP boot, TU tape
boot, RP/RM disk boot ROMs). Without it the emulator falls back to a
simplified built-in bootstrap, clearly documented as **non-authentic**
(spec §13).

## 2. Console switch / front-panel semantics

The emulator models the front panel as a **two-state control** — `RUN` and
`HALT` external events (`pdp11/control/front_panel.py`) — never by stopping
mid-cycle or corrupting machine state (spec §13):

- **11/03, 11/94:** BREAK key transitions RUN → HALT into ODT.
- **11/70:** halt switch on the console.
- **11/20:** manual deposit on the console switches (see §3).

On HALT, control falls to **ODT** — the `@` prompt behaving like the real
M9312/KD11-F console: `/` examine, `\` deposit, `G` go, octal addresses only
(spec §9). On the 11/70/94 the console first shows the `^ address` display
(the halted PC), matching the 11/70 front-panel behavior (§13) —
`pdp11/control/front_panel.py` emits `^nnnnnn` when `profile.has_mmu`.

## 3. The 11/20 bin-load ritual (operator's writeup, spec §13)

The PDP-11/20 predates boot ROMs: at power-on the CPU starts at 000000 with
RAM clear, and the operator loaded a bootstrap **by hand on the console
switches**. The ritual, as documented for the original machine:

1. Set the console switch register to the first bootstrap word's address
   (000000) and its value; press **LOAD ADRS**, then **EXAM/DEPOSIT** to
   store each word in sequence.
2. The standard entry was a short absolute loader keyed in word by word
   (the famous "toggle-in" binary loader, typically ~10–20 words), ending
   with the start address in the switches and **START**.
3. The loader then read the real bootstrap (paper tape via PC11, or the
   RK05/RX01 boot block) into high memory and jumped to it.

In eleven13 the equivalent is: deposit the loader words through ODT
(`@` prompt, `\` deposit) or auto-load from a script file with the 11/20
profile; the emulator starts at 000000 exactly as the real machine did.
`pdp11/control/rom_loader.py` (M2/M3) additionally supports real M9312 PROM
images for the ROM-boot machines.

## 4. Boot targets by acceptance criterion (spec §11)

| Machine | Boot target | Image |
|---|---|---|
| 11/03 | RT-11 | RX01 image (§11.2) |
| 11/20 | RT-11 | RK05 image (§11.3) |
| 11/70 | RSTS/E or RSX-11M | RP06 via RH70/Massbus at 176700 (M5 driver done: `--disk rp06.dsk`), MMU across kernel/supervisor/user (§11.4); a real RSTS/RSX RP06 image awaits the operator (§22.1) |
| 11/94 | RT-11 | RX01 image (§11.2) |

Disk/tape images are read from the SIMH-compatible containers of spec §16
(RK05 raw 512-byte sectors; RX01 raw 128-byte sectors; RP06/RP07 raw with
optional `.hp` headers; SIMH `.tap` magtape; PC11 `.pt`/raw). If SIMH can read
it, eleven13 can too — that is the interop test (spec §11.10).
