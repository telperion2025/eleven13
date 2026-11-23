"""
Massbus (spec §6.1) — 11/70 only: the RH70 controller translates Unibus
program I/O into Massbus register space and drives RP04/RP06/RP07/RM03
units beneath it (the device side lives in devices/rh70_rp.py).

RH70 register window at octal 176700 (window 0o54 bytes), verified against
SIMH pdp11_rh.c (mba_rd/mba_wr/mba_mapofs) and the RH70 manual:

    Unibus offset  Register   Contents
    176700 + 000   RHCS1      control/status 1
    176702 + 002   RHWC       word count (two's complement)
    176704 + 004   RHBA       bus address <15:0>
    176710 + 010   RHCS2      control/status 2 (unit select etc.)
    176722 + 022   RHDB       data buffer
    176750 + 050   RHBAE      bus address extension <5:0> = BA<21:16>
    176752 + 052   RHCS3      control/status 3
    (drive registers interleave via the PROM map: RPDS 176706, RPDA 176724,
     RPER1 176726, RPDC 176734, RPCC 176736 — see devices/rh70_rp.py)

The full data-transfer bus address is 22-bit physical: (BAE << 16) | BA —
on the 11/70 the RH70 addresses physical memory DIRECTLY (no Unibus map;
SIMH: `if (RH11 && cpu_bme) Map_Addr(ba); else pa = ba` — RH70 is not
RH11), so Massbus DMA goes through the board's massbus_host face.

RHCS1 bits (SIMH CS1_*): <0> GO, <5:1> function, <6> IE, <7> DONE,
<9:8> UAE (mirrors BAE<1:0>), <13> MCPE, <14> TRE (transfer error),
<15> SC (special condition). FNC_XFER = 0o24: functions >= 0o24 are data
transfers.
RHCS2 bits: <2:0> unit select, <3> UAI, <4> PAT, <5> CLR (controller
clear), <6> IR, <7> OR, <13:8> errors (PGE/NEM/NED/PE/WCE/DLT/MXF/MDPE).

Massbus RP function codes (RHCS1 <5:1>; SIMH pdp11_rp.c rp_svc, control
codes flagged §22.4): 0o00 no-op, 0o10 seek, 0o12 recalibrate, 0o14 release,
0o16 offset, 0o20 get status, 0o22 pack acknowledge, 0o24 write,
0o26 write check, 0o30 read, 0o32 read header.

Interrupt: vector 000254 at BR5. The RH interrupt logic is unusual (SIMH
warning): an internal interrupt flop (CSTB INTR) is set when DONE goes
0->1 with IE set, or when DONE+IE are written together; INTA (vector
acknowledge) clears IE in CS1/CS3 and the flop. The DONE interrupt is edge
sensitive; the SC interrupt is level sensitive — modeled in rh70_rp.py.

References: PDP-11/70 Processor Handbook Massbus chapter (EK-KB11C-TM-001);
RH70/RP06 manuals; SIMH pdp11_rh.c/pdp11_rp.c (§21).
"""

from __future__ import annotations

RH70_BASE = 0o176700
RH70_WINDOW = 0o54

# controller register offsets from RH70_BASE
RHCS1 = 0o00
RHWC = 0o02
RHBA = 0o04
RHCS2 = 0o10
RHDB = 0o22
RHBAE = 0o50
RHCS3 = 0o52

# drive (Massbus external) registers reachable through the window
RPDS = 0o06    # drive status
RPDA = 0o24    # disk address (surface/sector)
RPER1 = 0o26   # error register 1
RPDC = 0o34    # desired cylinder
RPCC = 0o36    # current cylinder

# -- RHCS1 bits ---------------------------------------------------------------
CS1_GO = 0o000001
CS1_FNC_MASK = 0o000076
CS1_FNC_SHIFT = 1
CS1_IE = 0o000100
CS1_DONE = 0o000200
CS1_UAE_MASK = 0o001400      # <9:8>
CS1_UAE_SHIFT = 8
CS1_MCPE = 0o020000
CS1_TRE = 0o040000
CS1_SC = 0o100000
FNC_XFER = 0o24          # functions >= this are data transfers

# -- RHCS2 bits -----------------------------------------------------------------
CS2_UNIT_MASK = 0o000007
CS2_UAI = 0o000010
CS2_PAT = 0o000020
CS2_CLR = 0o000040
CS2_IR = 0o000100
CS2_OR = 0o000200
CS2_MDPE = 0o000400
CS2_MXF = 0o001000
CS2_PGE = 0o002000
CS2_NEM = 0o004000
CS2_NED = 0o010000
CS2_PE = 0o020000
CS2_WCE = 0o040000
CS2_DLT = 0o100000
CS2_RW = CS2_UNIT_MASK | CS2_UAI | CS2_PAT | CS2_MXF | CS2_PE
CS2_ERR = (CS2_MDPE | CS2_MXF | CS2_PGE | CS2_NEM | CS2_NED | CS2_PE | CS2_WCE | CS2_DLT)

# -- RHCS3 bits -------------------------------------------------------------------
CS3_IE = CS1_IE
CS3_ERR = 0o172000       # APE/DPO/DPE/WCO/WCE
CS3_RW = CS1_IE | 0o17

# -- Massbus RP function codes (RHCS1 <5:1>) ---------------------------------------
FNC_NOOP = 0o00
FNC_SEEK = 0o10
FNC_RECAL = 0o12
FNC_RELEASE = 0o14
FNC_OFFSET = 0o16
FNC_GET_STATUS = 0o20
FNC_PACK_ACK = 0o22
FNC_WRITE = 0o24
FNC_WRITE_CHECK = 0o26
FNC_READ = 0o30
FNC_READ_HEADER = 0o32

VECTOR_RP = 0o000254     # RH70/RP interrupt vector
BR_LEVEL = 5             # BR5

# -- RPDA (disk address) fields ------------------------------------------------------
RPDA_SURF_SHIFT = 5
RPDA_SURF_MASK = 0o37
RPDA_SECT_MASK = 0o37

# -- RPDS bits (simplified set, flagged §22.4) -----------------------------------------
DS_ATA = 0o100000        # attention active
DS_DREADY = 0o000200     # drive ready
DS_DT = 0o040000         # drive type... (simplified)

# Seek timing model (§5.6, deterministic bus cycles; SIMH rp_swait ~ ms scale)
SEEK_BASE_CYCLES = 0o11610     # 5000
SEEK_PER_CYL_CYCLES = 0o1440   # 800

# Unibus-offset -> internal register PROM map (SIMH mba_mapofs): Unibus word
# offset (pa-base)>>1 maps to INT (controller) or EXT (drive) register.
MBA_MAPOFS = (
    0, 1, 2, 0o105, 3, 0o101, 0o102, 0o104,
    0o107, 4, 0o103, 0o106, 0o110, 0o111, 0o112, 0o113,
    0o114, 0o115, 0o116, 0o117, 0o120, 0o121, 0o122, 0o123,
    0o124, 0o125, 0o126, 0o127,
)
EXT_FLAG = 0o100


def mba_reg_of(unibus_offset: int) -> int:
    """Map a Unibus byte offset within the RH70 window to the internal
    register number (low 5 bits) with the EXT flag (0o100) for drive
    registers (SIMH mba_mapofs)."""
    return MBA_MAPOFS[(unibus_offset >> 1) & 0o37]
