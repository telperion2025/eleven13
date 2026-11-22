"""
RH70 Massbus controller + RP04/RP06/RP07 (and RM03) disk drives (spec §7,
P2; 11/70 only). RSTS/E and RSX-11M boot from an RP06 image through this
path on the 11/70 profile (§11.4).

The RH70 translates Unibus program I/O into Massbus register space
(bus/massbus.py); drives attach beneath it. RP06/RP07 containers are raw
512-byte sector streams, optionally with .hp-style headers (§16).

M5 stub per §19.10.

References: PDP-11/70 Processor Handbook Massbus chapter; RH70/RP06 manuals (§21).
"""

from __future__ import annotations


class RH70:
    """RH70 Massbus controller. M5."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 RH70 Massbus controller — M5 (11/70)")


class RPDrive:
    """RP04/RP06/RP07 drive unit behind an RH70. M5."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 RP04/RP06/RP07 drives — M5 (11/70)")
