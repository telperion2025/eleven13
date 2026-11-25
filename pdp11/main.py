"""
Emulator entry point and CLI.

Responsibilities (ONLY these, per §4.2 — main.py is a thin shell):
    1. parse CLI arguments,
    2. look up the MachineProfile,
    3. assemble the machine (clock, memory bus, io_page, devices, CPU),
    4. hand control to the run loop,
    5. apply real-time throttling HERE, never inside the machine.

Sample:
    eleven13 --machine 11/20 --debug          # console script (pyproject entry point)
    python -m pdp11.main --machine 11/20 --debug  # module form, same effect

M2: real assembly — SystemBoard + console DL11 + boot ROM (§13) + ODT shell
on HALT (§9). Wall-clock throttling lives only in run() below (§19.6).
M3: KW11-L line clock and RK11/RK05 with --disk containers (§16) attach
through the same registry.
M4: 11/03 Q-Bus arbiter (bus/qbus.py), DZ11 at 160100, RX11/RX01 at 177170;
--disk dispatches by container size (RK05 -> RK11, RX01 -> RX11, §11.2/§11.3).
M5: 11/70 Massbus — RH70 at 176700 with an RP06/RP07 drive (devices/
rh70_rp.py); --disk dispatches RP06/RP07-sized containers to the RH70 (§16).
DMA devices get board.dma_host (Unibus map applied on the 11/70) and the
RH70 gets board.massbus_host (22-bit physical direct, §6.1).
M7: --console selects the virtual terminal (vt100/vt52/la36 escape-sequence
frontends, graphics/vt100.py + la36.py, or the plain odt console) via
make_terminal() below; --debug runs the extended debugger (control/debugger.py,
§9) which supervises RUN (breakpoints/watchpoints) and owns the HALT shell.
M8: --save/--restore are the §15 session snapshot CLI (control/snapshot.py);
--powerfail/--powerrestore inject the vector-000024 sequence (§5.5) at the
given bus cycles; --maxspeed is the explicit no-throttle spelling of the
default (§14).
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any

from pdp11.board import SystemBoard
from pdp11.bus.massbus import RH70_BASE
from pdp11.config.profiles import PROFILES, BusType, MachineProfile
from pdp11.control.debugger import Debugger, crash_dump
from pdp11.control.front_panel import ODT, FrontPanel
from pdp11.control.rom_loader import attach_boot_rom
from pdp11.control.snapshot import load_snapshot, restore_state, save_snapshot
from pdp11.devices.dl11 import DL11
from pdp11.devices.dz11 import DZ11
from pdp11.devices.kw11l import KW11L
from pdp11.devices.kw11p import KW11P
from pdp11.devices.pc11 import PC11
from pdp11.devices.rh70_rp import RH70, RP06, RP07
from pdp11.devices.rk11_rk05 import RK05, RK11
from pdp11.devices.rl11 import RL11, RLDrive
from pdp11.devices.rx11 import RX11
from pdp11.devices.tm11 import TM11
from pdp11.devices.tqk11 import TQK11
from pdp11.disk_images.container import (
    RK05_SECTOR,
    RP06_SECTORS,
    RP07_SECTORS,
    RP_SECTOR,
    RX01_SECTOR,
    PtImage,
    TapImage,
    open_disk_image,
    open_rl,
)
from pdp11.errors import EmulationError
from pdp11.graphics.la36 import LA36Frontend
from pdp11.graphics.render_backend import RenderBackend, StdioBackend
from pdp11.graphics.terminal import ConsoleTerminal, TerminalFrontend
from pdp11.graphics.vt100 import VT52Frontend, VT100Frontend
from pdp11.system_clock import SystemClock

_log = logging.getLogger(__name__)

BUS_CYCLE_NS = 150  # nominal Unibus cycle (§5.6) — used ONLY for --throttle pacing

# Device registry: names from profile.default_devices -> (base address, factory).
# Devices whose milestone hasn't landed are skipped with an INFO note (§19.10).
CONSOLE_DL11_BASE = 0o177560
KW11L_BASE = 0o177546
KW11P_BASE = 0o172540
RK11_BASE = 0o177400
RL11_BASE = 0o174400
RX11_BASE = 0o177170
DZ11_BASE = 0o160100
TM11_BASE = 0o172520
TQK11_BASE = 0o174500
PC11_BASE = 0o177550
# RH70_BASE imported from bus.massbus (0o176700)


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI surface per §4.1 — --machine has NO default (behavior differs
    fundamentally across machines; guessing a default silently would
    betray the profile-driven design)."""
    p = argparse.ArgumentParser(prog="eleven13", description="DEC PDP-11 emulator")
    p.add_argument("--machine", required=True, choices=sorted(PROFILES.keys()),
                   help="which machine to emulate (no default: see spec §4.1)")
    p.add_argument("--memory", type=lambda s: int(s, 8), metavar="OCT_WORDS",
                   help="RAM size in octal words, overriding the profile max")
    p.add_argument("--disk", action="append", metavar="IMAGE",
                   help="attach a disk image (format detected per §16: RK05/RX01/"
                        "RP06/RP07/RL01/RL02)")
    p.add_argument("--rl", action="append", metavar="IMAGE",
                   help="attach an RL01/RL02 image (raw 256-byte sectors, "
                        "autosized per §16) to the RL11")
    p.add_argument("--tape", action="append", metavar="TAP",
                   help="attach a SIMH .tap magtape (§16) to TM11/TQK11")
    p.add_argument("--papertape", action="append", metavar="PT",
                   help="attach a PC11 paper tape (.pt raw byte stream, §16)")
    p.add_argument("--console", choices=["vt100", "vt52", "la36", "odt"],
                   default="vt100", help="terminal frontend (§8)")
    p.add_argument("--bootrom", metavar="FILE",
                   help="M9312 boot PROM image — §13")
    p.add_argument("--config", metavar="FILE.json",
                   help="per-run JSON overrides layered over the profile — §4.1")
    p.add_argument("--throttle", action="store_true",
                   help="slow execution to real-machine speed (default: full)")
    p.add_argument("--maxspeed", action="store_true",
                   help="explicit full speed — the default behavior (§14)")
    p.add_argument("--debug", action="store_true",
                   help="extended debugger on HALT (§9)")
    p.add_argument("--save", metavar="FILE",
                   help="write a §15 session snapshot to FILE on exit")
    p.add_argument("--restore", metavar="FILE",
                   help="restore a §15 session snapshot from FILE at startup")
    p.add_argument("--powerfail", nargs="?", const="0o100000", default=None,
                   metavar="OCT_CYCLES",
                   help="inject the power-fail trap (vector 000024, §5.5) at OCT_CYCLES "
                        "(default 0o100000)")
    p.add_argument("--powerrestore", nargs="?", const="0o200000", default=None,
                   metavar="OCT_CYCLES",
                   help="inject the power-restore restart (vector 000024, §5.5) at "
                        "OCT_CYCLES (default 0o200000)")
    return p


