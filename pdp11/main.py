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
from pdp11.control.front_panel import ODT, FrontPanel
from pdp11.control.rom_loader import attach_boot_rom
from pdp11.devices.dl11 import DL11
from pdp11.devices.dz11 import DZ11
from pdp11.devices.kw11l import KW11L
from pdp11.devices.rh70_rp import RH70, RP06, RP07
from pdp11.devices.rk11_rk05 import RK05, RK11
from pdp11.devices.rx11 import RX11
from pdp11.disk_images.container import (
    RK05_SECTOR,
    RP06_SECTORS,
    RP07_SECTORS,
    RP_SECTOR,
    RX01_SECTOR,
    open_disk_image,
)
from pdp11.graphics.render_backend import RenderBackend, StdioBackend
from pdp11.graphics.terminal import ConsoleTerminal, TerminalFrontend
from pdp11.system_clock import SystemClock

_log = logging.getLogger(__name__)

BUS_CYCLE_NS = 150  # nominal Unibus cycle (§5.6) — used ONLY for --throttle pacing

# Device registry: names from profile.default_devices -> (base address, factory).
# Devices whose milestone hasn't landed are skipped with an INFO note (§19.10).
CONSOLE_DL11_BASE = 0o177560
KW11L_BASE = 0o177546
RK11_BASE = 0o177400
RX11_BASE = 0o177170
DZ11_BASE = 0o160100
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
                   help="attach a disk image (format detected per §16)")
    p.add_argument("--console", choices=["vt100", "vt52", "la36", "odt"],
                   default="vt100", help="terminal frontend (§8)")
    p.add_argument("--bootrom", metavar="FILE",
                   help="M9312 boot PROM image — §13")
    p.add_argument("--config", metavar="FILE.json",
                   help="per-run JSON overrides layered over the profile — §4.1")
    p.add_argument("--throttle", action="store_true",
                   help="slow execution to real-machine speed (default: full)")
    p.add_argument("--debug", action="store_true",
                   help="extended debugger on HALT (§9)")
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
    rk_images = [d for d in disks if d.sector_size == RK05_SECTOR]
    rx_images = [d for d in disks if d.sector_size == RX01_SECTOR]
    rp_images = [d for d in disks if d.sector_size == RP_SECTOR
                 and d.sector_count in (RP06_SECTORS, RP07_SECTORS)]
    rk_attached = False
    rx_attached = False
    rh_attached = False
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
    if args.console not in ("odt",):
        _log.info("--console %s: full terminal emulation lands M7; using plain console",
                  args.console)
    return board


def run(board: SystemBoard, terminal: TerminalFrontend, throttle: bool = False) -> int:
    """Run loop: execute until HALT, then hand to ODT (§9). Returns exit code.
    Wall-clock throttling exists ONLY here, outside the deterministic core."""
    cpu = board.cpu
    panel = FrontPanel(board)
    t0 = time.monotonic()
    while True:
        while not cpu.state.halted:
            cpu.step()
            if terminal.break_pending():          # BREAK key -> HALT (§13)
                panel.halt()
            if throttle:                           # §14/§19.6: pace to 150 ns/cycle
                target = t0 + board.clock.cycle * BUS_CYCLE_NS / 1e9
                delay = target - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        action = ODT(board, terminal).run()           # HALT -> ODT (§9)
        if action == "quit":
            return 0
        panel.run()                                    # G -> RUN (§13)


def main(argv: list[str] | None = None, backend: RenderBackend | None = None) -> int:
    """Assemble the chosen machine and run until HALT/error."""
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    terminal: TerminalFrontend = ConsoleTerminal(backend if backend is not None else StdioBackend())
    board = assemble(args, terminal)
    return run(board, terminal, throttle=args.throttle)


if __name__ == "__main__":
    raise SystemExit(main())
