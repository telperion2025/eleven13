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
    assert names == ["DL11", "KW11L", "DZ11", "RX11", "RK11"]   # M4 devices landed
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


# -- M4: 11/03 Q-Bus assembly + RX01 boot path (§11.2/§12) -------------------------


def test_assemble_1103_qbus_devices(tmp_path):
    """11/03: Q-Bus arbiter on the board, DZ11 at 160100 (BIRQ4 on Q-Bus),
    RX11 at 177170 bound to an RX01 --disk image, RK11 at 177400 (no pack)."""
    from pdp11.disk_images.container import open_rx01
    img = str(tmp_path / "rt11.rx01")
    open_rx01(img).flush()
    args = build_arg_parser().parse_args(["--machine", "11/03", "--disk", img])
    board = assemble(args, ConsoleTerminal(FakeBackend()))
    assert board.qbus is not None and board.unibus is None
    assert board.qbus.addr_mask == 0o777777              # 18-bit physical
    names = [d.name for d in board.devices]
    assert names == ["DL11", "KW11L", "DZ11", "RX11", "RK11"]
    dz = next(d for d in board.devices if d.name == "DZ11")
    rx = next(d for d in board.devices if d.name == "RX11")
    assert dz.br_level == 4 and dz.base == 0o160100     # type: ignore[attr-defined]
    assert rx.base == 0o177170 and rx.image is not None  # type: ignore[attr-defined]
    assert board.read_word(0o160100) & 0o100000         # DZCSR TRDY at reset
    assert board.read_word(0o177170) == 0                # RXCS clear before init tick


def test_rx01_boot_sector_end_to_end(tmp_path):
    """M4 end-to-end on the 11/03 (§11.2 path shape): a hand-assembled
    program reads RX01 track 0 sector 1 through the RX11 TR handshake into
    RAM at 0o4000, with the BR5/vector-264 completion interrupt taken and
    handled, then HALTs. (A real RT-11 image boot awaits the operator's
    image per §22.1.)"""
    from pdp11.devices.rx11 import SECTOR_BYTES
    from pdp11.disk_images.container import open_rx01
    img_path = str(tmp_path / "boot.rx01")
    image = open_rx01(img_path)
    sector = bytes((i * 3) & 0xFF for i in range(SECTOR_BYTES))
    image.write_sector(0, sector)                        # track 0, sector 1
    image.flush()

    args = build_arg_parser().parse_args(["--machine", "11/03", "--disk", img_path])
    board = assemble(args, ConsoleTerminal(FakeBackend()))
    program = {
        0o1000: [0o032737, 0o000040, 0o177170],  # w0: BIT #DONE,@#RXCS (wait ready)
        0o1006: [0o001774],                      # BEQ w0
        0o1010: [0o012737, 0o000107, 0o177170],  # MOV #READ|IE|GO,@#RXCS
        0o1016: [0o032737, 0o000200, 0o177170],  # p1: BIT #TR,@#RXCS
        0o1024: [0o001774],                      # BEQ p1
        0o1026: [0o012737, 0o000001, 0o177172],  # MOV #1,@#RXDB (sector 1)
        0o1034: [0o032737, 0o000200, 0o177170],  # p2: BIT #TR,@#RXCS
        0o1042: [0o001774],                      # BEQ p2
        0o1044: [0o012737, 0o000000, 0o177172],  # MOV #0,@#RXDB (track 0)
        0o1052: [0o032737, 0o000040, 0o177170],  # p3: BIT #DONE,@#RXCS
        0o1060: [0o001774],                      # BEQ p3
        0o1062: [0o012737, 0o000003, 0o177170],  # MOV #EMPTY|GO,@#RXCS
        0o1070: [0o012700, 0o004000],            # MOV #0o4000,R0
        0o1074: [0o012701, 0o000200],            # MOV #128,R1
        0o1100: [0o032737, 0o000200, 0o177170],  # p4: BIT #TR,@#RXCS
        0o1106: [0o001774],                      # BEQ p4
        0o1110: [0o113720, 0o177172],            # MOVB @#RXDB,(R0)+
        0o1114: [0o005301],                      # DEC R1
        0o1116: [0o001370],                      # BNE p4
        0o1120: [0o000000],                      # HALT
        0o1200: [0o012737, 0o000006, 0o177170],  # handler: MOV #READ,@#RXCS (IE off)
        0o1206: [0o000002],                      # RTI
        0o0264: [0o001200],                      # vector 264 -> 0o1200
        0o0266: [0o000240],                      #   new PSW: IPL 5
    }
    for addr, ws in program.items():
        for i, w in enumerate(ws):
            board.ram.write_word(addr + 2 * i, w)
    cpu = board.cpu
    cpu.state.R[Reg.SP] = 0o17776
    cpu.state.R[Reg.PC] = 0o1000
    steps = 0
    while not cpu.state.halted and steps < 20000:
        cpu.step()
        steps += 1
    assert cpu.state.halted, "program did not reach HALT"
    got = bytes(board.ram.read_byte(0o4000 + i) for i in range(SECTOR_BYTES))
    assert got == sector                              # track 0 sector 1 landed in RAM


