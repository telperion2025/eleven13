"""
MMU — 11/70 and 11/94 only (spec §5.7): PAR/PDR pairs (8 APRs per mode,
3 modes: kernel/supervisor/user; separate I and D space sets on the 11/70),
MMR0-3 diagnostic registers, I/D space separation, and 22-bit physical
addressing. On the 11/70 the Unibus Map (memory/map_registers.py) provides
22-bit addressing for Unibus DMA devices; the CPU's MMU translates virtual
to 22-bit physical directly.

Register map (16-bit I/O-page convention used by this emulator, verified
against SIMH pdp11_defs.h/pdp11_cpumod.c and the 11/70 Processor Handbook;
in 18-bit Unibus terms add 0o600000):
    SIPDR  172200-172216   SDPDR 172220-172236   SIPAR 172240-172256   SDPAR 172260-172276
    KIPDR  172300-172316   KDPDR 172320-172336   KIPAR 172340-172356   KDPAR 172360-172376
    UIPDR  177600-177616   UDPDR 177620-177636   UIPAR 177640-177656   UDPAR 177660-177676
    MMR0   177572  MMR1 177574  MMR2 177576      MMR3  172516
    Unibus map window: 0o172400-0o172477 in this emulator (real 11/70: Unibus
    772200-772276, which aliases the supervisor APRs in 18-bit space; SIMH
    uses 770200 — see docs/acceptance-checklist.md).

MMR0 bits (SIMH pdp11_defs.h): <0> MME memory-mgt enable (the MMU is OFF
until set — flat addressing), <6:1> page number of the aborting access,
<8> IC instruction complete, <10> TENB trap enable, <12> TRAP mgt trap,
<13> RO read-only error, <14> PL page-length error, <15> NR no-access error,
<15:6> frozen while an error bit is set.
MMR3 bits: <0> UDS user D-space, <1> SDS supervisor D-space, <2> KDS kernel
D-space (the 11/70's per-mode separate I/D enables — the spec's "MMR0 bit 3"
is the J-11 (11/94) convention; on the 11/70 it lives in MMR3, flagged
§22.4), <4> M22E 22-bit enable, <5> BME DMA bus-map (Unibus map) enable.

PAR: 16-bit page base in units of 0o100 bytes — physical page base =
(PAR & 0o177777) << 6 on a 22-bit machine (PAR_22B).
PDR bits (SIMH): <2:0> ACF (0/3/7 = no access abort; 1/2 = read-only, write
aborts; 4/5 = read/write "watched" — the trap-on-access feature is not
modeled, §22.4; 6 = read/write), <3> ED expansion direction (0 = up,
1 = down/stack), <6> W written, <7> A accessed, <12:8> PLF page length,
<15> NOC don't-cache (KDJ11 only, stored).

Abort: MMR0 gets the error bit + mode (<7:6>) + I/D (<5>) + page (<4:1>),
MMR1 = 0 (register-change backout not modeled, §22.4), MMR2 = faulting
virtual address; the board routes MMUAbort to vector 000250 when MMR0 TENB
is set, else vector 000004 (SIMH VEC_MME=0250, §22.4).

Instruction fetches always use the I-space map of the current mode and never
touch the D-space map (§5.7); with a mode's D-space disabled (MMR3), data
accesses use that mode's I-space APRs. Trap/interrupt vectors are fetched
from kernel D space.

References: PDP-11/70 Processor Handbook (EK-KB11C-TM-001, MMU + Unibus Map
chapters); SIMH pdp11_defs.h/pdp11_cpu.c/pdp11_cpumod.c (§21).
"""

from __future__ import annotations

from typing import Any

from pdp11.bus.abstract_bus import BusDevice, BusInit

WORD_MASK = 0o177777

# -- modes (PSW current/previous mode encoding, SIMH MD_*) -------------------
MD_KER = 0
MD_SUP = 1
MD_USR = 3

# -- address spaces -------------------------------------------------------------
SPACE_I = 0
SPACE_D = 1

# -- MMR0 bits ------------------------------------------------------------------
MMR0_MME = 0o000001      # <0>  memory management enable
MMR0_DSE = 0o000010      # <3>  D-space enable (J-11/11/94 convention, §5.7:
                         # the 11/70 instead uses MMR3 UDS/SDS/KDS — §22.4)
MMR0_PAGE = 0o000176     # <6:1> page number of the aborting access
MMR0_IC = 0o000200       # <8>  instruction complete
MMR0_MAINT = 0o000400    # <9>  maintenance
MMR0_TENB = 0o001000     # <10> trap enable (abort -> vector 250)
MMR0_TRAP = 0o010000     # <12> memory management trap pending
MMR0_RO = 0o020000       # <13> read-only error
MMR0_PL = 0o040000       # <14> page length error
MMR0_NR = 0o100000       # <15> no access (non-resident) error
MMR0_FREEZE = 0o160000   # error bits set: MMR0<15:6> frozen
MMR0_WR = 0o171401       # writable bits

