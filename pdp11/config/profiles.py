"""
Machine profiles: the single source of truth for per-model differences.

A MachineProfile describes a machine as a parts list — CPU capability
flags, bus type, memory size, presence of MMU/FPU/CIS — and the bus-error
model (§5.5). Nothing in cpu/ or bus/ may hard-code a model check; every
behavioral difference flows through these flags.

Capability tags (instruction availability, §5.3 of the spec):
    BASE  — instructions present on every PDP-11 including the 11/20
    EIS   — MUL/DIV/ASH/ASHC and FIS (KEF11 on the 11/03, stock on 70/94)
    CIS   — commercial instruction set (string ops)
    FPU   — FP11/Floating-point instructions
    MMU   — supervisor/MMU-dependent instructions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class CpuCap(Enum):
    """Instruction-set capability tags checked by the decoder before execute."""
    BASE = auto()   # every machine has these, including the bare 11/20
    EIS = auto()    # MUL/DIV/ASH/ASHC (+ FIS on LSI-11 with KEF11)
    CIS = auto()    # LOCC/SKPC/MOVC-style string instructions
    FPU = auto()    # FP11/FPJ floating-point set
    MMU = auto()    # supervisor/MMU-dependent instructions


class BusType(Enum):
    """Physical bus, which selects the arbitration module (bus/*.py)."""
    UNIBUS = auto()   # 11/20, 11/70
    QBUS = auto()     # 11/03 (16/18-bit), 11/94 (22-bit, asynchronous)
    MASSBUS = auto()  # 11/70 only — always paired with a Unibus host


class BusErrorModel(Enum):
    """
    Per-machine semantics on nonexistent memory / odd-address word access.

    UNIBUS_TIMEOUT      clean timeout -> trap through vector 000004 (11/20, 11/70)
    LSI11_TOLERANT      KD11-F quirk set: non-uniform odd-word behavior,
                        nonexistent-memory behavior depends on backplane
                        configuration (11/03) — see §5.5 errata notes
    DCJ11_STRICT        J-11 bus timeouts with micro-trap format (11/94)
    """
    UNIBUS_TIMEOUT = auto()   # 11/20, 11/70
    LSI11_TOLERANT = auto()   # 11/03 (KD11-F quirk set, §5.5)
    DCJ11_STRICT = auto()     # 11/94 (J-11 micro-trap format)


@dataclass(frozen=True)
class MachineProfile:
    """Immutable description of one emulated machine. Selected at launch."""

    key: str                                   # canonical CLI name: "11/03", "11/20", ...
    cpu_name: str                             # e.g. "KD11-B" — documentation only
    caps: frozenset[CpuCap]                   # instruction capabilities actually fitted
    bus: BusType                              # primary system bus
    has_mmu: bool                             # False on 11/03 and 11/20
    has_fpu: bool                             # FP11 fitted? (11/70 optional, 11/94 on-chip)
    physical_bits: int                        # 16 (11/20), 18 (11/03), 22 (11/70, 11/94)
    max_ram_words: int                        # physical RAM ceiling in 16-bit words
    bus_error_model: BusErrorModel            # §5.5 divergence
    power_on_pc: int                         # octal PC at power-on (§13)
    iopage_base: int                         # octal start of the I/O page window
    default_devices: tuple[str, ...] = field(default_factory=tuple)  # module names to attach

    def has(self, cap: CpuCap) -> bool:
        """True if this machine was configured with the given capability."""
        return cap in self.caps


PROFILES: dict[str, MachineProfile] = {
    # --- PDP-11/20: the original, non-microcoded machine -----------------
    # No EIS/CIS/FPU/MMU: the KD11-B consumes those opcodes as no-ops when
    # fitted without options (spec §5.4). Flat 16-bit addressing, no MMU.
    "11/20": MachineProfile(
        key="11/20", cpu_name="KD11-B",
        caps=frozenset({CpuCap.BASE}),
        bus=BusType.UNIBUS, has_mmu=False, has_fpu=False,
        physical_bits=16, max_ram_words=0o20000,          # 56 KB usable after I/O page
        bus_error_model=BusErrorModel.UNIBUS_TIMEOUT,
        power_on_pc=0o000000,                              # no ROM: console-switch load (§13)
        iopage_base=0o177600,
        default_devices=("dl11_console", "kw11l", "pc11"),
    ),
    # --- PDP-11/03 (LSI-11): Q-Bus, microcoded; EIS only with the KEF11 --
    "11/03": MachineProfile(
        key="11/03", cpu_name="KD11-F (LSI-11)",
        caps=frozenset({CpuCap.BASE, CpuCap.EIS}),        # KEF11 assumed fitted
        bus=BusType.QBUS, has_mmu=False, has_fpu=False,
        physical_bits=18, max_ram_words=0o100000,         # 64 KB
        bus_error_model=BusErrorModel.LSI11_TOLERANT,
        power_on_pc=0o173000,                             # boot ROM search (§13)
        iopage_base=0o177600,
        default_devices=("dl11_console", "kw11l", "rx11", "rk11_rk05"),
    ),
    # --- PDP-11/70: Unibus + Massbus, 22-bit, 3-mode MMU, FP11 -----------
    "11/70": MachineProfile(
        key="11/70", cpu_name="KD11-Z",
        caps=frozenset({CpuCap.BASE, CpuCap.EIS, CpuCap.FPU, CpuCap.MMU}),
        bus=BusType.UNIBUS, has_mmu=True, has_fpu=True,   # FP11 assumed fitted
        physical_bits=22, max_ram_words=0o4000000,       # 4 MB
        bus_error_model=BusErrorModel.UNIBUS_TIMEOUT,
        power_on_pc=0o173000,                             # M9312 boot ROM (§13)
        iopage_base=0o177600,
        default_devices=("dl11_console", "kw11l", "rh70_rp", "tm11"),
    ),
    # --- PDP-11/94 (DCJ-11): fastest PDP-11; everything on-chip ---------
    "11/94": MachineProfile(
        key="11/94", cpu_name="DCJ-11 (J-11)",
        caps=frozenset({CpuCap.BASE, CpuCap.EIS, CpuCap.CIS, CpuCap.FPU, CpuCap.MMU}),
        bus=BusType.QBUS, has_mmu=True, has_fpu=True,
        physical_bits=22, max_ram_words=0o4000000,        # 4 MB; async Q22-CD mapping
        bus_error_model=BusErrorModel.DCJ11_STRICT,
        power_on_pc=0o173000,
        iopage_base=0o177600,
        default_devices=("dl11_console", "kw11l", "rk11_rk05", "tqk11"),
    ),
}