# -- M5: 11/70 assembly, Massbus (RH70+RP06), MMU boot path (spec §12) ------------


def test_assemble_1170_devices():
    """11/70: Unibus arbiter + MMU + Unibus map; DL11/KW11L/RH70 attach in
    profile order (tm11 is not implemented until its milestone and is
    skipped with a log note)."""
    args = build_arg_parser().parse_args(["--machine", "11/70"])
    board = assemble(args, ConsoleTerminal(FakeBackend()))
    assert board.unibus is not None and board.qbus is None
    assert board.mmu is not None and board.map is not None
    assert [d.name for d in board.devices] == ["DL11", "KW11L", "RH70"]


def test_assemble_1170_rp06_disk(tmp_path):
    """--disk with an RP06-sized container attaches an RP06 drive to the RH70
    at 176700 (§16 dispatch)."""
    from pdp11.disk_images.container import RP06_CYLINDERS, open_rp06
    img = str(tmp_path / "rp06.dsk")
    open_rp06(img).flush()
    args = build_arg_parser().parse_args(["--machine", "11/70", "--disk", img])
    board = assemble(args, ConsoleTerminal(FakeBackend()))
    rh = next(d for d in board.devices if d.name == "RH70")
    assert rh.drive.cylinders == RP06_CYLINDERS      # type: ignore[attr-defined]


def test_1170_builtin_bootstrap_and_caret_odt():
    """11/70 power-on (§13): the built-in bootstrap at 173000 prints the
    banner through the console DL11 and HALTs into ODT, which shows the
    `^ address` display on MMU machines."""
    board, backend = make_board("11/70", input_bytes=b"Q\n")
    assert board.rom is not None and board.rom_base == BOOT_BASE
    rc = run(board, ConsoleTerminal(backend))
    assert rc == 0
    assert "non-authentic" in backend.text()
    assert "^" in backend.text()


def test_1170_mmu_on_program_path():
    """Hand-assembled 11/70 program: map the kernel APRs, enable the MMU
    (MMR0 MME|TENB), then store through the mapping — the word must land at
    the physical page from KDPAR2, proving translation is active (§5.7)."""
    from pdp11.tests.cpu_golden import make_machine, step_n
    program: list[int] = []

    def mov_abs(value: int, addr: int) -> None:      # MOV #value,@#addr
        program.extend([0o012737, value, addr])

    mov_abs(0, 0o172340)                             # KIPAR0 = 0 (identity)
    mov_abs(0o077406, 0o172300)                      # KIPDR0: RW full page
    mov_abs(0o2000, 0o172344)                        # KIPAR2 -> PA 0o200000
    mov_abs(0o077406, 0o172304)                      # KIPDR2
    mov_abs(0o177600, 0o172356)                      # KIPAR7 -> I/O page
    mov_abs(0o077406, 0o172316)                      # KIPDR7
    mov_abs(0, 0o172360)                             # KDPAR0
    mov_abs(0o077406, 0o172320)                      # KDPDR0
    mov_abs(0o2000, 0o172364)                        # KDPAR2 -> PA 0o200000
    mov_abs(0o077406, 0o172324)                      # KDPDR2
    mov_abs(0o177600, 0o172376)                      # KDPAR7
    mov_abs(0o077406, 0o172336)                      # KDPDR7
    mov_abs(0o5, 0o172516)                           # MMR3: UDS|KDS
    mov_abs(0o1001, 0o177572)                        # MMR0: MME|TENB
    mov_abs(0o1234, 0o040000)                        # store through the map (page 2)
    program.append(0o000000)                         # HALT
    cpu, board = make_machine("11/70", ram_words=0o200000, program=program, pc=0o100)
    step_n(cpu, 20)
    assert cpu.state.halted
    assert board.mmu is not None and board.mmu.enabled
    assert board.ram.read_word(0o200000) == 0o1234


