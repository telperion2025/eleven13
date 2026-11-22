"""
DZ11 8-line multiplexer (spec §7, P1; 11/03, 11/70, 11/94).

Register window at octal 160500: CSR, RBUF/TBUF per line pair, with
interrupt vectors per line. Byte access to RBUF/TBUF halves is legal (§6.2).

M4 stub per §19.10.

References: PDP-11 Peripherals Handbook, DZ11 section (§21).
"""

from __future__ import annotations


class DZ11:
    """DZ11 8-line multiplexer. M4: per-line state, BISA-style vectors."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 DZ11 multiplexer — M4")
