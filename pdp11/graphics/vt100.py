"""
VT100/VT52 software terminal emulators (spec §8) — M7: a real state machine,
not raw pass-through — RT-11/RSTS expect the escape semantics. Scope for v1
(§8, R5 ceiling): cursor control sequences, AVO 24-line mode, ANSI mode;
VT52 mode as a reduced variant. Graphics character sets are OUT of scope for
v1 (§8, R5) — charset designations are consumed and ignored.

Machine model (DEC VT100/VT52 programmer references; the scope overlap is
cross-checked against SIMH vt100.c — divergences flagged §22.4 in
docs/acceptance-checklist.md):
- 24 x 80 screen (the AVO 24-line mode), cursor row/col, top-left origin.
- Wrapping is IMMEDIATE: a printable at column 80 linefeeds first. The real
  VT100 defers the wrap by one character (pending-wrap flag) — flagged
  §22.4.
- ANSI (VT100) mode is entered with ESC > and left with ESC < ; the VT52
  frontend starts in VT52 mode (ESC < / ESC > switch at runtime, as on the
  real VT52-with-VT100-switch). VT52 subset: ESC A/B/C/D cursor moves,
  ESC H home, ESC I reverse linefeed, ESC J/K erase, ESC Y direct cursor
  addressing (two bytes, each value+0o40), ESC Z identify (flag only — the
  answer is an input byte, not modeled).
- CSI (ESC [) sequences: A/B/C/D cursor moves, H/f cursor position (1-based),
  J erase display (0/1/2), K erase line (0/1/2), m SGR consumed and ignored
  (v1 has no rendition). Scroll regions (r) and other modes are consumed,
  not modeled (R5).
- BEL is forwarded to the host display (the terminal beeps); printable
  ASCII, CR and LF are forwarded too, so a plain StdioBackend still shows a
  live transcript — the frontend's write() forwards whatever feed() returns.

Character-encoding contract (§8): strictly 7-bit ASCII in both directions.
Host keys with no 7-bit equivalent are dropped by BackendTerminal with a
debug-log note. Host arrow keys arrive as terminal-encoded ESC [ A/B/C/D
sequences and are interpreted by this state machine like any guest output;
editing keys with no 7-bit equivalent are dropped (§8).

References: VT100 User Guide (EK-VT100-UG-003), VT52 User Guide
(EK-VT52-HR-002), PDP-11 Peripherals Handbook DL11 section; SIMH vt100.c (§21).
"""

from __future__ import annotations

import logging

from pdp11.graphics.terminal import BackendTerminal

_log = logging.getLogger(__name__)

ROWS = 24   # AVO 24-line mode (§8)
COLS = 80

ESC = 0o033
BEL = 0o007
BS = 0o010
HT = 0o011
LF = 0o012
VT = 0o013
FF = 0o014
CR = 0o015

# parser states
_GROUND, _ESC, _CHARSET, _CSI, _VT52_ROW, _VT52_COL = range(6)