# -- M6: 11/94 (DCJ-11) assembly, FPJ, Q22-CD map (spec §12) ---------------------


def test_assemble_1194_devices():
    """11/94: Q-Bus arbiter + MMU (J-11 flavor) + Q22-CD map; DL11/KW11L/RK11
    attach (tqk11 is not implemented until its milestone and is skipped)."""
    args = build_arg_parser().parse_args(["--machine", "11/94"])
    board = assemble(args, ConsoleTerminal(FakeBackend()))
    assert board.qbus is not None and board.unibus is None
    assert board.qbus.addr_mask == 0o17777777              # 22-bit
    assert board.mmu is not None and board.map is not None
    assert [d.name for d in board.devices] == ["DL11", "KW11L", "RK11"]


def test_1194_builtin_bootstrap_and_caret_odt():
    """11/94 power-on (§13): built-in bootstrap at 173000 prints via DL11,
    HALTs into ODT with the `^ address` display (has_mmu)."""
    board, backend = make_board("11/94", input_bytes=b"Q\n")
    rc = run(board, ConsoleTerminal(backend))
    assert rc == 0
    assert "non-authentic" in backend.text()
    assert "^" in backend.text()


def test_1194_fpj_instructions():
    """The J-11 FPJ is FP11-compatible at the ISA level (§5.8): LDF/ADDF/STF
    through the 0o17xxxx group work on the 11/94 (FPU cap on-chip)."""
    from fractions import Fraction

    from pdp11.cpu.fp11 import P_F
    from pdp11.tests.cpu_golden import make_machine, step_n
    # SETF ; LDF AC0,@#2000 ; ADDF AC0,@#2004 ; STF AC0,@#2010 ; HALT
    cpu, board = make_machine(
        "11/94",
        program=[0o170001, 0o172437, 0o2000, 0o172037, 0o2004, 0o174037, 0o2010, 0o000000],
        pc=0o100)
    board.ram.write_word(0o2002, 0o040000)                 # 1.0F
    board.ram.write_word(0o2006, 0o040200)                 # 2.0F
    step_n(cpu, 5)
    assert cpu.state.halted
    assert cpu.state.fp._value(cpu.state.fp.ac[0], P_F) == Fraction(3)
    assert board.ram.read_word(0o2012) == 0o40300          # 3.0F high word


def test_1194_mmu_on_program_path():
    """11/94 with the MMU on (J-11: MMR0 MME|DSE) — a store through the
    kernel D-space APRs lands at the KDPAR physical page."""
    from pdp11.tests.cpu_golden import make_machine, step_n
    program: list[int] = []

    def mov_abs(value: int, addr: int) -> None:
        program.extend([0o012737, value, addr])

    mov_abs(0, 0o172340)                             # KIPAR0 = 0
    mov_abs(0o077406, 0o172300)                      # KIPDR0
    mov_abs(0, 0o172360)                             # KDPAR0 = 0 (vector fetch)
    mov_abs(0o077406, 0o172320)                      # KDPDR0
    mov_abs(0o2000, 0o172364)                        # KDPAR2 -> PA 0o200000
    mov_abs(0o077406, 0o172324)                      # KDPDR2
    mov_abs(0o177600, 0o172356)                      # KIPAR7 -> I/O page
    mov_abs(0o077406, 0o172316)                      # KIPDR7
    mov_abs(0o177600, 0o172376)                      # KDPAR7
    mov_abs(0o077406, 0o172336)                      # KDPDR7
    mov_abs(0o011, 0o177572)                         # MMR0: MME | DSE (bit 3)
    mov_abs(0o1234, 0o040000)                        # store through D-space map
    program.append(0o000000)                         # HALT
    cpu, board = make_machine("11/94", ram_words=0o200000, program=program, pc=0o100)
    step_n(cpu, 15)
    assert cpu.state.halted
    assert board.mmu is not None and board.mmu.enabled
    assert board.ram.read_word(0o200000) == 0o1234


# -- M7: --console frontends + terminal wiring (spec §8, §12 M7) ----------------------


