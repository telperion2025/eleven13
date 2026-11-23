"""
BusDevice — the universal hardware-object contract (spec §6.1).

Every peripheral, memory block, and bus controller implements this abstract
base class. Read/write methods word and byte granularity, and accept bus
relative (octal) offsets into the device's own register window, not physical
addresses, which keeps devices agnostic of which bus or machine they're on.

Serialization (spec §15) is part of this contract: every device must be able
to state-dump and restore, so snapshot tests can enforce that no device is
silently omitted from BusDevice.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol


class DMAHost(Protocol):
    """The bus face a DMA-capable device drives its transfers through
    (§5.5/§6.1): word-granularity access to physical bus addresses while it
    holds the NPR grant. SystemBoard satisfies this structurally; devices
    depend only on this bus-layer contract and never import the board
    (layering, §3 — no cpu↔devices↔board imports)."""

    def read_word(self, addr: int) -> int: ...
    def write_word(self, addr: int, value: int) -> None: ...


class BusInit(Exception):
    """Raised inside a device read/write to trigger a bus timeout (§5.5 –
    the memory/Unibus layer routes this; devices themselves never call
    the trap vector directly, they raise and the bus takes care of it)."""


class BusDevice(ABC):
    """Anything attached to a bus — device, memory, or bus-to-bus adapter."""

    # -- register window (subclass overrides in class body) ----------------
    #: Size of the register window in BYTES (octal constants per §10).
    window_size: int = 0
    #: Human-readable name for logs/debugger ("DL11", "RK11", ...).
    name: str = "???"

    # -- required bus-facing operations ------------------------------------
    @abstractmethod
    def read_word(self, offset: int) -> int:
        """Read a 16-bit word at `offset` (0-based into this device's window)."""

    @abstractmethod
    def write_word(self, offset: int, value: int) -> None:
        """Write a 16-bit word. Bit <15:0> only; masks are the device's job."""

    @abstractmethod
    def read_byte(self, offset: int) -> int:
        """Read an 8-bit byte. Byte access is legal on the low or high half
        of a word (e.g., the DL11 console's RBUF/XBUF) — per §6.2."""

    @abstractmethod
    def write_byte(self, offset: int, value: int) -> None:
        """Write an 8-bit byte (bit <7:0> only)."""

    # -- optional capabilities (default-raise, devices override) ----------
    def dma_request(self) -> bool:
        """Device wants the bus as master (NPR on Unibus, BDMG on Q-Bus).
        Default: device cannot DMA; DMA-capable devices override."""
        return False

    def interrupt_pending(self) -> tuple[int, int] | None:
        """
        Device has an interrupt outstanding: return (vector, ipl) if it does,
        None if not. Polled by the bus arbiter — not by the CPU directly
        (§6.1: keep devices and CPU separate; the bus is the middleman).
        """
        return None

    def reset(self) -> None:
        """Handle a CPU RESET instruction / bus-init condition (§6.1).
        Overridden by devices with nontrivial reset state (RK11, RH70...)."""

    def tick(self) -> None:
        """Optional per-quantum state advance — devices that need clocking
        (tape motion, disk rotation, debounce) override this."""

    # -- snapshot contract (spec §15, mandatory for every device) ---------
    @abstractmethod
    def serialize(self) -> dict[str, Any]:
        """Return a JSON-safe dict of this device's full state."""

    @abstractmethod
    def deserialize(self, state: dict[str, Any]) -> None:
        """Restore state previously returned by serialize().
        Implementations must validate keys; a snapshot from a different
        profile version raises ValueError."""
