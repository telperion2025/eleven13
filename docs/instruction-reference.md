# PDP-11 Instruction Reference — M1 implemented set

What milestone M1 implements: the **BASE** set of the PDP-11/20 (KD11-B) —
everything present on every PDP-11, including the original 11/20 — on flat
16-bit memory. Opcodes and flag behavior per the *PDP-11 Processor Handbook*
(instruction set reference; timing table for the `COSTS` entries in
`cpu/core.py`). All values octal (spec §10.1).

Not on the 11/20 (spec §2): MUL, DIV, ASH, ASHC, XOR, SOB, MARK, SXT,
MTPS/MFPS, SPL — these opcodes are **consumed as no-ops** on the 11/20
(capability gate, spec §5.4) and are stubs (`NotImplementedError`) on
capable machines until M4–M6. FP11 (0o17xxxx) and CIS trap as reserved on
machines without the FPU/CIS (spec §5.8).

## Double-operand (bits 15-12 opcode; byte form = bit 15 set)

| Octal | Mnemonic | Operation | Flags |
|---|---|---|---|
| 01ssdd / 11ssdd | MOV / MOVB | dst ← src | N,Z; V=0 |
| 02ssdd / 12ssdd | CMP / CMPB | flags as dst−src, no write | N,Z,V,C |
| 03ssdd / 13ssdd | BIT / BITB | flags as dst AND src | N,Z; V=0 |
| 04ssdd / 14ssdd | BIC / BICB | dst ← dst AND NOT src | N,Z; V=0 |
| 05ssdd / 15ssdd | BIS / BISB | dst ← dst OR src | N,Z; V=0 |
| 06ssdd | ADD | dst ← dst+src (mod 0o200000) | N,Z,V,C |
| 16ssdd | SUB | dst ← dst−src | N,Z,V,C (C=borrow) |

## Single-operand (bits 15-6 opcode; byte form 0o105x–0o106x)

| Octal | Mnemonic | Operation | Flags |
|---|---|---|---|
| 0050dd / 1050dd | CLR / CLRB | dst ← 0 | Z=1, N=V=C=0 |
| 0051dd | COM / COMB | dst ← ~dst | N,Z; V=0; C=1 |
| 0052dd | INC / INCB | dst ← dst+1 | N,Z; V=(was 0o077777) |
| 0053dd | DEC / DECB | dst ← dst−1 | N,Z; V=(was 0o100000) |
| 0054dd | NEG / NEGB | dst ← −dst | N,Z; V=(was 0o100000); C=(was≠0) |
| 0055dd | ADC / ADCB | dst ← dst+C | N,Z,V,C |
| 0056dd | SBC / SBCB | dst ← dst−C | N,Z,V,C |
| 0057dd | TST / TSTB | flags from dst | N,Z; V=C=0 |
| 0060dd | ROR / RORB | rotate right through C | N,Z; V=0; C=bit0 |
| 0061dd | ROL / ROLB | rotate left through C | N,Z; V=0; C=bit15 |
| 0062dd | ASR / ASRB | arithmetic shift right | N,Z; V=0; C=bit0 |
| 0063dd | ASL / ASLB | shift left, 0 into bit 0 | N,Z; C=bit15; V=N⊕C |
| 0003dd | SWAB | swap bytes | N,Z from new low byte; V=C=0 |

## Branches (bits 15-8 condition, bits 7-0 signed word offset from post-fetch PC)

| Octal | Mnemonic | Condition |
|---|---|---|
| 000400 | BR | always |
| 001000 / 001400 | BNE / BEQ | Z=0 / Z=1 |
| 002000 / 002400 | BGE / BLT | N=V / N≠V |
| 003000 / 003400 | BGT / BLE | ¬Z∧N=V / Z∨N≠V |
| 100000 / 100400 | BPL / BMI | N=0 / N=1 |
| 101000 / 101400 | BHI / BLOS | ¬C∧¬Z / C∨Z |
| 102000 / 102400 | BVC / BVS | V=0 / V=1 |
| 103000 / 103400 | BCC / BCS | C=0 / C=1 |

## Jump / subroutine / traps

| Octal | Mnemonic | Notes |
|---|---|---|
| 0001dd | JMP | PC ← EA(dst); mode 0 → reserved 000010 |
| 004Rdd | JSR | push old R; R ← PC (after operand words); PC ← EA(dst) |
| 00020r | RTS | PC ← R; R ← (SP)+ |
| 104000–104377 | EMT | trap through 000030 |
| 104400–104777 | TRAP | trap through 000034 |

## Control (group 0)

| Octal | Mnemonic | Notes |
|---|---|---|
| 000000 | HALT | park CPU; ODT owns the machine (M2+) |
| 000001 | WAIT | park until an interrupt outranks the PSW IPL |
| 000002 / 000006 | RTI / RTT | PSW ← (SP)+; PC ← (SP)+ |
| 000003 | BPT | trap through 000014 (shared with T-bit) |
| 000004 | IOT | trap through 000020 |
| 000005 | RESET | bus init: reset all devices |
| 000240 | NOP | — |
| 000241–000257 | CLC/CLV/CLZ/CLN/CCC | clear C/V/Z/N/all (bits 3-0 select) |
| 000261–000277 | SEC/SEV/SEZ/SEN/SCC | set C/V/Z/N/all |

## Standard vectors (spec §5.5)

| Octal | Meaning |
|---|---|
| 000004 | bus error / timeout |
| 000010 | illegal / reserved instruction |
| 000014 | T-bit trace trap (BPT shares it) |
| 000020 | IOT |
| 000024 | power fail / power restore |
| 000030 | EMT |
| 000034 | TRAP |

## Address modes (spec §5.2; all covered by golden tests)

| Mode | Name | Notes |
|---|---|---|
| 0 | Register | operand in Rn (byte ops use low byte) |
| 1 | (Rn) | register-deferred |
| 2 | (Rn)+ | autoincrement; byte +1 except PC (immediate literal, +2) |
| 3 | @(Rn)+ | autoinc-deferred; PC = absolute address |
| 4 | −(Rn) | autodecrement (push on SP) |
| 5 | @−(Rn) | autodec-deferred (pop on SP) |
| 6 | X(Rn) | index, signed word displacement; PC = relative |
| 7 | @X(Rn) | index-deferred; PC = relative-deferred |

PSW is memory-mapped at 177776 (low byte 177777: IPL/T/condition codes;
high byte: modes — read 0 and writes ignored without an MMU).