class VT100:
    """The VT100/VT52 state machine. feed() consumes one guest output byte
    (hard 7-bit masked, §8) and returns the byte to forward to the host
    display (printable, CR, LF, BEL) or None for consumed control sequences."""

    def __init__(self, vt52: bool = False) -> None:
        self._reset(vt52=vt52)

    def _reset(self, vt52: bool) -> None:
        """RIS state (ESC c) and power-on: blank screen, cursor home, mode
        per the frontend (ANSI default, VT52 for VT52Frontend)."""
        self.ansi = not vt52
        self.screen: list[list[int]] = [[0o040] * COLS for _ in range(ROWS)]
        self.row = 0
        self.col = 0
        self._state = _GROUND
        self._csi = ""
        self._vt52_row = 0
        self.saved = (0, 0)
        self.scrolls = 0       # lines scrolled off the top (observability)
        self.bells = 0
        self.identified = False  # ESC Z seen (VT52 identify; answer not modeled, §22.4)

    # -- rendering ---------------------------------------------------------
    def text(self) -> str:
        """The screen as 24 lines of text (trailing blanks stripped)."""
        return "\n".join("".join(chr(c) for c in line).rstrip() for line in self.screen)

    def line(self, row: int) -> str:
        """One screen row as text."""
        return "".join(chr(c) for c in self.screen[row]).rstrip()

    # -- byte stream ---------------------------------------------------------
    def feed(self, byte: int) -> int | None:
        """Consume one guest output byte; return the host-forward byte or None."""
        b = byte & 0o177                      # hard 7-bit mask (§8)
        if self._state == _GROUND:
            return self._ground(b)
        if self._state == _ESC:
            self._state = _GROUND
            return self._esc(b)
        if self._state == _CHARSET:
            self._state = _GROUND             # ESC ( x / ESC ) x: graphics charset, ignored (R5)
            return None
        if self._state == _CSI:
            if 0o100 <= b <= 0o176:           # final byte terminates the sequence
                self._state = _GROUND
                self._csi_dispatch(b)
                return None
            self._csi += chr(b)               # parameter/intermediate bytes
            return None
        if self._state == _VT52_ROW:          # ESC Y <row+0o40> <col+0o40>
            self._state = _VT52_COL
            self._vt52_row = (b - 0o040) & 0o177
            return None
        # _VT52_COL
        self._state = _GROUND
        self.row = min(ROWS - 1, self._vt52_row)
        self.col = min(COLS - 1, (b - 0o040) & 0o177)
        return None

    def _ground(self, b: int) -> int | None:
        if b == ESC:
            self._state = _ESC
            return None
        if b == CR:
            self.col = 0
            return b
        if b in (LF, VT, FF):
            self._linefeed()
            return b if b == LF else None     # forward LF; VT/FF consumed
        if b == BS:
            self.col = max(0, self.col - 1)
            return None
        if b == HT:
            self.col = min(COLS - 1, (self.col // 8 + 1) * 8)
            return None
        if b == BEL:
            self.bells += 1
            return b
        if 0o040 <= b <= 0o176:
            self._put(b)
            return b
        return None                           # remaining C0 controls: consumed

    def _put(self, b: int) -> None:
        if self.col >= COLS:                  # immediate wrap (deferred wrap §22.4)
            self._linefeed()
            self.col = 0
        self.screen[self.row][self.col] = b
        self.col += 1

    def _linefeed(self) -> None:
        if self.row >= ROWS - 1:
            self.screen.pop(0)                # scroll the top line off
            self.screen.append([0o040] * COLS)
            self.scrolls += 1
        else:
            self.row += 1

    def _reverse_index(self) -> None:
        if self.row == 0:
            self.screen.insert(0, [0o040] * COLS)
            self.screen.pop()
            self.scrolls += 1
        else:
            self.row -= 1

    def _clamp(self) -> None:
        self.row = min(ROWS - 1, max(0, self.row))
        self.col = min(COLS - 1, max(0, self.col))

    # -- escape sequences ------------------------------------------------------
    def _esc(self, b: int) -> int | None:
        if b == 0o133:                        # '[' -> CSI parameter collection
            self._state = _CSI
            self._csi = ""
            return None
        if b in (0o050, 0o051, 0o052, 0o053):  # ( ) * + : charset designate, consume 1 more (R5)
            self._state = _CHARSET
            return None
        if b == 0o076:                        # '>' enter ANSI mode
            self.ansi = True
            return None
        if b == 0o074:                        # '<' enter VT52 mode
            self.ansi = False
            return None
        if not self.ansi:
            return self._vt52_esc(b)
        if b == 0o067:                        # '7' save cursor
            self.saved = (self.row, self.col)
            return None
        if b == 0o070:                        # '8' restore cursor
            self.row, self.col = self.saved
            self._clamp()
            return None
        if b == 0o104:                        # 'D' index (linefeed)
            self._linefeed()
            return None
        if b == 0o105:                        # 'E' next line
            self._linefeed()
            self.col = 0
            return None
        if b == 0o115:                        # 'M' reverse index
            self._reverse_index()
            return None
        if b == 0o143:                        # 'c' RIS: full reset
            self._reset(vt52=False)
            return None
        return None                           # unknown ESC final: consumed

    def _vt52_esc(self, b: int) -> int | None:
        """The reduced VT52 escape set (ESC < selected this mode)."""
        if b == 0o101:                        # A cursor up
            self.row = max(0, self.row - 1)
        elif b == 0o102:                      # B cursor down
            self.row = min(ROWS - 1, self.row + 1)
        elif b == 0o103:                      # C cursor right
            self.col = min(COLS - 1, self.col + 1)
        elif b == 0o104:                      # D cursor left
            self.col = max(0, self.col - 1)
        elif b == 0o110:                      # H home
            self.row = 0
            self.col = 0
        elif b == 0o111:                      # I reverse linefeed
            self._reverse_index()
        elif b == 0o112:                      # J erase to end of screen
            self._erase_display(0)
        elif b == 0o113:                      # K erase to end of line
            self._erase_line(0)
        elif b == 0o131:                      # Y direct cursor address
            self._state = _VT52_ROW
        elif b == 0o132:                      # Z identify (answer is input, not modeled)
            self.identified = True
        return None

    # -- CSI sequences -----------------------------------------------------------
    def _csi_dispatch(self, final: int) -> None:
        raw = self._csi
        params = [int(p) if p.isdigit() else 0 for p in raw.split(";")] if raw else []

        def n(i: int, default: int = 1) -> int:
            v = params[i] if i < len(params) else 0
            return v if v else default

        if final == 0o101:                    # A cursor up
            self.row = max(0, self.row - n(0))
        elif final == 0o102:                  # B cursor down
            self.row = min(ROWS - 1, self.row + n(0))
        elif final == 0o103:                  # C cursor right
            self.col = min(COLS - 1, self.col + n(0))
        elif final == 0o104:                  # D cursor left
            self.col = max(0, self.col - n(0))
        elif final in (0o110, 0o146):         # H / f cursor position (1-based)
            self.row = min(ROWS - 1, max(0, n(0) - 1))
            self.col = min(COLS - 1, max(0, n(1) - 1))
        elif final == 0o112:                  # J erase display
            self._erase_display(n(0, 0))
        elif final == 0o113:                  # K erase line
            self._erase_line(n(0, 0))
        # 'm' (SGR), 'r' (scroll region) and the rest: consumed, not modeled (R5)
        self._clamp()

    def _erase_display(self, mode: int) -> None:
        if mode == 2:
            self.screen = [[0o040] * COLS for _ in range(ROWS)]
        elif mode == 0:                       # cursor to end of screen
            self._erase_line(0)
            for r in range(self.row + 1, ROWS):
                self.screen[r] = [0o040] * COLS
        elif mode == 1:                       # start of screen to cursor
            for r in range(0, self.row):
                self.screen[r] = [0o040] * COLS
            self._erase_line(1)

    def _erase_line(self, mode: int) -> None:
        line = self.screen[self.row]
        if mode == 0:                         # cursor to end of line
            for c in range(self.col, COLS):
                line[c] = 0o040
        elif mode == 1:                       # start of line to cursor
            for c in range(0, self.col + 1):
                line[c] = 0o040
        else:                                 # whole line
            self.screen[self.row] = [0o040] * COLS


class VT100Frontend(BackendTerminal):
    """The DL11 console attached to a VT100 (ANSI mode by default; ESC <
    switches to VT52 at runtime). Guest output bytes go through the state
    machine; feed()'s forwarded bytes (printable/CR/LF/BEL) reach the
    RenderBackend, so a stdio host shows a live transcript (§8)."""

    name = "vt100"

    def __init__(self, backend: RenderBackend, vt52: bool = False) -> None:  # noqa: F821
        super().__init__(backend)
        self.term = VT100(vt52=vt52)

    def write(self, byte: int) -> None:
        out = self.term.feed(byte)
        if out is not None:
            self._backend.putch(out)
        self._backend.flush()


class VT52Frontend(VT100Frontend):
    """The VT52 reduced variant: starts in VT52 mode (ESC > switches to ANSI)."""

    name = "vt52"

    def __init__(self, backend: RenderBackend) -> None:  # noqa: F821
        super().__init__(backend, vt52=True)
