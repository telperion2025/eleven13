"""
Front panel / ODT console (spec §9, §13) — M2.

On HALT, control falls to ODT — the classic `@` prompt, behaving like the
real M9312/KD11-F console: `/` examine, `\\` deposit, `G` go, octal
addresses only (§9). Line-based command set (the host terminal delivers
bytes; ODT accumulates a line):

    <octal>/     examine word at address (prints it, sets current address)
    <octal>\\    deposit: read next line as an octal value, write at current
    G <octal>    go: set PC (default = current address) and resume RUN
    Q            quit the emulator
    (empty)      re-examine current address

Panel semantics (§13): RUN and HALT are external two-state events —
FrontPanel.halt()/run(). BREAK (host Ctrl-C, 0o003) during RUN drops to ODT
on the 11/03/94; the 11/70 uses the console halt switch; the 11/20's console
switches are the manual deposit path (ODT `\\`). Host-side behavior is
surfaced here, never by stopping mid-cycle or corrupting machine state.

Host I/O goes through the TerminalFrontend only (§8) — this module never
touches stdin/stdout directly.
"""

from __future__ import annotations

import logging

from pdp11.board import SystemBoard
from pdp11.cpu.registers import Reg
from pdp11.graphics.terminal import TerminalFrontend

_log = logging.getLogger(__name__)

PROMPT = "@"


class FrontPanel:
    """The two-state RUN/HALT panel control (§13)."""

    def __init__(self, board: SystemBoard) -> None:
        self.board = board

    def halt(self) -> None:
        """External HALT event (halt switch / BREAK): RUN -> HALT."""
        self.board.cpu.state.halted = True

    def run(self) -> None:
        """External RUN event (G): HALT -> RUN."""
        self.board.cpu.state.halted = False


class ODT:
    """Octal Debugging Technique console (§9): the `@` prompt shell.
    run() is the line loop; _dispatch() handles one command line and returns
    "go"/"quit" to leave the loop, None to continue — the M7 Debugger
    subclasses this and intercepts its extended commands in _dispatch."""

    prompt = PROMPT

    def __init__(self, board: SystemBoard, terminal: TerminalFrontend) -> None:
        self.board = board
        self.terminal = terminal
        self.current = 0            # current examine/deposit address (octal)

    def _readline(self) -> str | None:
        """Read one line of host input through the terminal (bytes until
        CR/LF). Returns None when host input is exhausted (EOF)."""
        chars: list[str] = []
        while True:
            b = self.terminal.read()
            if b is None:
                if self.terminal.at_eof():
                    return None      # non-interactive stdin closed: stop waiting
                return ""            # no input pending: treat as empty line
            if b in (0o012, 0o015):  # LF / CR ends the line
                break
            chars.append(chr(b))
        return "".join(chars)

    def _emit(self, text: str) -> None:
        for ch in text:
            self.terminal.write(ord(ch))

    def run(self) -> str:
        """ODT command loop until G (returns "go") or Q (returns "quit").
        On MMU machines (11/70, 11/94) the halt display shows the address
        with a caret first — the 11/70 console's `^ address` display (§13)."""
        if self.board.profile.has_mmu:
            self._emit(f"^{self.board.cpu.state.R[Reg.PC]:06o}\r\n")
        while True:
            self._emit(self.prompt)
            line = self._readline()
            if line is None:
                return "quit"                # host input exhausted (piped stdin)
            action = self._dispatch(line)
            if action is not None:
                return action

    @staticmethod
    def _oct(text: str) -> int | None:
        """Parse an octal field; None on garbage — ODT answers "?" (§9),
        never crashes on a malformed address."""
        try:
            return int(text.strip(), 8) & 0o177777
        except ValueError:
            return None

    def _dispatch(self, line: str) -> str | None:
        """Handle one command line; returns "go"/"quit" to leave the loop,
        None to prompt again."""
        line = line.strip()
        if not line:
            self._emit(f"{self.current:06o}/ {self.board.read_word(self.current):06o}\r\n")
            return None
        if line in ("Q", "q"):
            return "quit"
        if line in ("G", "g") or line.upper().startswith("G "):
            parts = line.split()
            if len(parts) > 1:
                addr = self._oct(parts[1])
                if addr is None:
                    self._emit("?\r\n")       # octal addresses only (§9)
                    return None
                self.board.cpu.state.R[Reg.PC] = addr
            else:
                self.board.cpu.state.R[Reg.PC] = self.current & 0o177777
            return "go"
        if line.endswith("/"):
            addr = self._oct(line[:-1])           # octal only (§9)
            if addr is None:
                self._emit("?\r\n")
                return None
            self.current = addr
            self._emit(f"{addr:06o}/ {self.board.read_word(addr):06o}\r\n")
            return None
        if line.endswith("\\"):
            addr = self._oct(line[:-1])
            if addr is None:
                self._emit("?\r\n")
                return None
            self.current = addr
            self._emit(self.prompt)
            nxt = self._readline()
            if nxt is None:
                return "quit"                     # host input exhausted mid-deposit
            value = self._oct(nxt) or 0
            self.board.write_word(self.current, value)
            self._emit(f"{self.current:06o}/ {value:06o}\r\n")
            return None
        self._emit("?\r\n")   # octal addresses only (§9)
        return None
