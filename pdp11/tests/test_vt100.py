"""
VT100/VT52 frontend tests (spec §8, M7): the escape-sequence state machine
(cursor control, AVO 24-line scroll, ANSI/VT52 modes, 7-bit contract) and
the TerminalFrontend wiring over a RenderBackend.
"""

from __future__ import annotations

from pdp11.graphics.render_backend import RenderBackend
from pdp11.graphics.vt100 import (
    BEL,
    COLS,
    CR,
    ESC,
    LF,
    ROWS,
    VT100,
    VT52Frontend,
    VT100Frontend,
)


class FakeBackend(RenderBackend):
    def __init__(self, input_bytes: bytes = b"") -> None:
        self.input = bytearray(input_bytes)
        self.output: list[int] = []

    def getch(self) -> int | None:
        return self.input.pop(0) if self.input else None

    def putch(self, byte: int) -> None:
        self.output.append(byte)

    def flush(self) -> None:
        pass

    def text(self) -> str:
        return "".join(chr(b) for b in self.output)


def feed_str(term: VT100, s: str) -> None:
    for ch in s:
        term.feed(ord(ch))


def feed_bytes(term: VT100, *bs: int) -> None:
    for b in bs:
        term.feed(b)


def csi(term: VT100, seq: str) -> None:
    """Feed ESC [ <seq> as a CSI sequence."""
    term.feed(ESC)
    term.feed(0o133)
    for ch in seq:
        term.feed(ord(ch))


# -- plain text and cursor basics -------------------------------------------------

def test_plain_text_and_crlf():
    t = VT100()
    for ch in "HELLO":
        assert t.feed(ord(ch)) == ord(ch)       # printables forwarded to the host
    t.feed(CR)
    t.feed(LF)
    feed_str(t, "WORLD")
    assert t.line(0) == "HELLO"
    assert t.line(1) == "WORLD"
    assert (t.row, t.col) == (1, 5)
    assert len(t.text().split("\n")) == ROWS    # AVO 24-line screen (§8)


def test_scroll_at_bottom():
    t = VT100()
    feed_str(t, "TOP")
    for _ in range(ROWS):                        # one LF per line: 24th scrolls
        t.feed(LF)
    assert t.scrolls == 1
    assert t.line(0) == ""                       # TOP scrolled off
    assert (t.row, t.col) == (ROWS - 1, 3)       # LF does not reset the column


def test_immediate_wrap_at_col_80():
    t = VT100()
    for _ in range(COLS):
        t.feed(ord("a"))
    assert (t.row, t.col) == (0, COLS)
    t.feed(ord("b"))                             # printable at col 80 wraps first
    assert (t.row, t.col) == (1, 1)
    assert t.line(1) == "b"


def test_backspace_tab_bel():
    t = VT100()
    feed_str(t, "AB")
    t.feed(0o010)                                # BS
    assert t.col == 1
    t.feed(0o011)                                # HT -> next 8-col stop
    assert t.col == 8
    assert t.feed(BEL) == BEL and t.bells == 1   # BEL forwarded (the beep)


def test_seven_bit_mask():
    t = VT100()
    t.feed(0o301)                                # >7 bits: masked to 0o101 'A' (§8)
    assert t.screen[0][0] == ord("A")


# -- CSI sequences (ANSI mode) ------------------------------------------------------

def test_csi_cursor_position_and_moves():
    t = VT100()
    csi(t, "2;5H")                               # row 2, col 5 (1-based)
    assert (t.row, t.col) == (1, 4)
    t.feed(ord("X"))
    assert t.screen[1][4] == ord("X")
    assert t.col == 5                            # the X advanced the cursor
    csi(t, "2B")
    assert t.row == 3
    csi(t, "3C")
    assert t.col == 8
    csi(t, "A")
    assert t.row == 2
    csi(t, "D")
    assert t.col == 7
    csi(t, "10;20f")                             # 'f' == 'H'
    assert (t.row, t.col) == (9, 19)


