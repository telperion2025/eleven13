"""
Abstract terminal frontend interface (spec §8, §3 layout).

The DL11 console connects to a virtual terminal through this interface;
host keyboard input maps to the terminal's input queue and terminal output
flows back out. No module outside graphics/ touches host I/O (§8) — the
only host-I/O implementations live in render_backend.py.

Character-encoding contract (§8): the virtual terminals are strictly 7-bit
ASCII devices, as the real ones were. Host Unicode keys map to 7-bit ASCII
by truncation/modulo where a plausible match exists; keys with no 7-bit
equivalent are dropped with a debug-log note, never passed through as
multi-byte garbage.

M2 implements ConsoleTerminal (a plain text terminal over a RenderBackend);
the VT100/VT52 state machines land in M7 (vt100.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

BREAK_BYTE = 0o003  # host Ctrl-C is the BREAK key (§13)


class TerminalFrontend(ABC):
    """The guest-facing side of a virtual terminal: the DL11 reads input
    bytes from it and writes output bytes to it."""

    name: str = "terminal"

    @abstractmethod
    def read(self) -> int | None:
        """Next input byte (7-bit ASCII) from the host, or None if none pending."""

    @abstractmethod
    def write(self, byte: int) -> None:
        """Send one guest output byte (7-bit) to the host display."""

    def break_pending(self) -> bool:
        """True if the BREAK key (Ctrl-C, §13) is pending in the input queue.
        Default: no BREAK source."""
        return False

    def at_eof(self) -> bool:
        """True when host input is exhausted (non-interactive stdin)."""
        return False


class ConsoleTerminal(TerminalFrontend):
    """A plain text terminal over a RenderBackend (M2). Input bytes are
    buffered; break_pending() consumes a BREAK byte if one is queued."""

    name = "console"

    def __init__(self, backend: RenderBackend) -> None:  # noqa: F821 — typing only
        from pdp11.graphics.render_backend import RenderBackend  # local: avoid cycle
        self._backend: RenderBackend = backend
        self._queue: list[int] = []

    def _fill(self) -> None:
        while True:
            b = self._backend.getch()
            if b is None:
                return
            self._queue.append(b)

    def read(self) -> int | None:
        if not self._queue:
            self._fill()
        return self._queue.pop(0) if self._queue else None

    def write(self, byte: int) -> None:
        self._backend.putch(byte & 0o177)     # hard 7-bit mask (§8)
        self._backend.flush()

    def break_pending(self) -> bool:
        if not self._queue:
            self._fill()
        if BREAK_BYTE in self._queue:
            self._queue.remove(BREAK_BYTE)
            return True
        return False

    def at_eof(self) -> bool:
        return not self._queue and self._backend.at_eof()