# -- MMR3 bits --------------------------------------------------------------------
MMR3_UDS = 0o001         # user D-space enable
MMR3_SDS = 0o002         # supervisor D-space enable
MMR3_KDS = 0o004         # kernel D-space enable
MMR3_CSM = 0o010         # CSM enable (11/44+, not modeled)
MMR3_M22E = 0o020        # 22-bit memory management enable
MMR3_BME = 0o040         # DMA bus map (Unibus map) enable

# -- PDR bits ---------------------------------------------------------------------
PDR_ACF = 0o000007       # <2:0> access control field
PDR_ED = 0o000010        # <3>   expansion direction (1 = down)
PDR_W = 0o000100         # <6>   written
PDR_A = 0o000200         # <7>   accessed
PDR_PLF = 0o077400       # <12:8> page length field
PDR_NOC = 0o100000       # <15>  don't cache
PDR_WR = 0o177417        # writable bits (ACF|ED|PLF|NOC)

PAR_22B = 0o177777       # 22-bit machines: PAR holds PA<21:6>

# -- APR window addresses (16-bit convention) -------------------------------------
APR_BLOCKS: tuple[tuple[int, int, int, bool], ...] = (
    # (base, space, mode, is_par)
    (0o172200, SPACE_I, MD_SUP, False), (0o172220, SPACE_D, MD_SUP, False),
    (0o172240, SPACE_I, MD_SUP, True), (0o172260, SPACE_D, MD_SUP, True),
    (0o172300, SPACE_I, MD_KER, False), (0o172320, SPACE_D, MD_KER, False),
    (0o172340, SPACE_I, MD_KER, True), (0o172360, SPACE_D, MD_KER, True),
    (0o177600, SPACE_I, MD_USR, False), (0o177620, SPACE_D, MD_USR, False),
    (0o177640, SPACE_I, MD_USR, True), (0o177660, SPACE_D, MD_USR, True),
)
MMR012_BASE = 0o177572
MMR3_BASE = 0o172516
MAP_WINDOW_BASE = 0o172400   # see module docstring (real 11/70: Unibus 772200)

# ACF values (11/70 Processor Handbook PDR table)
ACF_NO_ACCESS = (0, 3, 7)   # abort all accesses (NR)
ACF_READ_ONLY = (1, 2)       # abort on write (RO)
ACF_READ_WRITE = (4, 5, 6)   # 4/5 are "watched" variants (traps not modeled, §22.4)

# Abort reason bits folded into MMR0
ABORT_NR = MMR0_NR
ABORT_RO = MMR0_RO
ABORT_PL = MMR0_PL

VECTOR_MMU_TRAP = 0o250      # SIMH VEC_MME; used when MMR0 TENB set (§22.4)


class MMUAbort(BusInit):
    """A page fault from MMU.translate. Subclasses BusInit so the CPU's
    existing bus-error path catches it; `vector` selects 000250 (MMR0 TENB)
    vs 000004 (bus error)."""

    def __init__(self, reason: int, va: int, page: int, mode: int, is_data: bool,
                 tenb: bool) -> None:
        self.reason = reason
        self.va = va
        self.page = page
        self.mode = mode
        self.is_data = is_data
        self.vector = VECTOR_MMU_TRAP if tenb else 0o000004
        kind = {ABORT_NR: "no access (non-resident)", ABORT_RO: "read-only",
                ABORT_PL: "page length"}[reason]
        super().__init__(f"MMU abort ({kind}) at virtual octal {oct(va)}")


class PDR:
    """Page Descriptor Register with the spec-required bitfield properties
    (§5.7: PLF, ACF, ED exposed; A/W tracking included)."""

    def __init__(self, value: int = 0) -> None:
        self.value = value & WORD_MASK

    @property
    def acf(self) -> int:
        """Access control field <2:0>."""
        return self.value & PDR_ACF

    @property
    def ed(self) -> bool:
        """Expansion direction <3>: False = expand up, True = expand down."""
        return bool(self.value & PDR_ED)

    @property
    def plf(self) -> int:
        """Page length field <12:8> (page length in 0o400-byte units, minus 1)."""
        return (self.value & PDR_PLF) >> 8

    @property
    def a(self) -> bool:
        """Accessed flag <7> — set by hardware on any access."""
        return bool(self.value & PDR_A)

    @property
    def w(self) -> bool:
        """Written flag <6> — set by hardware on a write."""
        return bool(self.value & PDR_W)

    @property
    def noc(self) -> bool:
        """Don't-cache <15> (KDJ11-class only; stored)."""
        return bool(self.value & PDR_NOC)