def test_csi_erase_display_and_line():
    t = VT100()
    feed_str(t, "abcdef")
    csi(t, "K")                                  # mode 0: cursor -> end of line
    assert t.line(0) == "abcdef"                 # cursor at col 6: nothing erased
    t.col = 2
    csi(t, "1K")                                 # mode 1: start -> cursor
    assert t.line(0) == "   def"
    csi(t, "2K")                                 # whole line
    assert t.line(0) == ""
    feed_str(t, "junk")
    csi(t, "2J")                                 # erase entire display
    assert t.text() == "\n".join([""] * ROWS)


def test_sgr_and_charset_consumed():
    t = VT100()
    csi(t, "1;31m")                              # SGR: consumed, no rendition (R5)
    feed_bytes(t, ESC, 0o050, ord("0"))          # ESC ( 0: graphics charset ignored (R5)
    t.feed(ord("Q"))
    assert t.line(0) == "Q"


def test_esc_save_restore_ris():
    t = VT100()
    feed_str(t, "ab")
    feed_bytes(t, ESC, 0o067)                    # ESC 7 save cursor
    feed_bytes(t, CR, LF)
    feed_bytes(t, ESC, 0o070)                    # ESC 8 restore cursor
    assert (t.row, t.col) == (0, 2)
    feed_bytes(t, ESC, 0o143)                    # ESC c RIS
    assert (t.row, t.col) == (0, 0) and t.ansi is True


# -- VT52 reduced variant -------------------------------------------------------------

def test_vt52_mode_switch_and_sequences():
    t = VT100(vt52=True)
    assert t.ansi is False
    feed_bytes(t, ESC, 0o076)                    # ESC > -> ANSI
    assert t.ansi is True
    feed_bytes(t, ESC, 0o074)                    # ESC < -> VT52
    assert t.ansi is False
    feed_bytes(t, ESC, 0o102)                    # ESC B cursor down
    feed_bytes(t, ESC, 0o103)                    # ESC C cursor right
    assert (t.row, t.col) == (1, 1)
    feed_bytes(t, ESC, 0o110)                    # ESC H home
    assert (t.row, t.col) == (0, 0)
    feed_bytes(t, ESC, 0o131, 0o040 + 2, 0o040 + 5)   # ESC Y <row+040> <col+040>
    assert (t.row, t.col) == (2, 5)
    t.feed(ord("Z"))
    assert t.line(2) == "     Z"
    feed_bytes(t, ESC, 0o132)                    # ESC Z identify: flag only (§22.4)
    assert t.identified is True


# -- frontends over a RenderBackend ---------------------------------------------------

def test_vt100_frontend_forwards_printables_only():
    be = FakeBackend()
    fe = VT100Frontend(be)
    fe.write(ord("A"))
    csi_bytes = bytes([ESC, 0o133, ord("5"), 0o156])   # CSI 5 n (DSR): consumed, not forwarded
    for b in csi_bytes:
        fe.write(b)
    fe.write(ord("B"))
    assert be.text() == "AB"                     # only printables reached the host
    assert fe.term.line(0) == "AB"


def test_frontend_input_queue_and_break():
    be = FakeBackend(bytes([ord("x"), 0o003, ord("y"), 0o301]))  # 0o301 dropped (>7-bit, §8)
    fe = VT52Frontend(be)
    assert fe.name == "vt52" and fe.term.ansi is False
    assert fe.read() == ord("x")
    assert fe.break_pending() is True            # BREAK byte consumed (§13)
    assert fe.read() == ord("y")
    assert fe.read() is None                     # 0o301 was dropped with a debug log
    assert fe.at_eof() is False


def test_frontend_eof():
    class EofBackend(FakeBackend):
        def at_eof(self) -> bool:
            return True

    fe = VT100Frontend(EofBackend(b""))
    assert fe.read() is None and fe.at_eof() is True
