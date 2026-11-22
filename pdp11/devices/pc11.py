"""
PC11 paper tape reader/punch (spec §7, P0 on the 11/20 default set).

Registers (octal, PC11 at 177550): PRS reader status, PRB reader buffer,
PPS punch status, PPB punch buffer. Read-only in v1 (§16): the reader
works, the punch is stubbed.

Container: SIMH .pt format or raw byte stream (§16).

References: PDP-11 Peripherals Handbook, PC11 section (§21).
"""

from __future__ import annotations


class PC11:
    """PC11 paper tape reader/punch. M2: reader side (punch read-only in v1)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 PC11 paper tape — M2")