def test_console_choices_wire_frontends():
    """--console {vt100,vt52,la36,odt} selects the TerminalFrontend (§8): the
    escape-sequence terminals of graphics/vt100.py + la36.py, or the plain
    console. Every choice assembles and HALTs into ODT on the 11/20."""
    from pdp11.main import make_terminal
    for choice, name in (("vt100", "vt100"), ("vt52", "vt52"),
                         ("la36", "la36"), ("odt", "console")):
        backend = FakeBackend(b"Q\n")
        term = make_terminal(choice, backend)
        assert term.name == name
        args = build_arg_parser().parse_args(["--machine", "11/20", "--console", choice])
        board = assemble(args, term)
        assert run(board, term) == 0
        if choice == "la36":
            assert backend.text() == ""            # hardcopy buffers until a line LF
        else:
            assert "@" in backend.text()           # ODT prompt reached the host


def test_1103_bootstrap_via_default_vt100_frontend():
    """Default --console vt100 (§8): the 11/03 bootstrap banner flows through
    the VT100 state machine — printables/CR/LF are forwarded to the host, so
    the banner text is visible on a plain backend. (Input leads with a filler
    byte: during RUN the DL11 input pump consumes one queued byte into RCVBUF,
    as the real console receiver would; the Q for ODT follows it.)"""
    backend = FakeBackend(b"\nQ\n")
    rc = main(["--machine", "11/03"], backend=backend)
    assert rc == 0
    assert "non-authentic" in backend.text()
    assert "@" in backend.text()                   # ODT prompt after HALT


def test_1103_bootstrap_via_la36_frontend():
    """--console la36: the banner prints line-by-line (buffered until LF) —
    a hardcopy device, not a screen (§8)."""
    backend = FakeBackend(b"\nQ\n")                # see note above: pump eats one byte
    rc = main(["--machine", "11/03", "--console", "la36"], backend=backend)
    assert rc == 0
    assert "non-authentic" in backend.text()
    assert "\r\n" in backend.text()                # lines flushed with CR LF


# -- M8: power-fail/restore (vector 000024, §5.5/§11.9) + snapshot CLI (§15) -------


def test_powerfail_and_restore_trap_vector_024():
    """§11.9 shape: injected --powerfail/--powerrestore events trap through
    vector 000024; the handler runs once per injection and the machine keeps
    running; two fresh runs are cycle-identical (deterministic injection)."""
    from pdp11.tests.cpu_golden import make_machine
    handler = [0o012737, 0o000001, 0o002000,       # MOV #1,@#0o2000  (flag)
               0o005237, 0o002002,                 # INC @#0o2002      (count)
               0o000002]                           # RTI

    def build_and_run():
        cpu, board = make_machine("11/20", program=[0o000777], pc=0o1000)
        for i, w in enumerate(handler):
            board.ram.write_word(0o1100 + 2 * i, w)
        board.ram.write_word(0o0024, 0o001100)     # vector 000024 -> handler
        board.ram.write_word(0o0026, 0o000340)     #   new PSW: IPL 7
        board.clock.schedule(0o2000, cpu.power_fail_trap)    # --powerfail
        board.clock.schedule(0o4000, cpu.power_fail_trap)    # --powerrestore
        steps = 0
        while steps < 3000:
            cpu.step()
            steps += 1
        return board

    b1 = build_and_run()
    b2 = build_and_run()
    assert b1.ram.read_word(0o002000) == 1         # handler ran
    assert b1.ram.read_word(0o002002) == 2         # once per injection (fail + restore)
    assert b1.clock.cycle == b2.clock.cycle        # deterministic
    assert not b1.cpu.state.halted                 # machine still running


def test_save_restore_powerflag_maxspeed_cli(tmp_path):
    """M8 CLI wiring: --save writes a §15 snapshot on exit; --restore loads it;
    --powerfail/--powerrestore/--maxspeed parse (octal cycles, §10.1)."""
    import json

    import pytest

    snap = tmp_path / "s.json"
    rc = main(["--machine", "11/20", "--save", str(snap),
               "--powerfail", "0o100", "--powerrestore", "0o200", "--maxspeed"],
              backend=FakeBackend(b"Q\n"))
    assert rc == 0 and snap.exists()
    wrapper = json.loads(snap.read_text(encoding="utf-8"))
    assert wrapper["format"] == "eleven13-snapshot" and wrapper["profile"] == "11/20"
    assert wrapper["version"] == 1 and isinstance(wrapper["core"], str)
    rc = main(["--machine", "11/20", "--restore", str(snap)], backend=FakeBackend(b"Q\n"))
    assert rc == 0
    # restoring a snapshot into a different profile is user-facing misuse (§18.3)
    with pytest.raises(ValueError, match="profile"):
        main(["--machine", "11/03", "--restore", str(snap)], backend=FakeBackend(b"Q\n"))
