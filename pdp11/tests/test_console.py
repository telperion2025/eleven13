"""
M2 integration tests: ODT front panel (§9/§13), machine assembly and the run
loop in main.py, and the built-in boot ROM path (§13) on the 11/03 profile.

M3: device wiring (KW11-L, RK11 + --disk containers) and the end-to-end
RK05 boot-block path — I/O-page register writes, NPR cycle stealing, and
the BR5/vector-220 completion interrupt (§7/§11.3).
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


# -- M3: device wiring + RK05 boot path (§7/§16/§11.3) -----------------------------


def test_assemble_attaches_m3_devices(tmp_path):
    """--disk attaches the RK11 at 177400 and profiles listing kw11l get the
    line clock at 177546; both route through the I/O page (§6.2)."""
    from pdp11.disk_images.container import open_rk05
    img = str(tmp_path / "d.rk05")
    open_rk05(img).flush()
    args = build_arg_parser().parse_args(["--machine", "11/03", "--disk", img])
    board = assemble(args, ConsoleTerminal(FakeBackend()))
    names = [d.name for d in board.devices]
    assert names == ["DL11", "KW11L", "RK11"]           # rx11 still stubbed (M4)
    rk = board.devices[-1]
    assert rk.base == 0o177400 and rk.drive.loaded      # type: ignore[attr-defined]
    assert board.read_word(0o177404) & 0o200            # RKCS DONE at reset
    assert board.read_word(0o177546) == 0               # KW11-L LKS clear
    # 11/20 defaults lack rk11_rk05, but --disk attaches it anyway (§11.3)
    args2 = build_arg_parser().parse_args(["--machine", "11/20", "--disk", img])
    board2 = assemble(args2, ConsoleTerminal(FakeBackend()))
    assert [d.name for d in board2.devices] == ["DL11", "KW11L", "RK11"]


def test_rk05_boot_block_end_to_end(tmp_path):
    """M3 end-to-end boot path (§11.3, without a real RT-11 image — that
    awaits the operator's image per §22.1): a hand-assembled program on the
    11/20 issues an RK11 READ of sector 0 into RAM through the I/O page,
    NPR word-at-a-time cycle stealing, and the BR5/vector-220 completion
    interrupt, then HALTs.

    Program layout (octal): code at 0o1000, DMA buffer at 0o4000, interrupt
    flag at 0o2000, vector-220 handler at 0o1100.
    """
    from pdp11.disk_images.container import RK05_SECTOR, open_rk05
    img_path = str(tmp_path / "boot.rk05")
    image = open_rk05(img_path)
    words = [(0o100000 + i) & 0o177777 for i in range(RK05_SECTOR // 2)]
    data = bytearray(RK05_SECTOR)
    for i, w in enumerate(words):
        data[2 * i] = w & 0o377
        data[2 * i + 1] = (w >> 8) & 0o377
    image.write_sector(0, bytes(data))
    image.flush()

    args = build_arg_parser().parse_args(["--machine", "11/20", "--disk", img_path])
    board = assemble(args, ConsoleTerminal(FakeBackend()))
    rk = next(d for d in board.devices if d.name == "RK11")
    assert rk.drive.loaded                              # type: ignore[attr-defined]

    program = {
        0o1000: [0o012737, 0o177400, 0o177406],  # MOV #-256,@#RKWC   (RKWC=177400)
        0o1006: [0o012737, 0o004000, 0o177410],  # MOV #0o4000,@#RKBA (buffer)
        0o1014: [0o005037, 0o177412],            # CLR @#RKDA         (cyl0 surf0 sect0)
        0o1020: [0o012737, 0o000111, 0o177404],  # MOV #READ|IE|GO,@#RKCS
        0o1026: [0o032737, 0o000200, 0o177404],  # poll: BIT #DONE,@#RKCS (src mode 2, dst mode 3)
        0o1034: [0o001774],                      # BEQ 0o1026 (disp -4 words)
        0o1036: [0o000000],                      # HALT
        0o1100: [0o012737, 0o000001, 0o002000],  # handler: MOV #1,@#0o2000 (flag)
        0o1106: [0o012737, 0o000010, 0o177404],  # MOV #READ,@#RKCS (clear IE)
        0o1114: [0o000002],                      # RTI
        0o0220: [0o001100],                      # vector 220 -> handler 0o1100
        0o0222: [0o000240],                      #   new PSW: IPL 5
    }
    for addr, ws in program.items():
        for i, w in enumerate(ws):
            board.ram.write_word(addr + 2 * i, w)
    cpu = board.cpu
    cpu.state.R[Reg.SP] = 0o17776
    cpu.state.R[Reg.PC] = 0o1000
    steps = 0
    while not cpu.state.halted and steps < 20000:
        cpu.step()                                   # service_dma steals 1 word/step
        steps += 1
    assert cpu.state.halted, "program did not reach HALT"
    for i, w in enumerate(words):                    # sector 0 landed at 0o4000
        assert board.ram.read_word(0o4000 + 2 * i) == w
    assert board.ram.read_word(0o2000) == 1          # vector-220 handler ran
    assert board.clock.cycle >= 256                  # 256 stolen DMA cycles charged
