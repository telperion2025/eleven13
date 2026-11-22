"""
KW11-P programmable clock (spec §7, P2; 11/70, 11/94).

Register (octal): LKS-style CSR at 170744 region per the Peripherals
Handbook — programmable interval, interrupt on done.

M6 stub per §19.10.

References: PDP-11 Peripherals Handbook, KW11-P section (§21).
"""

from __future__ import annotations


class KW11P:
    """Programmable clock. M6: interval register + scheduled interrupts."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 KW11-P programmable clock — M6")
