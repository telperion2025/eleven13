"""
Render backends (spec §8): host-facing display/input. This module — and
main.py only — may touch host I/O and import time.* (§19.6); the
deterministic machine core never does (§5.6).

M2 implements StdioBackend, a non-blocking POSIX stdio backend (select +
os.read on fd 0), which is all the ODT shell and console DL11 need. The
curses text-grid backend (the §8 default for full-screen use) and the
optional pixel backend (pygame/tkinter, [pixel] extra) land in M7.

Character-encoding contract (§8): host keys are mapped to 7-bit ASCII by
truncation where a plausible match exists; keys with no 7-bit equivalent
(accented letters, emoji, …) are dropped with a DEBUG log note.
"""

from __future__ import annotations

import logging
import os
import select
import sys
from abc import ABC, abstractmethod

_log = logging.getLogger(__name__)


class RenderBackend(ABC):
    """Host-facing I/O surface. getch() is NON-blocking (returns None when no
    key is pending) so the run loop can poll BREAK without stalling (§13)."""

    @abstractmethod
    def getch(self) -> int | None:
        """Next host key as a 7-bit ASCII byte, or None. Non-7-bit keys are
        dropped here with a debug-log note (§8)."""

    @abstractmethod
    def putch(self, byte: int) -> None:
        """Display one 7-bit byte from the guest."""

    @abstractmethod
    def flush(self) -> None:
        """Flush pending output to the host."""

    def at_eof(self) -> bool:
        """True when the host input stream is exhausted (e.g. piped stdin
        closed). Default: never EOF (interactive)."""
        return False


class StdioBackend(RenderBackend):
    """M2 text backend: POSIX stdin/stdout via select (non-blocking getch)."""

    def __init__(self) -> None:
        self._buf: list[int] = []
        self._eof = False

    def at_eof(self) -> bool:
        return self._eof and not self._buf

    def getch(self) -> int | None:
        if self._buf:
            return self._buf.pop(0)
        if self._eof:
            return None
        ready, _, _ = select.select([sys.stdin], [], [], 0)   # non-blocking poll
        if not ready:
            return None
        data = os.read(0, 256)
        if not data:
            self._eof = True                # host input stream exhausted
            return None
        for b in data:
            if b <= 0o177:
                self._buf.append(b)
            else:
                _log.debug("dropped non-7-bit host key 0x%02x (§8)", b)
        return self._buf.pop(0) if self._buf else None

    def putch(self, byte: int) -> None:
        sys.stdout.write(chr(byte & 0o177))

    def flush(self) -> None:
        sys.stdout.flush()
