# PDP-11 Instruction Reference — M1–M5 implemented set

M1 implements the **BASE** set of the PDP-11/20 (KD11-B); M3 adds EIS;
M5 adds the **MMU-only** instructions and the **FP11** group on the
11/70/94. Opcodes and flag behavior per the *PDP-11 Processor Handbook*
(instruction set reference; timing table for the `COSTS` entries in
`cpu/core.py`). All values octal (spec §10.1).

Not on the 11/20 (spec §2): MUL, DIV, ASH, ASHC, XOR, SOB, MARK, SXT,
MTPS/MFPS, SPL — these opcodes are **consumed as no-ops** on the 11/20
(capability gate, spec §5.4); on MMU machines (11/70/94) MARK/MTPS/MFPS/SPL
are real (see §"MMU-only" below). FP11 (0o17xxxx) and CIS trap as reserved on
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

## MMU-only instructions (11/70, 11/94 — no-ops on the 11/20, spec §5.4/§5.7)

| Octal | Mnemonic | Operation |
|---|---|---|
| 00023m | SPL m | PSW<7:5> (IPL) ← m |
| 0064nn | MARK nn | SP ← PC+2·nn; PC ← R5; R5 ← (SP)+ |
| 1064dd | MTPS dd | PSW<7:0> ← src byte (IPL/T/CC only) |
| 1067dd | MFPS dd | dst byte ← PSW<7:0> |
| 0065dd | MFPI dd | dst ← previous mode's **I** space at dd |
| 0066dd | MTPI dd | previous mode's **I** space at dd ← src |
| 1065dd | MTPD dd | previous mode's **D** space at dd ← src |
| 1066dd | MFPD dd | dst ← previous mode's **D** space at dd |

On MMU machines, trap/interrupt vectors are fetched from **kernel D space**,
the previous-mode field records the old mode, and R6 is banked per mode
(`switch_mode`); RTI switches mode after popping PSW and PC.

## FP11 floating-point group (0o17xxxx, 11/70/94 with FP11; reserved 000010 elsewhere)

Decode: fop = IR<11:8>, accumulator ac = IR<7:6>, operand spec = IR<5:0>.
F (FPS D=0) is 32-bit, D (FPS D=1) is 64-bit; both use an 8-bit excess-128
exponent. FP operand specifiers are length-aware (F=4 bytes, D=8; PC
immediates advance PC by one word); mode 6/7 displacements are fetched from
I space. Exceptions trap through **000244** when the FPS IE bit for the
exception is set and ID is clear; FEC/FEA record code/address (STST).

| Octal | Mnemonic | Operation |
|---|---|---|
| 170000 / 170001 / 170002 / 170011 / 170012 | CFCC / SETF / SETI / SETD / SETL | FPS↔PSW cc; set F/I/D/L modes |
| 1701dd / 1702dd / 1703dd | LDFPS / STFPS / STST | FPS ↔ operand; (FEC,FEA) long ↔ operand |
| 1704dd / 1705dd / 1706dd / 1707dd | CLRf / TSTf / ABSf / NEGf | per-precision clear/test/abs/negate |
| 1710as / 1714as / 172ass / 173ass / 1734as | MULf / MODf / ADDF / SUBF / CMPf | ac op= src (MOD: rem→AC, quot→AC+1) |
| 1724as / 174ass / 1744as | LDF / STf / DIVf | load / store / divide |
| 1750dd / 1764dd | STEXP / LDEXP | exponent ↔ integer operand |
| 1754dd / 1760dd | STCfi / STCff′ | AC → integer / other precision |
| 1770dd / 1774dd | LDCif / LDCff′ | integer / other precision → AC |

## CIS — Commercial Instruction Set (0o076xxx, caps=CIS: 11/94 on-chip J-11, 11/03+KEF11, 11/70+KH11; reserved 000010 elsewhere)

op = IR<6:0>; bit 6 (0o100) selects inline operands (descriptor ADDRESSES as
inline words fetched from I space). String descriptor = (length word, address
word). Register forms use R0:R1/R2:R3 as descriptors, R4 = fill/match char,
R5 = table address. M6 implements the string ops per SIMH pdp11_cis.c;
packed-decimal ops (0o050-0o057, 0o070-0o077, inline 0o150-0o177) raise
NotImplementedError (§22.4).

| Octal | Mnemonic | Operation |
|---|---|---|
| 0760[2-7]r | L2DR | load 2 descriptors into R0-R3 from list at Rr |
| 0761[2-7]r | L3DR | load 3 descriptors into R0-R5 |
| 076030 / 076130 | MOVC / MOVCI | move string, pad dst with fill; CC from srclen−dstlen |
| 076031 / 076131 | MOVRC / MOVRCI | move reversed to END of dst, fill front |
| 076032 / 076132 | MOVTC / MOVTCI | MOVC + translate each byte via table at R5 |
| 076040 / 076140 | LOCC / LOCCI | find char: R0:R1 <- unmatched rest (incl. match) |
| 076041 / 076141 | SKPC / SKPCI | skip char (first non-match) |
| 076042 / 076142 | SCANC / SCANCI | scan until table[c] & mask ≠ 0 |
| 076043 / 076143 | SPANC / SPANCI | span while table[c] & mask ≠ 0 |
| 076044 / 076144 | CMPC / CMPCI | compare two strings (fill-padded); NZVC from first diff, Z if equal |
| 076045 / 076145 | MATC / MATCI | count chars matching mask table |

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
| 000244 | floating-point exception (FP11, M5) |
| 000250 | MMU abort with MMR0 TENB set (M5; else 000004) |

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
