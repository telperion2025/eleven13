"""
Boot ROM loader (spec §9, §13) — M2.

Loads M9312 512-word boot PROM images so any of the four machines can run
real DEC boot ROMs (173000-series: XXDP boot, TU tape boot, RP/RM disk
boot). `--bootrom <file>` supplies the image as 512 little-endian 16-bit
words (1024 bytes). Without it, ROM-boot machines (11/03, 11/70, 11/94 —
power_on_pc 0o173000) fall back to a simplified built-in bootstrap, clearly
documented as NON-AUTHENTIC (§13): it just prints a banner through the
console DL11 and HALTs. The 11/20 has no ROM: it starts at 000000 in
operator-deposited RAM (§13) and drops straight to ODT.

References: M9312 bootstrap/terminator module documentation (§21).
"""

from __future__ import annotations

import logging

from pdp11.memory.rom import ROM

_log = logging.getLogger(__name__)

PROM_WORDS = 0o1000          # M9312 PROM = 512 words = 0o1000 octal
BOOT_BASE = 0o173000         # 173000-series boot ROM window (§13)

XMTSTAT = 0o177564           # console DL11 registers (§7)
XMTBUF = 0o177566
DONE = 0o000200


def load_prom(path: str) -> ROM:
    """Load a 512-word M9312 PROM image (1024 bytes, little-endian words)."""
    data = open(path, "rb").read()                       # user-facing error if missing
    if len(data) != PROM_WORDS * 2:
        raise ValueError(
            f"boot PROM {path} must be exactly {PROM_WORDS} words ({PROM_WORDS * 2} bytes), "
            f"got {len(data)} bytes")
    words = [data[2 * i] | (data[2 * i + 1] << 8) for i in range(PROM_WORDS)]
    rom = ROM(PROM_WORDS)
    rom.load_image(words)
    _log.info("loaded M9312 boot PROM %s (%d words)", path, PROM_WORDS)
    return rom


def builtin_bootstrap(message: str | None = None) -> ROM:
    """The simplified built-in bootstrap (§13) — NON-AUTHENTIC fallback used
    when no --bootrom image is given. Hand-assembled print loop: for each
    message byte, MOV #ch,R0 ; MOV R0,@#XMTBUF ; poll XMTSTAT DONE ; then HALT."""
    if message is None:
        message = "eleven13 built-in bootstrap (non-authentic)\r\n"  # fits 512-word PROM
    words: list[int] = []
    for ch in message:
        words += [0o012700, ord(ch) & 0o377]        # MOV #ch, R0
        words += [0o010037, XMTBUF]                 # MOV R0, @#XMTBUF
        wait = len(words)                           # label: poll loop head
        words += [0o013701, XMTSTAT]                # MOV @#XMTSTAT, R1
        words += [0o032701, DONE]                   # BIT #DONE, R1 (mode 2 = immediate)
        offset = wait - (len(words) + 1)            # BEQ displacement (words, signed byte)
        words.append(0o001400 + (offset & 0o377))   # BEQ wait
    words.append(0o000000)                          # HALT -> ODT (§9)
    rom = ROM(PROM_WORDS)
    rom.load_image(words)
    return rom


def attach_boot_rom(board, path: str | None) -> None:
    """Wire the boot ROM at octal 173000: the --bootrom image if given, else
    the built-in non-authentic bootstrap (§13). Only for ROM-boot machines."""
    from pdp11.board import SystemBoard  # typing aid only
    assert isinstance(board, SystemBoard)
    if board.profile.power_on_pc != BOOT_BASE:
        if path:
            _log.warning("--bootrom ignored: %s has no boot ROM (power-on PC %o, §13)",
                         board.profile.key, board.profile.power_on_pc)
        return
    rom = load_prom(path) if path else builtin_bootstrap()
    board.attach_rom(BOOT_BASE, rom)


__all__ = ["PROM_WORDS", "BOOT_BASE", "load_prom", "builtin_bootstrap", "attach_boot_rom"]
