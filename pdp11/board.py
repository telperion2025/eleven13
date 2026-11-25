"""
SystemBoard — assembles the machine from its profile (spec §4.2).

The board is the single MemoryInterface the CPU talks to (§4.2). Two
addressing regimes, selected by profile.has_mmu (never by a profile.key
check, §19.1):

- Flat (11/03, 11/20): CPU addresses are physical bus addresses (16/18-bit),
  RAM below the I/O page, the IOPPage at the top 8 KB of the 16-bit window
  (0o160000-0o177777 for all profiles — see docs/acceptance-checklist.md),
  the PSW register window at 0o177776-0o177777.
- MMU-mapped (11/70, 11/94 — M5/M6): CPU addresses are 16-bit VIRTUAL.
  board.read_word/write_word/read_byte/write_byte translate through
  cpu/mmu.py (mode from the PSW, I-space for instruction fetches via the
  `fetch` flag — §5.7 "instruction fetch must not touch the D-space map")
  to 22-bit physical, then route: RAM, the boot ROM window, or the I/O
  page. On 22-bit machines the I/O page lives at physical 0o17760000+ (the
  top 8 KB of the 22-bit space, SIMH IOPAGEBASE) and the flat 16-bit window
  0o160000-0o200000 aliases it so MMU-off code (boot ROM) reaches devices.

Devices never see virtual addresses: DMA transfers go through the board's
DMA faces — `dma_host` (18-bit Unibus address, through the Unibus map when
MMR3 BME is set — map_registers.py) for Unibus/Q-Bus NPR devices, and
`massbus_host` (22-bit physical direct, RH70 BAE:BA) for Massbus (§6.1).
Both count one bus cycle per word into cycles_used (§5.6).

Bus-error behavior is profile-driven (§5.5): the UNIBUS_TIMEOUT model
(odd-address word access, nonexistent memory, unassigned I/O window) raises
BusInit, which the CPU core routes to vector 000004; MMU page faults raise
MMUAbort (a BusInit subclass, cpu/mmu.py) routed to vector 000250 when MMR0
TENB is set. M4's LSI11_TOLERANT odd-address word access splits into a byte
pair (nonexistent-memory traps cleanly; backplane-dependent variants are
flagged §22.1). DCJ11_STRICT (M6, 11/94 J-11) is implemented: strict
timeout raising BusInit on nonexistent memory AND on odd-address word access
(no byte-split quirk); micro-trap format differences vs the 11/70 are
flagged §22.4.
"""

from __future__ import annotations

from typing import Any, NoReturn

from pdp11.bus.abstract_bus import BusDevice, BusInit
from pdp11.bus.qbus import QBus
from pdp11.bus.unibus import Unibus
from pdp11.config.profiles import BusErrorModel, BusType, MachineProfile
from pdp11.cpu import interrupts
from pdp11.cpu.core import CPU
from pdp11.cpu.mmu import (
    APR_BLOCKS,
    MMR012_BASE,
    MMR3_BASE,
    MMR3_BME,
    MMU,
    APRWindow,
    MMR012Window,
    MMR3Window,
)
from pdp11.cpu.registers import PSW, Reg
from pdp11.memory.io_page import IOPPage
from pdp11.memory.map_registers import (
    MAP_WINDOW_BASE,
    Q22_MAP_WINDOW_BASE,
    Q22Map,
    UnibusMap,
)
from pdp11.memory.ram import RAM
from pdp11.memory.rom import ROM
from pdp11.system_clock import SystemClock

WORD_MASK = 0o177777


