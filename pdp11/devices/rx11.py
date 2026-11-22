"""
RX01/RX02 floppy + RX11 controller (spec §7, P0; RX02 double-density is out
of scope for v1, §16).

Registers (octal, RX11 at 177170): RXCS control/status, RXDB data buffer,
RXES error/status, RXMP multipurpose, RXSA address.

RX01 container: raw 128-byte sector stream, 77 tracks x 26 sectors (§16).
RT-11 boots from an RX01 image on the 11/03 and 11/94 profiles (§11.2).

M3 stub per §19.10.

References: PDP-11 Peripherals Handbook, RX11 section (§21).
"""

from __future__ import annotations


class RX11:
    """RX11 floppy controller. M3: CSR file, DMA, RX01 image attach."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 RX01/RX11 — M3")