class PAR:
    """Page Address Register — physical page base in 0o100-byte units."""

    def __init__(self, value: int = 0) -> None:
        self.value = value & PAR_22B

    @property
    def base(self) -> int:
        """Physical byte base of the page: (PAR & 0o177777) << 6."""
        return (self.value & PAR_22B) << 6


class MMU:
    """The 11/70/94 memory management unit: 6 APR sets (I/D x 3 modes x 8
    pages), MMR0-3, and virtual->physical translation with aborts.

    `dspace_mmr0` selects the J-11 (11/94) I/D convention — D-space enabled
    globally by MMR0 <3> — instead of the 11/70's per-mode MMR3 bits (§5.7,
    profile-selected per §19.1)."""

    def __init__(self, dspace_mmr0: bool = False) -> None:
        self._dspace_mmr0 = dspace_mmr0
        # par[space][mode][page] / pdr[space][mode][page]; mode indexed 0..3
        # (mode 2 unused — PSW encoding 0/1/3)
        self.par: list[list[list[int]]] = [
            [[0] * 8 for _ in range(4)] for _ in range(2)]
        self.pdr: list[list[list[int]]] = [
            [[0] * 8 for _ in range(4)] for _ in range(2)]
        self.mmr0 = 0
        self.mmr1 = 0
        self.mmr2 = 0
        self.mmr3 = 0

    # -- control -------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """MMR0 <0> MME: the MMU translates only when set; otherwise flat."""
        return bool(self.mmr0 & MMR0_MME)

    def dspace_enabled(self, mode: int) -> bool:
        """D-space enable: J-11 = MMR0 <3> DSE globally; 11/70 = MMR3
        per-mode UDS/SDS/KDS bits (§5.7, §22.4)."""
        if self._dspace_mmr0:
            return bool(self.mmr0 & MMR0_DSE)
        bit = {MD_KER: MMR3_KDS, MD_SUP: MMR3_SDS, MD_USR: MMR3_UDS}.get(mode, 0)
        return bool(self.mmr3 & bit)

    def space_for(self, mode: int, is_instruction: bool) -> int:
        """Which APR set a reference uses: instruction fetches are always
        I-space (§5.7); data uses D-space only when enabled for the mode."""
        if is_instruction or not self.dspace_enabled(mode):
            return SPACE_I
        return SPACE_D

    # -- translation -----------------------------------------------------------
    def translate(self, va: int, mode: int, *, is_instruction: bool = False,
                  is_write: bool = False, phys_bits: int = 22) -> int:
        """Translate a 16-bit virtual address to a physical byte address.

        Raises MMUAbort (a BusInit) on an access violation; when the MMU is
        disabled (MMR0 MME clear) the address passes through flat, masked
        to the profile's physical width.
        """
        va &= WORD_MASK
        if not self.enabled:
            return va & ((1 << phys_bits) - 1)
        space = self.space_for(mode, is_instruction)
        page = (va >> 13) & 0o7                 # VA<15:13> = APR index
        off = va & 0o17777                      # VA<12:0> = displacement in page
        par = self.par[space][mode][page]
        pdr = self.pdr[space][mode][page]
        acf = pdr & PDR_ACF
        if acf in ACF_NO_ACCESS:
            self._abort(ABORT_NR, va, page, mode, space)
        if is_write and acf in ACF_READ_ONLY:
            self._abort(ABORT_RO, va, page, mode, space)
        plf = (pdr & PDR_PLF) >> 8
        if not (pdr & PDR_ED):                  # expand up from page base
            limit = ((plf + 1) << 8) - 1
            if off > limit:
                self._abort(ABORT_PL, va, page, mode, space)
        else:                                   # expand down (stack page)
            limit = 0o20000 - ((plf + 1) << 8)
            if off < limit:
                self._abort(ABORT_PL, va, page, mode, space)
        # hardware A/W tracking
        pdr |= PDR_A
        if is_write:
            pdr |= PDR_W
        self.pdr[space][mode][page] = pdr
        pa = ((par & PAR_22B) << 6 | off) & ((1 << phys_bits) - 1)
        return pa

    def _abort(self, reason: int, va: int, page: int, mode: int, space: int) -> None:
        """Record the abort in MMR0/1/2 and raise MMUAbort (§5.5/§5.7)."""
        if not (self.mmr0 & MMR0_FREEZE):
            self.mmr0 = (self.mmr0 & ~(MMR0_PAGE | 0o300)) | reason \
                | (mode << 6) | (space << 5) | (page << 1)
        self.mmr1 = 0          # register-change backout not modeled (§22.4)
        self.mmr2 = va         # faulting virtual address
        raise MMUAbort(reason, va, page, mode, space == SPACE_D,
                       bool(self.mmr0 & MMR0_TENB))


    # -- snapshot contract (§15) -------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        """MMR0-3 + all six APR sets (par/pdr), JSON-safe."""
        return {"mmr0": self.mmr0, "mmr1": self.mmr1, "mmr2": self.mmr2,
                "mmr3": self.mmr3, "par": self.par, "pdr": self.pdr}

    def deserialize(self, state: dict[str, Any]) -> None:
        """Restore MMU state; validates the schema keys (§15 contract)."""
        for key in ("mmr0", "mmr1", "mmr2", "mmr3", "par", "pdr"):
            if key not in state:
                raise ValueError(f"MMU snapshot missing {key!r}")
        self.mmr0, self.mmr1, self.mmr2, self.mmr3 = (state["mmr0"], state["mmr1"],
                                                     state["mmr2"], state["mmr3"])
        self.par = state["par"]
        self.pdr = state["pdr"]


