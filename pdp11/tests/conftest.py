"""
Shared pytest fixtures (spec §17.9's test_m1_skeleton.py uses
io_page_with_device).
"""

from __future__ import annotations

from typing import Any

import pytest

from pdp11.bus.abstract_bus import BusDevice
from pdp11.memory.io_page import IOPPage


class DummyDevice(BusDevice):
    """Minimal two-register device window for I/O page routing tests."""

    window_size = 4                 # two 16-bit registers = 4 bytes
    name = "DUMMY"

    def __init__(self) -> None:
        self.regs: list[int] = [0, 0]

    def read_word(self, offset: int) -> int:
        return self.regs[offset >> 1]

    def write_word(self, offset: int, value: int) -> None:
        self.regs[offset >> 1] = value & 0o177777

    def read_byte(self, offset: int) -> int:
        word = self.regs[offset >> 1]
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        idx = offset >> 1
        if offset % 2 == 0:
            self.regs[idx] = (self.regs[idx] & 0o177400) | (value & 0o377)
        else:
            self.regs[idx] = (self.regs[idx] & 0o000377) | ((value & 0o377) << 8)

    def serialize(self) -> dict[str, Any]:
        return {"regs": list(self.regs)}

    def deserialize(self, state: dict[str, Any]) -> None:
        if "regs" not in state:
            raise ValueError("dummy device snapshot missing 'regs'")
        self.regs = list(state["regs"])


class IOPPageHarness:
    """Wraps an IOPPage with one dummy device attached at the base address;
    attach_duplicate_window() re-attaches at the same address to force the
    overlap check."""

    def __init__(self, base: int = 0o177600) -> None:
        self.page = IOPPage(base)
        self.dummy = DummyDevice()
        self.page.attach(base, self.dummy)

    def attach(self, address: int, device: BusDevice) -> None:
        self.page.attach(address, device)

    def attach_duplicate_window(self) -> None:
        """Attach a second device over the dummy's window — must raise."""
        self.page.attach(0o177600, DummyDevice())


@pytest.fixture
def io_page_with_device() -> IOPPageHarness:
    """IOPage with a dummy device at 0o177600 (spec §17.9 fixture)."""
    return IOPPageHarness()