def _load_config_overrides(path: str | None) -> dict[str, Any]:
    """--config <json>: per-run overrides (memory_words, devices, volumes)."""
    if not path:
        return {}
    with open(path, encoding="utf-8") as fh:      # user-facing error if unreadable
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"config {path} must be a JSON object")
    return data


def make_terminal(choice: str, backend: RenderBackend) -> TerminalFrontend:
    """--console selection (§8): the DL11 talks to a virtual terminal through
    the TerminalFrontend interface — VT100 (default), VT52, or the LA36
    hardcopy; "odt" keeps the plain text console. Only render_backend.py and
    main.py touch host I/O (§8/§19.6)."""
    if choice == "vt100":
        return VT100Frontend(backend)
    if choice == "vt52":
        return VT52Frontend(backend)
    if choice == "la36":
        return LA36Frontend(backend)
    return ConsoleTerminal(backend)             # "odt": plain console


def assemble(args: argparse.Namespace, terminal: TerminalFrontend) -> SystemBoard:
    """Build the machine from the profile + CLI/config overrides (§4.2)."""
    profile: MachineProfile = PROFILES[args.machine]
    overrides = _load_config_overrides(args.config)
    clock = SystemClock()                          # §5.6: deterministic scheduler
    ram_words: int | None = args.memory            # --memory OCT_WORDS (octal, §10.1)
    if ram_words is None and "memory_words" in overrides:
        ram_words = int(str(overrides["memory_words"]), 8)   # config.json override
    board = SystemBoard(profile, clock, ram_words=ram_words)

    # boot ROM (M9312 image or the documented non-authentic fallback, §13)
    attach_boot_rom(board, args.bootrom)

    # default devices: attach the implemented ones in profile order (slot
    # order = daisy-chain priority, §6.1); skip not-yet-implemented ones.
    requested = overrides.get("devices", profile.default_devices)
    disks = [open_disk_image(p) for p in (args.disk or [])]   # containers (§16)
    rk_images = [d for d in disks if d.sector_size == RK05_SECTOR
                 and d.sector_count == 4879]
    rx_images = [d for d in disks if d.sector_size == RX01_SECTOR]
    rp_images = [d for d in disks if d.sector_size == RP_SECTOR
                 and d.sector_count in (RP06_SECTORS, RP07_SECTORS)]
    rl_images = [open_rl(p) for p in (args.rl or [])]        # RL packs (§16)
    tapes = [TapImage(p) for p in (args.tape or [])]          # .tap (§16)
    paper_tapes = [PtImage(p) for p in (args.papertape or [])]  # .pt (§16)
    rk_attached = False
    rx_attached = False
    rh_attached = False
    rl_attached = False
    tm_attached = False
    tqk11_attached = False
    pc_attached = False
    for name in requested:
        if name == "dl11_console":
            board.attach_device(CONSOLE_DL11_BASE, DL11(clock, terminal))
        elif name == "kw11l":
            board.attach_device(KW11L_BASE, KW11L(clock))       # §5.6 line clock (M3)
        elif name == "dz11":
            # BIRQ4 on Q-Bus, BR5 on Unibus (E11 default; flagged §22.4)
            br = 4 if profile.bus is BusType.QBUS else 5
            board.attach_device(DZ11_BASE, DZ11(clock, br_level=br))
        elif name == "rk11_rk05":
            image = rk_images.pop(0) if rk_images else None
            board.attach_device(RK11_BASE, RK11(clock, board.dma_host, RK05(image)))
            rk_attached = True
        elif name == "rx11":
            image = rx_images.pop(0) if rx_images else None
            board.attach_device(RX11_BASE, RX11(clock, image))
            rx_attached = True
        elif name == "rh70_rp":
            image = rp_images.pop(0) if rp_images else None
            drive = (RP07(image) if image is not None and image.sector_count == RP07_SECTORS
                     else RP06(image))
            board.attach_device(RH70_BASE, RH70(clock, board.massbus_host, drive))
            rh_attached = True
        elif name == "tm11":
            tape_img = tapes.pop(0) if tapes else None
            board.attach_device(TM11_BASE, TM11(clock, board.dma_host, tape_img))
            tm_attached = True
        elif name == "tqk11":
            tape_img = tapes.pop(0) if tapes else None
            board.attach_device(TQK11_BASE, TQK11(clock, board.dma_host, tape_img))
            tqk11_attached = True
        elif name == "rl11":
            rl_img = rl_images.pop(0) if rl_images else None
            board.attach_device(RL11_BASE, RL11(clock, board.dma_host, RLDrive(rl_img)))
            rl_attached = True
        elif name == "pc11":
            pt = paper_tapes.pop(0) if paper_tapes else None
            board.attach_device(PC11_BASE, PC11(clock, pt))
            pc_attached = True
        elif name == "kw11p":
            board.attach_device(KW11P_BASE, KW11P(clock))
        else:
            _log.info("device %r not implemented until its milestone — skipping", name)
    # leftover --disk images attach by container format (§16): RK05 -> RK11,
    # RX01 -> RX11, RP06/RP07 -> RH70 — even on profiles whose defaults don't
    # list them (§11.2/§11.3)
    for image in rk_images:
        if not rk_attached:
            board.attach_device(RK11_BASE, RK11(clock, board.dma_host, RK05(image)))
            rk_attached = True
        else:
            _log.info("extra RK05 image %s: v1 models drive unit 0 only — ignored", image.path)
    for image in rx_images:
        if not rx_attached:
            board.attach_device(RX11_BASE, RX11(clock, image))
            rx_attached = True
        else:
            _log.info("extra RX01 image %s: v1 models drive 0 only — ignored", image.path)
    for image in rp_images:
        if not rh_attached:
            drive = RP07(image) if image.sector_count == RP07_SECTORS else RP06(image)
            board.attach_device(RH70_BASE, RH70(clock, board.massbus_host, drive))
            rh_attached = True
        else:
            _log.info("extra RP image %s: v1 models one Massbus drive — ignored", image.path)
    for image in rl_images:
        if not rl_attached:
            board.attach_device(RL11_BASE, RL11(clock, board.dma_host, RLDrive(image)))
            rl_attached = True
        else:
            _log.info("extra RL image %s: v1 models drive unit 0 only — ignored", image.path)
    for tape_img in tapes:
        if not tm_attached and profile.bus is BusType.UNIBUS:
            board.attach_device(TM11_BASE, TM11(clock, board.dma_host, tape_img))
            tm_attached = True
        elif not tqk11_attached:
            board.attach_device(TQK11_BASE, TQK11(clock, board.dma_host, tape_img))
            tqk11_attached = True
        else:
            _log.info("extra tape %s: v1 models one tape drive — ignored", tape_img.path)
    for pt in paper_tapes:
        if not pc_attached:
            board.attach_device(PC11_BASE, PC11(clock, pt))
            pc_attached = True
        else:
            _log.info("extra paper tape %s: v1 models one PC11 — ignored", pt.path)
    # M8: --restore loads a §15 snapshot AFTER assembly (devices attached), so
    # the saved device/queue state maps onto the freshly attached windows.
    if args.restore:
        restore_state(board, load_snapshot(args.restore))
        _log.info("restored snapshot %s (profile %s, cycle %d)",
                  args.restore, board.profile.key, board.clock.cycle)
    return board


