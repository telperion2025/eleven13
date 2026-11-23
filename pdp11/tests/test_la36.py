"""
LA36 DECwriter frontend tests (spec §8, M7): hardcopy line buffering, CR/LF/
FF/BEL semantics, 132-column overflow bell, and the TerminalFrontend wiring
(pacing itself comes from the DL11 TX schedule — deterministic, §5.6/§19.6).
"""

from __future__ import annotations

from pdp11.graphics.la36 import FF, LA36, LINE_WIDTH, LA36Frontend
from pdp11.graphics.render_backend import RenderBackend


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


def test_line_buffered_until_lf():
    prn = LA36()
    for ch in "HELLO":
        assert prn.feed(ord(ch)) == []           # buffered, nothing emitted yet
    assert prn.text() == "HELLO"
    out = prn.feed(0o012)                        # LF prints the line
    assert out == [ord(c) for c in "HELLO"] + [0o015, 0o012]
    assert prn.lines_printed == 1
    assert prn.text() == ""                      # buffer re-blanked


def test_cr_overwrites_without_printing():
    prn = LA36()
    for ch in "AB":
        prn.feed(ord(ch))
    assert prn.feed(0o015) == []                 # CR: col 0, NO print
    assert prn.lines_printed == 0
    prn.feed(ord("C"))                           # overwrites 'A' (hardcopy cursor)
    assert prn.feed(0o014) == [ord("C"), ord("B"), 0o015, 0o012, FF]  # FF: line + form feed
    assert prn.lines_printed == 1


def test_bel_bs_ht():
    prn = LA36()
    assert prn.feed(0o007) == [0o007] and prn.bells == 1
    prn.feed(ord("x"))
    prn.feed(0o010)                              # BS moves the print position back
    prn.feed(ord("y"))
    assert prn.text() == "y"
    prn2 = LA36()
    prn2.feed(ord("a"))
    prn2.feed(0o011)                             # HT -> next 8-column stop
    assert prn2.col == 8


def test_column_overflow_bells():
    prn = LA36()
    for _ in range(LINE_WIDTH + 8):
        prn.feed(0o101)
    assert prn.col == LINE_WIDTH                 # hardcopy cursor stops at the margin
    assert prn.bells == 8                        # bytes past 132 dropped with a bell (§22.4)


def test_seven_bit_mask():
    prn = LA36()
    prn.feed(0o301)                              # masked to 0o101 'A' (§8)
    assert prn.text() == "A"


def test_la36_frontend_emits_completed_lines():
    be = FakeBackend()
    fe = LA36Frontend(be)
    assert fe.name == "la36"
    for ch in "BOOT":
        fe.write(ord(ch))
    assert be.text() == ""                       # still buffered
    fe.write(0o012)
    assert be.text() == "BOOT\r\n"               # line flushed as a block
    assert fe.term.lines_printed == 1
    assert fe.read() is None and fe.break_pending() is False
