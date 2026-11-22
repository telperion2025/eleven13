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
"""

from __future__ import annotations

import argparse
import sys

from pdp11.config.profiles import PROFILES
from pdp11.system_clock import SystemClock


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
    p.add_argument("--throttle", action="store_true",
                   help="slow execution to real-machine speed (default: full)")
    p.add_argument("--debug", action="store_true",
                   help="extended debugger on HALT (§9)")
    return p


def main(argv: list[str] | None = None) -> int:
    """Assemble the chosen machine and run until HALT/error."""
    args = build_arg_parser().parse_args(argv)
    profile = PROFILES[args.machine]          # KeyError impossible: argparse choices
    clock = SystemClock()                     # §5.6: deterministic scheduler

    # Placeholder machine assembly for M1 — fleshed out in M2 (SystemBoard):
    #   memory = RAM(profile.max_ram_words)
    #   iopage = IOPPage(profile.iopage_base)
    #   devices = [load_device(n)(...) for n in profile.default_devices]
    #   cpu = CPU(profile, clock); cpu.bus = memory_bus(memory, iopage, profile)
    print(f"pdp11 {profile.key} skeleton built (M1); run loop lands with M2 "
          f"(deterministic clock at cycle {clock.cycle})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
