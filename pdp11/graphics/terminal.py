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
M7 implements the VT100/VT52 escape-sequence terminals (vt100.py) and the
LA36 hardcopy terminal (la36.py), all subclassing BackendTerminal.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

_log = logging.getLogger(__name__)

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


class BackendTerminal(TerminalFrontend):
    """Shared host-input queue over a RenderBackend (M2; the M7 escape-sequence
    terminals vt100.py/la36.py subclass it). read() is non-blocking, returns
    7-bit bytes only — host keys with no 7-bit equivalent are dropped here
    with a debug-log note (§8) — and break_pending() consumes a queued BREAK
    byte. Subclasses implement write() (raw console, VT100, LA36)."""

    def __init__(self, backend: RenderBackend) -> None:  # noqa: F821 — typing only
        from pdp11.graphics.render_backend import RenderBackend  # local: avoid cycle
        self._backend: RenderBackend = backend
        self._queue: list[int] = []

    def _fill(self) -> None:
        while True:
            b = self._backend.getch()
            if b is None:
                return
            if b <= 0o177:
                self._queue.append(b)
            else:
                _log.debug("dropped non-7-bit host key 0x%02x (§8)", b)

    def read(self) -> int | None:
        if not self._queue:
            self._fill()
        return self._queue.pop(0) if self._queue else None

    def break_pending(self) -> bool:
        if not self._queue:
            self._fill()
        if BREAK_BYTE in self._queue:
            self._queue.remove(BREAK_BYTE)
            return True
        return False

    def at_eof(self) -> bool:
        return not self._queue and self._backend.at_eof()


class ConsoleTerminal(BackendTerminal):
    """A plain text terminal over a RenderBackend (M2): output passes through
    with a hard 7-bit mask (§8)."""

    name = "console"

    def write(self, byte: int) -> None:
        self._backend.putch(byte & 0o177)     # hard 7-bit mask (§8)
        self._backend.flush()
