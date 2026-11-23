"""
LA36 DECwriter hardcopy rendering (spec §8) — M7: a hardcopy device, not a
screen. Buffered slow output with realistic line pacing.

Machine model (LA36 DECwriter II documentation / PDP-11 Peripherals
Handbook; cross-checked against SIMH la36.c — divergences flagged §22.4 in
docs/acceptance-checklist.md):
- 132-column print line (LINE_WIDTH); printable ASCII overwrites at the
  print position (a hardcopy cursor, no wrap — a byte past column 132 is
  dropped and the bell rings, flagged §22.4).
- CR returns the print position to column 0 WITHOUT printing; LF prints the
  buffered line (emits the line text + CR + LF to the host); FF prints the
  line and additionally emits a form feed (0o014); BEL rings (emitted to
  the host); BS/HT move the print position.
- Line pacing: the LA36 is slow because it prints a line per line-time.
  Here the pacing comes from the DL11 transmitter schedule (one byte per
  TX_CYCLES bus cycles, devices/dl11.py) — the DECwriter itself only buffers
  the line and emits it whole on the line terminator, so no wall-clock is
  involved (§19.6) and the model stays deterministic (§5.6).

Character-encoding contract (§8): strictly 7-bit ASCII in both directions;
non-7-bit host keys are dropped by BackendTerminal with a debug-log note.

References: LA36 DECwriter documentation (§21); SIMH la36.c.
"""

from __future__ import annotations

import logging

from pdp11.graphics.terminal import BackendTerminal

_log = logging.getLogger(__name__)

LINE_WIDTH = 132   # LA36 print line width in columns (decimal, off-metal §10.3)

BEL = 0o007
BS = 0o010
HT = 0o011
LF = 0o012
FF = 0o014
CR = 0o015


class LA36:
    """The DECwriter state machine. feed() consumes one guest output byte
    (hard 7-bit masked, §8) and returns the host bytes to emit NOW — a
    completed line comes out as its text followed by CR LF (plus FF for a
    form feed); everything else is buffered and returns []."""

    def __init__(self) -> None:
        self.line: list[int] = [0o040] * LINE_WIDTH
        self.col = 0
        self.lines_printed = 0   # completed lines (observability)
        self.bells = 0

    def text(self) -> str:
        """The current unprinted line buffer as text."""
        return "".join(chr(c) for c in self.line).rstrip()

    def feed(self, byte: int) -> list[int]:
        """Consume one guest output byte; return the host bytes to emit now."""
        b = byte & 0o177                      # hard 7-bit mask (§8)
        if b == CR:
            self.col = 0                      # carriage return alone does NOT print
            return []
        if b == LF:
            return self._print_line()
        if b == FF:
            out = self._print_line()
            out.append(FF)
            return out
        if b == BEL:
            self.bells += 1
            return [BEL]
        if b == BS:
            self.col = max(0, self.col - 1)
            return []
        if b == HT:
            self.col = min(LINE_WIDTH - 1, (self.col // 8 + 1) * 8)
            return []
        if 0o040 <= b <= 0o176:
            if self.col < LINE_WIDTH:
                self.line[self.col] = b       # hardcopy overwrite at the print position
                self.col += 1
            else:
                self.bells += 1               # column overflow: bell, byte dropped (§22.4)
            return []
        return []                             # remaining C0 controls: consumed

    def _print_line(self) -> list[int]:
        """Emit the buffered line as text + CR LF and re-buffer a blank line."""
        text = self.text()
        self.lines_printed += 1
        self.line = [0o040] * LINE_WIDTH
        self.col = 0
        return [ord(c) for c in text] + [CR, LF]


class LA36Frontend(BackendTerminal):
    """The DL11 console attached to an LA36 DECwriter: guest output is
    buffered line-by-line and each completed line is emitted to the
    RenderBackend as a block (pacing comes from the DL11 TX schedule, §8)."""

    name = "la36"

    def __init__(self, backend: RenderBackend) -> None:  # noqa: F821
        super().__init__(backend)
        self.term = LA36()

    def write(self, byte: int) -> None:
        for out in self.term.feed(byte):
            self._backend.putch(out)
        self._backend.flush()
