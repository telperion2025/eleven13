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
BusInit, which the CPU core routes to vector 000004. The LSI11_TOLERANT and
DCJ11_STRICT quirk sets are M4/M6 work and raise NotImplementedError here
per §19.10 — never a guessed behavior.
"""

from __future__ import annotations

from typing import Any, NoReturn

from pdp11.bus.abstract_bus import BusDevice, BusInit
from pdp11.config.profiles import BusErrorModel, MachineProfile
from pdp11.cpu.core import CPU
from pdp11.cpu.registers import PSW, Reg
from pdp11.memory.io_page import IOPPage
from pdp11.memory.ram import RAM
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
        self.io_page = IOPPage(profile.iopage_base)
        self.devices: list[BusDevice] = []       # bus order = priority order (§6.1)
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

    def _classify(self, addr: int) -> str:
        """Route an address: 'ram', 'io', or 'none' (nonexistent memory)."""
        if addr >= self.profile.iopage_base:
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
        raise NotImplementedError(
            f"§5.5 bus_error_model {model.name} — M4 (11/03) / M6 (11/94)")

    def read_word(self, addr: int) -> int:
        """Read a 16-bit word. One bus cycle per transfer (§5.6)."""
        addr &= self._addr_mask                  # flat 16-bit wrap in M1
        self.cycles_used += 1
        if addr & 1:                             # Unibus word reads must be even
            self._bus_error(addr, is_word=True)
        kind = self._classify(addr)
        if kind == "ram":
            return self.ram.read_word(addr)
        if kind == "io":
            return self.io_page.read_word(addr)
        self._bus_error(addr, is_word=True)

    def write_word(self, addr: int, value: int) -> None:
        """Write a 16-bit word. One bus cycle per transfer."""
        addr &= self._addr_mask
        self.cycles_used += 1
        if addr & 1:
            self._bus_error(addr, is_word=True)
        kind = self._classify(addr)
        if kind == "ram":
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
        if kind == "ram":
            self.ram.write_byte(addr, value)
        elif kind == "io":
            self.io_page.write_byte(addr, value)
        else:
            self._bus_error(addr, is_word=False)

    # -- device attachment (M2+) ---------------------------------------------
    def attach_device(self, address: int, device: BusDevice) -> None:
        """Attach a device window at octal `address` and append it to the
        bus-ordered device list (slot order = daisy-chain priority, §6.1)."""
        self.io_page.attach(address, device)
        self.devices.append(device)
