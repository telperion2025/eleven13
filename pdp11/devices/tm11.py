"""
TU10 magnetic tape + TM11 controller (spec §7, P0; 11/70 default set).

Registers (octal, TM11 at 172520): MTTC control, MTDS drive status,
MTER error, MTCA command, MTBA bus address, MTCN count, MTSR status.

Container: SIMH magtape .tap — 32-bit little-endian record header, raw
record bytes, trailing 32-bit length; zero-length record = EOF, zero header
= EOM (§16, hard requirement: tm11.py must read/write SIMH-created .tap
byte-compatibly). M3/M8 lands the driver and the round-trip tests (§11.10).

References: PDP-11 Peripherals Handbook, TM11 section; SIMH .tap format (§21).
"""

from __future__ import annotations


class TM11:
    """TM11 tape controller. M3: registers, tape motion model, .tap I/O."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 TU10/TM11 + SIMH .tap — M3 (format tests M8)")
