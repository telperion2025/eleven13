"""
RK05 cartridge disk + RK11 controller emulation (spec §7, P0) — the
REFERENCE device implementation for all other drivers (§7).

Registers (octal offsets from base 177400):
    RKDS  drive status      (read-only, reset 000000)
    RKER  error register   (read/clear)
    RKCS  control/status   (read/write, reset 000000)
    RKWC  word count       (read/write)
    RKBA  bus address      (read/write)
    RKDA  disk address     (read/write)
    RKMR  maintenance       (unused, stubbed)

RK05 geometry: 2 surfaces x 203 cylinders x 12 sectors of 512 bytes =
4879 sectors, raw container, no header (§16). M3 lands the driver plus an
end-to-end "boot RT-11 from RK05" transcript in its tests (§7).

M3 stub per §19.10.

References:
    PDP-11 Peripherals Handbook (1976), RK11 section;
    RK11/RK05 engineering drawings EK-RK110-MM-001.
"""

from __future__ import annotations


class RK11:
    """RK11 controller. M3: full register file, transfer timing, errors."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 RK05/RK11 reference device — M3")


class RK05:
    """RK05 drive unit behind an RK11. M3: geometry, seeks, sector I/O."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 RK05 drive — M3")