def run(board: SystemBoard, terminal: TerminalFrontend, throttle: bool = False,
        debug: bool = False, powerfail: int | None = None,
        powerrestore: int | None = None, save: str | None = None) -> int:
    """Run loop: execute until HALT, then hand to ODT (§9) — or to the extended
    debugger with --debug, which also supervises RUN (breakpoints/watchpoints,
    §9). Returns exit code. Wall-clock throttling exists ONLY here, outside
    the deterministic core (§19.6). --powerfail/--powerrestore schedule the
    vector-000024 injection events (§5.5) on the deterministic clock; --save
    writes the §15 snapshot when the session ends."""
    cpu = board.cpu
    panel = FrontPanel(board)
    dbg = Debugger(board, terminal) if debug else None
    if powerfail is not None:
        board.clock.schedule(powerfail, cpu.power_fail_trap)      # vector 000024 (§5.5)
    if powerrestore is not None:
        board.clock.schedule(powerrestore, cpu.power_fail_trap)
    t0 = time.monotonic()
    while True:
        while not cpu.state.halted:
            if dbg is not None:
                if not dbg.supervised_step():      # breakpoint/watchpoint fired
                    panel.halt()
                    break
            else:
                cpu.step()
            if terminal.break_pending():          # BREAK key -> HALT (§13)
                panel.halt()
            if throttle:                           # §14/§19.6: pace to 150 ns/cycle
                target = t0 + board.clock.cycle * BUS_CYCLE_NS / 1e9
                delay = target - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        shell: ODT = dbg if dbg is not None else ODT(board, terminal)
        action = shell.run()                           # HALT -> ODT/debugger (§9)
        if action == "quit":
            if dbg is not None:
                # §14 instrumentation: ips computed HERE at the §19.6 boundary
                dbg.report_perf(time.monotonic() - t0)
            if save is not None:
                save_snapshot(board, save)            # §15 snapshot on exit
                _log.info("snapshot saved to %s (cycle %d)", save, board.clock.cycle)
            return 0
        panel.run()                                    # G -> RUN (§13)


def main(argv: list[str] | None = None, backend: RenderBackend | None = None) -> int:
    """Assemble the chosen machine and run until HALT/error."""
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    host = backend if backend is not None else StdioBackend()
    terminal = make_terminal(args.console, host)      # --console frontend (§8, M7)
    board = assemble(args, terminal)

    def _oct_cycles(value: str | None) -> int | None:
        return int(value, 8) if value is not None else None   # octal CLI (§10.1)

    try:
        return run(board, terminal, throttle=args.throttle, debug=args.debug,
                   powerfail=_oct_cycles(args.powerfail),
                   powerrestore=_oct_cycles(args.powerrestore),
                   save=args.save)
    except EmulationError as exc:
        # §18.3: crash loudly with the debugger's pasteable state dump
        dump = crash_dump(board, exc)
        for ch in dump:
            terminal.write(ord(ch))
        _log.error("EmulationError crash dump:\n%s", dump)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
