"""
M2 integration tests: ODT front panel (§9/§13), machine assembly and the run
loop in main.py, and the built-in boot ROM path (§13) on the 11/03 profile.
"""

from __future__ import annotations

from pdp11.board import SystemBoard
from pdp11.control.front_panel import ODT, FrontPanel
from pdp11.control.rom_loader import BOOT_BASE, builtin_bootstrap, load_prom
from pdp11.cpu.registers import Reg
from pdp11.graphics.render_backend import RenderBackend
from pdp11.graphics.terminal import ConsoleTerminal
from pdp11.main import assemble, build_arg_parser, main, run


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


def make_board(machine: str = "11/20", input_bytes: bytes = b"") -> tuple[SystemBoard, FakeBackend]:
    args = build_arg_parser().parse_args(["--machine", machine])
    backend = FakeBackend(input_bytes)
    board = assemble(args, ConsoleTerminal(backend))
    return board, backend


# -- ODT (§9) ----------------------------------------------------------------

def test_odt_examine_deposit_go():
    board, backend = make_board(input_bytes=b"200/\n200\\\n777\nG 100\n")
    board.ram.write_word(0o000200, 0o123456)
    odt = ODT(board, ConsoleTerminal(backend))
    assert odt.run() == "go"                       # G resumes
    out = backend.text()
    assert "000200/ 123456" in out                 # / examine printed the word
    assert board.ram.read_word(0o000200) == 0o777  # \ deposit wrote the value
    assert board.cpu.state.R[Reg.PC] == 0o000100   # G <octal> set the PC


def test_odt_octal_only_rejects_garbage():
    board, backend = make_board(input_bytes=b"xyz\nQ\n")
    assert ODT(board, ConsoleTerminal(backend)).run() == "quit"
    assert "?" in backend.text()


def test_front_panel_run_halt_events():
    board, _ = make_board()
    panel = FrontPanel(board)
    panel.halt()
    assert board.cpu.state.halted is True          # RUN -> HALT (§13)
    panel.run()
    assert board.cpu.state.halted is False         # HALT -> RUN


# -- assembly + run loop (main.py, §4.2) --------------------------------------

def test_main_1120_halts_into_odt_then_quits():
    backend = FakeBackend(b"Q\n")
    rc = main(["--machine", "11/20"], backend=backend)
    assert rc == 0
    assert "@" in backend.text()                   # 11/20 starts at 000000 -> HALT -> ODT


def test_1103_builtin_bootstrap_prints_banner():
    """11/03 boots the built-in non-authentic bootstrap at 173000 (§13), which
    prints a banner through the console DL11 and HALTs -> ODT."""
    board, backend = make_board("11/03", input_bytes=b"Q\n")
    assert board.rom is not None and board.rom_base == BOOT_BASE
    rc = run(board, ConsoleTerminal(backend))
    assert rc == 0
    assert "non-authentic" in backend.text()       # banner came out via DL11
    assert board.cpu.state.halted                  # bootstrap HALTed into ODT


def test_bootrom_image_loads(tmp_path):
    words = [0o000000] * 0o1000
    words[0] = 0o000240                            # NOP at 173000
    blob = b"".join(bytes([w & 0o377, (w >> 8) & 0o377]) for w in words)
    f = tmp_path / "boot.prom"
    f.write_bytes(blob)
    rom = load_prom(str(f))
    assert rom.read_word(0) == 0o000240
    import pytest
    with pytest.raises(ValueError):
        bad = tmp_path / "bad.prom"
        bad.write_bytes(b"\x00" * 10)
        load_prom(str(bad))


def test_builtin_bootstrap_assembles():
    rom = builtin_bootstrap("X")
    assert rom.read_word(0) == 0o012700            # MOV #'X',R0
    assert rom.read_word(2) == ord("X")
    assert rom.read_word(4) == 0o010037            # MOV R0,@#XMTBUF
    assert rom.read_word(6) == 0o177566


def test_config_overrides_layer_over_profile(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text('{"memory_words": "0o4000", "devices": ["dl11_console"]}')
    args = build_arg_parser().parse_args(
        ["--machine", "11/20", "--config", str(cfg)])
    board = assemble(args, ConsoleTerminal(FakeBackend()))
    assert len(board.ram.data) == 0o4000
    assert any(d.name == "DL11" for d in board.devices)