class _MMUBase(BusDevice):
    """Common byte/word plumbing for MMU register windows."""

    def __init__(self, mmu: MMU) -> None:
        self._mmu = mmu

    def read_byte(self, offset: int) -> int:
        word = self.read_word(offset & 0o177776)
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        cur = self.read_word(reg)
        if offset % 2 == 0:
            self.write_word(reg, (cur & 0o177400) | (value & 0o377))
        else:
            self.write_word(reg, (cur & 0o000377) | ((value & 0o377) << 8))

    def serialize(self) -> dict[str, Any]:
        return self._mmu.serialize()      # §15 contract lives on the MMU itself

    def deserialize(self, state: dict[str, Any]) -> None:
        self._mmu.deserialize(state)


class MMR012Window(_MMUBase):
    """MMR0-2 at octal 177572-177576 (MMR0 writable bits per MMR0_WR)."""

    window_size = 0o6
    name = "MMR0-2"

    def read_word(self, offset: int) -> int:
        m = self._mmu
        reg = offset & 0o177776          # window-relative: 0=MMR0 2=MMR1 4=MMR2
        if reg == 0:
            return m.mmr0 & 0o177777
        if reg == 2:
            return m.mmr1 & 0o177777
        return m.mmr2 & 0o177777

    def write_word(self, offset: int, value: int) -> None:
        m = self._mmu
        reg = offset & 0o177776
        v = value & 0o177777
        if reg == 0:
            # J-11 flavor: MMR0 <3> DSE is writable too (§5.7, §22.4)
            wr = MMR0_WR | (MMR0_DSE if m._dspace_mmr0 else 0)
            m.mmr0 = (m.mmr0 & ~wr & 0o177777) | (v & wr)
        elif reg == 2:
            m.mmr1 = v
        else:
            m.mmr2 = v


class MMR3Window(_MMUBase):
    """MMR3 at octal 172516 (11/70 D-space/map enables, §5.7)."""

    window_size = 0o2
    name = "MMR3"

    def read_word(self, offset: int) -> int:
        return self._mmu.mmr3 & 0o177777

    def write_word(self, offset: int, value: int) -> None:
        self._mmu.mmr3 = value & 0o177777


class APRWindow(_MMUBase):
    """One 8-register APR block (PAR or PDR set for one mode/space)."""

    window_size = 0o20
    name = "APR"

    def __init__(self, mmu: MMU, space: int, mode: int, is_par: bool,
                 base: int) -> None:
        super().__init__(mmu)
        self._space = space
        self._mode = mode
        self._is_par = is_par
        self.base = base
        mode_name = {MD_KER: "K", MD_SUP: "S", MD_USR: "U"}[mode]
        self.name = f"{'PAR' if is_par else 'PDR'}{'ID'[space]}{mode_name}"

    def read_word(self, offset: int) -> int:
        page = (offset & 0o177776) >> 1
        m = self._mmu
        if self._is_par:
            return m.par[self._space][self._mode][page] & PAR_22B
        return m.pdr[self._space][self._mode][page] & 0o177777

    def write_word(self, offset: int, value: int) -> None:
        page = (offset & 0o177776) >> 1
        m = self._mmu
        if self._is_par:
            m.par[self._space][self._mode][page] = value & PAR_22B
        else:
            # A/W are hardware-set flags: writes preserve them
            m.pdr[self._space][self._mode][page] = (value & PDR_WR & 0o177777) \
                | (m.pdr[self._space][self._mode][page] & (PDR_A | PDR_W))
