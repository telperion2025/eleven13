"""
SystemBoard — assembles the machine from its profile (spec §4.2).

M1 scope: the flat 16-bit MemoryInterface used by the 11/20 profile — RAM
below the I/O page, the IOPPage above it, the PSW register window at octal
177776-177777, and the CPU. Devices attach to the I/O page and join
board.devices (bus order = daisy-chain priority, §6.1) in M2+.

In M5/M6 this same class becomes the MMU-mapped 22-bit MemoryInterface
(selected by profile.has_mmu, §5.7) — the CPU only ever sees this one face.

Bus-error behavior is profile-driven (§5.5): the UNIBUS_TIMEOUT model
(odd-address word access, nonexistent memory, unassigned I/O window) raises
BusInit, which the CPU core routes to vector 000004. M4 implements
LSI11_TOLERANT's odd-address word access as a split byte pair (the
nonexistent-memory case traps cleanly; the backplane-dependent variants
are flagged §22.1). DCJ11_STRICT is M6 work and raises NotImplementedError
per §19.10 — never a guessed behavior.
"""

from __future__ import annotations

from typing import Any, NoReturn

from pdp11.bus.abstract_bus import BusDevice, BusInit
from pdp11.bus.qbus import QBus
from pdp11.bus.unibus import Unibus
from pdp11.config.profiles import BusErrorModel, BusType, MachineProfile
from pdp11.cpu import interrupts
from pdp11.cpu.core import CPU
from pdp11.cpu.registers import PSW, Reg
from pdp11.memory.io_page import IOPPage
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
        self._cpu.state.psw.value = v

    def read_byte(self, offset: int) -> int:
        """Byte read: offset 0 = condition byte, offset 1 = mode/IPL byte."""
        v = self._cpu.state.psw.value
        return v & 0o377 if offset == 0 else (v >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        """Byte write to either PSW half (the classic way to set IPL/T)."""
        psw = self._cpu.state.psw
        if offset == 0:
            psw.value = (psw.value & 0o177400) | (value & 0o377)
        else:
            v = (psw.value & 0o377) | ((value & 0o377) << 8)
            if not self._profile.has_mmu:
                v &= ~(PSW.Curr_MODE | PSW.Prev_MODE)
            psw.value = v

    def serialize(self) -> dict[str, Any]:
        """PSW state for snapshots (§15)."""
        return {"psw": self._cpu.state.psw.value}

    def deserialize(self, state: dict[str, Any]) -> None:
        """Restore PSW state; validates the schema key."""
        if "psw" not in state:
            raise ValueError("PSW snapshot missing 'psw' key")
        self._cpu.state.psw.value = state["psw"]


class SystemBoard:
    """The assembled machine: CPU + RAM + I/O page + devices, acting as the
    single MemoryInterface the CPU talks to (§4.2). Flat 16-bit mode in M1."""

    def __init__(self, profile: MachineProfile, clock: SystemClock,
                 ram_words: int | None = None) -> None:
        self.profile = profile
        self.clock = clock
        # RAM size: profile ceiling, clipped to the space below the I/O page.
        # (max_ram_words is in 16-bit words; the I/O page owns the top.)
        words = ram_words if ram_words is not None else min(
            profile.max_ram_words, profile.iopage_base >> 1)
        self.ram = RAM(words)
        # The I/O page is the TOP 8 KB of the 16-bit bus window (0o160000-0o177777,
        # §6.2 "top 8 KB"; the profile's 56 KB-usable comment matches). Within it,
        # profile.iopage_base (0o177600) marks the internal-register boundary, so
        # device windows like the console DL11 at 0o177560 route correctly.
        self._io_base = 0o200000 - 0o20000
        self.io_page = IOPPage(self._io_base)
        self.rom: ROM | None = None          # boot PROM overlay (§13), M2
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
        self.cpu.bus = self                      # the single MemoryInterface (§4.2)
        self.cpu.devices = self.devices
        self.io_page.attach(0o177776, PSWWindow(self.cpu, profile))
        self.cycles_used = 0                     # bus transfers this instruction (§5.6)
        self._addr_mask = (1 << profile.physical_bits) - 1   # 0o177777 at 16 bits
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

    def _classify(self, addr: int) -> str:
        """Route an address: 'rom', 'ram', 'io', or 'none' (nonexistent)."""
        if self.rom is not None and self.rom_base <= addr < self.rom_base + len(self.rom.data) * 2:
            return "rom"
        if addr >= self._io_base:
            return "io"
        if addr < len(self.ram.data) * 2:
            return "ram"
        return "none"

    def _bus_error(self, addr: int, is_word: bool) -> NoReturn:
        """Apply the profile's bus_error_model (§5.5). UNIBUS_TIMEOUT raises
        BusInit (-> vector 000004 in the CPU); the LSI-11/DCJ-11 quirk sets
        are M4/M6 work and must not be guessed here (§19.10, §22.1)."""
        model = self.profile.bus_error_model
        if model is BusErrorModel.UNIBUS_TIMEOUT:
            kind = "odd-address word access" if is_word and addr & 1 else "nonexistent memory"
            raise BusInit(f"Unibus timeout ({kind}) at octal {oct(addr)}")
        if model is BusErrorModel.LSI11_TOLERANT:
            # nonexistent memory on the LSI-11 depends on backplane config
            # (§5.5); v1 models a clean Q-Bus timeout — flagged §22.1
            raise BusInit(f"Q-Bus timeout (nonexistent memory) at octal {oct(addr)}")
        raise NotImplementedError(f"§5.5 bus_error_model {model.name} — M6 (11/94)")

    def read_word(self, addr: int) -> int:
        """Read a 16-bit word. One bus cycle per transfer (§5.6)."""
        addr &= self._addr_mask                  # wrap at the profile's physical width
        if addr & 1 and self.profile.bus_error_model is BusErrorModel.LSI11_TOLERANT:
            # LSI-11 quirk (§5.5): an odd-address word read is split into two
            # byte transfers instead of trapping uniformly — flagged §22.4
            lo = self.read_byte(addr)
            hi = self.read_byte((addr + 1) & self._addr_mask)
            return lo | (hi << 8)
        self.cycles_used += 1
        if addr & 1:                             # Unibus word reads must be even
            self._bus_error(addr, is_word=True)
        kind = self._classify(addr)
        if kind == "rom":
            return self.rom.read_word(addr - self.rom_base) if self.rom else 0
        if kind == "ram":
            return self.ram.read_word(addr)
        if kind == "io":
            return self.io_page.read_word(addr)
        self._bus_error(addr, is_word=True)

    def write_word(self, addr: int, value: int) -> None:
        """Write a 16-bit word. One bus cycle per transfer."""
        addr &= self._addr_mask
        if addr & 1 and self.profile.bus_error_model is BusErrorModel.LSI11_TOLERANT:
            # LSI-11 quirk (§5.5): odd-address word write splits into bytes
            self.write_byte(addr, value & 0o377)
            self.write_byte((addr + 1) & self._addr_mask, (value >> 8) & 0o377)
            return
        self.cycles_used += 1
        if addr & 1:
            self._bus_error(addr, is_word=True)
        kind = self._classify(addr)
        if kind == "rom":
            if self.rom:
                self.rom.write_word(addr - self.rom_base, value)  # PROMs ignore writes
        elif kind == "ram":
            self.ram.write_word(addr, value)
        elif kind == "io":
            self.io_page.write_word(addr, value)
        else:
            self._bus_error(addr, is_word=True)

    def read_byte(self, addr: int) -> int:
        """Read an 8-bit byte — legal on either half of any word (§6.2)."""
        addr &= self._addr_mask
        self.cycles_used += 1
        kind = self._classify(addr)
        if kind == "rom":
            return self.rom.read_byte(addr - self.rom_base) if self.rom else 0
        if kind == "ram":
            return self.ram.read_byte(addr)
        if kind == "io":
            return self.io_page.read_byte(addr)
        self._bus_error(addr, is_word=False)

    def write_byte(self, addr: int, value: int) -> None:
        """Write an 8-bit byte, either half-word (DL11 console, RX01, §6.2)."""
        addr &= self._addr_mask
        self.cycles_used += 1
        kind = self._classify(addr)
        if kind == "rom":
            if self.rom:
                self.rom.write_byte(addr - self.rom_base, value)  # ignored
        elif kind == "ram":
            self.ram.write_byte(addr, value)
        elif kind == "io":
            self.io_page.write_byte(addr, value)
        else:
            self._bus_error(addr, is_word=False)

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
        Every word it moves through read_word/write_word is already counted
        in cycles_used by the routing layer; the return value is the number
        of stolen cycles to charge to the current instruction (≥ 1)."""
        arbiter = self.unibus if self.unibus is not None else self.qbus
        if arbiter is None:
            return 0
        dev = arbiter.grant_dma()
        if dev is None:
            return 0
        before = self.cycles_used
        dev.tick()                            # the device moves its data via the bus
        return max(1, self.cycles_used - before)   # one cycle per word, at least one