class PSWWindow(BusDevice):
    """The PSW as an I/O-page register at octal 177776 (low byte 177777),
    per §17.6. On machines without an MMU the mode bits read as 0 and
    writes to them are ignored (PSW docstring; 11/20 behavior)."""

    window_size = 2
    name = "PSW"

    def __init__(self, cpu: CPU, profile: MachineProfile) -> None:
        self._cpu = cpu
        self._profile = profile

    def read_word(self, offset: int) -> int:
        """Read the PSW word (offset 0 = 177776)."""
        return self._cpu.state.psw.value

    def write_word(self, offset: int, value: int) -> None:
        """Write the PSW word; mode bits are dropped without an MMU."""
        v = value & WORD_MASK
        if not self._profile.has_mmu:
            v &= ~(PSW.Curr_MODE | PSW.Prev_MODE)
        old_cm = (self._cpu.state.psw.value & PSW.Curr_MODE) >> 14
        self._cpu.state.psw.value = v
        if self._profile.has_mmu:
            self._cpu.state.switch_mode((v & PSW.Curr_MODE) >> 14, old_cm)

    def read_byte(self, offset: int) -> int:
        """Byte read: offset 0 = condition byte, offset 1 = mode/IPL byte."""
        v = self._cpu.state.psw.value
        return v & 0o377 if offset == 0 else (v >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        """Byte write to either PSW half (the classic way to set IPL/T)."""
        psw = self._cpu.state.psw
        old_cm = (psw.value & PSW.Curr_MODE) >> 14
        if offset == 0:
            psw.value = (psw.value & 0o177400) | (value & 0o377)
        else:
            v = (psw.value & 0o377) | ((value & 0o377) << 8)
            if not self._profile.has_mmu:
                v &= ~(PSW.Curr_MODE | PSW.Prev_MODE)
            psw.value = v
        if self._profile.has_mmu:
            self._cpu.state.switch_mode((psw.value & PSW.Curr_MODE) >> 14, old_cm)

    def serialize(self) -> dict[str, Any]:
        """PSW state for snapshots (§15)."""
        return {"psw": self._cpu.state.psw.value}

    def deserialize(self, state: dict[str, Any]) -> None:
        """Restore PSW state; validates the schema key."""
        if "psw" not in state:
            raise ValueError("PSW snapshot missing 'psw' key")
        self._cpu.state.psw.value = state["psw"]


class _DMAFace:
    """The DMAHost face a DMA device drives its transfers through (§5.5/§6.1).

    use_map=True applies the 11/70 Unibus map to 18-bit Unibus addresses
    (when MMR3 BME is set); use_map=False passes the address through as a
    direct physical address (RH70 Massbus BAE:BA, 22-bit). Every word moved
    costs one bus cycle, counted in board.cycles_used (§5.6).
    """

    def __init__(self, board: SystemBoard, use_map: bool) -> None:
        self._board = board
        self._use_map = use_map

    def _pa(self, addr: int) -> int:
        b = self._board
        if self._use_map and b.map is not None:
            if isinstance(b.map, Q22Map):
                return b.map.translate(addr)      # Q22-CD map: always in use (M6)
            if b.mmu is not None and (b.mmu.mmr3 & MMR3_BME):
                return b.map.translate(addr & 0o777777)
        return addr & b._addr_mask

    def read_word(self, addr: int) -> int:
        b = self._board
        b.cycles_used += 1                      # one stolen cycle per word (§5.5)
        return b._route_read_word(self._pa(addr))

    def write_word(self, addr: int, value: int) -> None:
        b = self._board
        b.cycles_used += 1
        b._route_write_word(self._pa(addr), value)

    def read_byte(self, addr: int) -> int:
        b = self._board
        b.cycles_used += 1                      # one stolen cycle per byte
        return b._route_read_byte(self._pa(addr))

    def write_byte(self, addr: int, value: int) -> None:
        b = self._board
        b.cycles_used += 1
        b._route_write_byte(self._pa(addr), value & 0o377)


class SystemBoard:
    """The assembled machine: CPU + RAM + I/O page + devices, acting as the
    single MemoryInterface the CPU talks to (§4.2). Flat mode on 11/03/20;
    MMU-mapped 22-bit mode on 11/70/94 (M5/M6, profile.has_mmu)."""

    def __init__(self, profile: MachineProfile, clock: SystemClock,
                 ram_words: int | None = None) -> None:
        self.profile = profile
        self.clock = clock
        bits = profile.physical_bits
        self._addr_mask = (1 << bits) - 1
        # RAM size: profile ceiling clipped to the space below the I/O page.
        # 16/18-bit machines clip at the 16-bit I/O window (0o177600 bytes);
        # 22-bit machines clip at the top 8 KB of the 22-bit physical space.
        if bits > 18:
            ram_ceiling = ((1 << bits) - 0o20000) >> 1
        else:
            ram_ceiling = profile.iopage_base >> 1
        words = ram_words if ram_words is not None else min(profile.max_ram_words, ram_ceiling)
        self.ram = RAM(words)
        # The I/O page is the TOP 8 KB of the 16-bit window (0o160000-0o177777,
        # §6.2; documented in docs/acceptance-checklist.md). On 22-bit machines
        # the physical I/O page is the top 8 KB of the 22-bit space
        # (0o17760000+, SIMH IOPAGEBASE) and 0o160000-0o200000 aliases it so
        # flat (MMU-off) code reaches devices.
        self._io_base = 0o160000 if bits <= 18 else (1 << bits) - 0o20000
        self._io_alias = 0o160000 if bits > 18 else None
        self.io_page = IOPPage(0o160000)       # 16-bit-convention register map
        self.rom: ROM | None = None            # boot PROM overlay (§13)
        self.rom_base = 0
        # M2: Unibus machines get a real Unibus arbiter (NPR/BR daisy chain,
        # §6.1); the board's device list IS the backplane slot order.
        # M4: Q-Bus machines get the BIAK/BDMG arbiter (qbus.py).
        self.unibus: Unibus | None = (
            Unibus(profile.physical_bits) if profile.bus is BusType.UNIBUS else None)
        self.qbus: QBus | None = (
            QBus(profile.physical_bits) if profile.bus is BusType.QBUS else None)
        arbiter = self.unibus if self.unibus is not None else self.qbus
        self.devices: list[BusDevice] = arbiter.devices if arbiter is not None else []
        self.cpu = CPU(profile, clock)
        self.cpu.bus = self                    # the single MemoryInterface (§4.2)
        self.cpu.devices = self.devices
        self.io_page.attach(0o177776, PSWWindow(self.cpu, profile))
        self.cycles_used = 0                   # bus transfers this instruction (§5.6)
        # M5: MMU + Unibus map on has_mmu profiles; register windows attach
        # into the I/O page (addresses in cpu/mmu.py docstring).
        # M6: on the 22-bit Q-Bus profile (11/94) the Q22-CD map replaces the
        # Unibus map, and the J-11 selects I/D via MMR0 <3> (dspace_mmr0).
        self.mmu: MMU | None = (
            MMU(dspace_mmr0=profile.bus is BusType.QBUS) if profile.has_mmu else None)
        self.map: UnibusMap | Q22Map | None = None
        if profile.has_mmu:
            if profile.bus is BusType.UNIBUS:
                self.map = UnibusMap()
            elif bits > 18:
                self.map = Q22Map()
        if self.mmu is not None:
            self.io_page.attach(MMR012_BASE, MMR012Window(self.mmu))
            self.io_page.attach(MMR3_BASE, MMR3Window(self.mmu))
            for base, space, mode, is_par in APR_BLOCKS:
                self.io_page.attach(base, APRWindow(self.mmu, space, mode, is_par, base))
            if isinstance(self.map, UnibusMap):
                self.io_page.attach(MAP_WINDOW_BASE, self.map)
            elif isinstance(self.map, Q22Map):
                self.io_page.attach(Q22_MAP_WINDOW_BASE, self.map)
        # DMA faces for devices (§6.1): Unibus/Q-Bus NPR (map applied) and
        # Massbus (direct 22-bit physical).
        self.dma_host = _DMAFace(self, use_map=True)
        self.massbus_host = _DMAFace(self, use_map=False)
        # Power-on state (§13): registers and PSW cleared, PC from the profile
        # (11/20: 000000 — console-switch load; others: the 173000 boot ROM).
        self.cpu.state.R[Reg.PC] = profile.power_on_pc & WORD_MASK

    # -- MemoryInterface (§4.2) ---------------------------------------------
    def begin_instruction(self) -> None:
        """Reset the per-instruction bus-cycle counter; called by CPU.step."""
        self.cycles_used = 0

    def attach_rom(self, base: int, rom: ROM) -> None:
        """Overlay a boot PROM at octal `base` (M9312 window 173000, §13)."""
        self.rom = rom
        self.rom_base = base

    # -- virtual -> physical translation (M5, §5.7) ---------------------------
    def _va_to_pa(self, va: int, *, fetch: bool, mode: int | None,
                  is_write: bool) -> int:
        """Translate a CPU virtual address to physical. Flat when the profile
        has no MMU or the MMU is disabled (MMR0 MME clear). `fetch` selects
        I-space (instruction fetches never touch the D-space map, §5.7);
        `mode` overrides the PSW current mode (MFPI/MTPI previous-space)."""
        if self.mmu is None or not self.mmu.enabled:
            return va & self._addr_mask
        cm = mode if mode is not None else (self.cpu.state.psw.value & PSW.Curr_MODE) >> 14
        return self.mmu.translate(va & WORD_MASK, cm, is_instruction=fetch,
                                  is_write=is_write, phys_bits=self.profile.physical_bits)

    # -- physical routing -------------------------------------------------------
    def _classify_phys(self, pa: int) -> tuple[str, int]:
        """Route a physical address: ('rom'|'io'|'ram'|'none', device addr)."""
        if self.rom is not None:
            rom_bytes = len(self.rom.data) * 2
            if self.rom_base <= pa < self.rom_base + rom_bytes:
                return "rom", pa - self.rom_base
            if self._io_alias is not None \
                    and 0o17600000 + self.rom_base <= pa < 0o17600000 + self.rom_base + rom_bytes:
                return "rom", pa - (0o17600000 + self.rom_base)   # 22-bit mirror
        if pa >= self._io_base:
            return "io", 0o160000 + (pa - self._io_base)           # 22-bit I/O page
        if self._io_alias is not None and self._io_alias <= pa < 0o200000:
            return "io", pa                                         # flat 16-bit alias
        if pa < len(self.ram.data) * 2:
            return "ram", pa
        return "none", pa

    def _bus_error(self, addr: int, is_word: bool) -> NoReturn:
        """Apply the profile's bus_error_model (§5.5). UNIBUS_TIMEOUT raises
        BusInit (-> vector 000004 in the CPU; MMUAbort carries its own vector);
        the LSI-11/DCJ-11 quirk sets must not be guessed here (§19.10, §22.1)."""
        model = self.profile.bus_error_model
        if model is BusErrorModel.UNIBUS_TIMEOUT:
            kind = "odd-address word access" if is_word and addr & 1 else "nonexistent memory"
            raise BusInit(f"Unibus timeout ({kind}) at octal {oct(addr)}")
        if model is BusErrorModel.LSI11_TOLERANT:
            # nonexistent memory on the LSI-11 depends on backplane config
            # (§5.5); v1 models a clean Q-Bus timeout — flagged §22.1
            raise BusInit(f"Q-Bus timeout (nonexistent memory) at octal {oct(addr)}")
        if model is BusErrorModel.DCJ11_STRICT:
            # J-11 (11/94, M6): strict timeout on nonexistent memory AND on
            # odd-address word access (no byte-split quirk); the micro-trap
            # format differences vs the 11/70 are flagged §22.4
            kind = "odd-address word access" if is_word and addr & 1 else "nonexistent memory"
            raise BusInit(f"J-11 bus timeout ({kind}) at octal {oct(addr)}")
        raise NotImplementedError(f"§5.5 bus_error_model {model.name}")

    def _route_read_word(self, pa: int) -> int:
        """Read a 16-bit word at physical address `pa` (no cycle count — the
        caller charges it)."""
        pa &= self._addr_mask
        kind, addr = self._classify_phys(pa)
        if kind == "rom":
            return self.rom.read_word(addr) if self.rom else 0
        if kind == "ram":
            return self.ram.read_word(addr)
        if kind == "io":
            return self.io_page.read_word(addr)
        self._bus_error(pa, is_word=True)

    def _route_write_word(self, pa: int, value: int) -> None:
        pa &= self._addr_mask
        kind, addr = self._classify_phys(pa)
        if kind == "rom":
            if self.rom:
                self.rom.write_word(addr, value)   # PROMs ignore writes
        elif kind == "ram":
            self.ram.write_word(addr, value)
        elif kind == "io":
            self.io_page.write_word(addr, value)
        else:
            self._bus_error(pa, is_word=True)

    def _route_read_byte(self, pa: int) -> int:
        pa &= self._addr_mask
        kind, addr = self._classify_phys(pa)
        if kind == "rom":
            return self.rom.read_byte(addr) if self.rom else 0
        if kind == "ram":
            return self.ram.read_byte(addr)
        if kind == "io":
            return self.io_page.read_byte(addr)
        self._bus_error(pa, is_word=False)

    def _route_write_byte(self, pa: int, value: int) -> None:
        pa &= self._addr_mask
        kind, addr = self._classify_phys(pa)
        if kind == "rom":
            if self.rom:
                self.rom.write_byte(addr, value)   # ignored
        elif kind == "ram":
            self.ram.write_byte(addr, value)
        elif kind == "io":
            self.io_page.write_byte(addr, value)
        else:
            self._bus_error(pa, is_word=False)

    # -- CPU-facing bus (virtual on MMU machines) -------------------------------
    def _va_mask(self) -> int:
        """CPU address width: 16-bit virtual on MMU machines, else the full
        physical bus width (16-bit 11/20, 18-bit 11/03)."""
        return WORD_MASK if self.mmu is not None else self._addr_mask

    def read_word(self, addr: int, *, fetch: bool = False, mode: int | None = None) -> int:
        """Read a 16-bit word. On MMU machines `addr` is virtual: `fetch`
        selects I-space. One bus cycle per transfer (§5.6)."""
        va = addr & self._va_mask()
        if va & 1 and self.profile.bus_error_model is BusErrorModel.LSI11_TOLERANT \
                and self.mmu is None:
            # LSI-11 quirk (§5.5): an odd-address word read is split into two
            # byte transfers instead of trapping uniformly — flagged §22.4
            lo = self.read_byte(va)
            hi = self.read_byte((va + 1) & self._addr_mask)
            return lo | (hi << 8)
        self.cycles_used += 1
        if va & 1:                             # Unibus word reads must be even
            self._bus_error(va, is_word=True)
        pa = self._va_to_pa(va, fetch=fetch, mode=mode, is_write=False)
        return self._route_read_word(pa)

    def write_word(self, addr: int, value: int, *, fetch: bool = False,
                   mode: int | None = None) -> None:
        """Write a 16-bit word (virtual on MMU machines). One bus cycle."""
        va = addr & self._va_mask()
        if va & 1 and self.profile.bus_error_model is BusErrorModel.LSI11_TOLERANT \
                and self.mmu is None:
            # LSI-11 quirk (§5.5): odd-address word write splits into bytes
            self.write_byte(va, value & 0o377)
            self.write_byte((va + 1) & self._addr_mask, (value >> 8) & 0o377)
            return
        self.cycles_used += 1
        if va & 1:
            self._bus_error(va, is_word=True)
        pa = self._va_to_pa(va, fetch=fetch, mode=mode, is_write=True)
        self._route_write_word(pa, value)

    def read_byte(self, addr: int, *, fetch: bool = False, mode: int | None = None) -> int:
        """Read an 8-bit byte — legal on either half of any word (§6.2)."""
        va = addr & self._va_mask()
        self.cycles_used += 1
        pa = self._va_to_pa(va, fetch=fetch, mode=mode, is_write=False)
        return self._route_read_byte(pa)

    def write_byte(self, addr: int, value: int, *, fetch: bool = False,
                   mode: int | None = None) -> None:
        """Write an 8-bit byte, either half-word (DL11 console, RX01, §6.2)."""
        va = addr & self._va_mask()
        self.cycles_used += 1
        pa = self._va_to_pa(va, fetch=fetch, mode=mode, is_write=True)
        self._route_write_byte(pa, value)

    # -- device attachment (M2+) ---------------------------------------------
    def attach_device(self, address: int, device: BusDevice) -> None:
        """Attach a device window at octal `address` and plug it into the next
        backplane slot (slot order = daisy-chain priority, §6.1)."""
        self.io_page.attach(address, device)
        if self.unibus is not None:
            self.unibus.attach(device)
        elif self.qbus is not None:
            self.qbus.attach(device)
        else:
            self.devices.append(device)

    # -- bus arbitration face for the CPU (§6.1: the CPU asks the bus) ---------
    def poll_interrupt(self) -> tuple[int, int] | None:
        """BR/BG daisy-chain grant: (vector, ipl) or None. On Unibus machines
        the Unibus arbiter decides; other buses fall back to a plain
        slot-order scan until their modules land (M4/M6)."""
        if self.unibus is not None:
            return self.unibus.grant_interrupt()
        if self.qbus is not None:
            return self.qbus.grant_interrupt()
        return interrupts.pending(self.devices)

    def service_dma(self) -> int:
        """NPR cycle stealing at word granularity (§5.5/§6.1): grant the bus
        to the first DMA-requesting slot and let it run one transfer step.
        Every word it moves through the DMA face is already counted in
        cycles_used by the routing layer; the return value is the number
        of stolen cycles to charge to the current instruction (≥ 1)."""
        arbiter = self.unibus if self.unibus is not None else self.qbus
        if arbiter is None:
            return 0
        dev = arbiter.grant_dma()
        if dev is None:
            return 0
        before = self.cycles_used
        dev.tick()                            # the device moves its data via its DMA face
        return max(1, self.cycles_used - before)   # one cycle per word, at least one
