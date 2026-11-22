"""
DL11 serial line — the console DL11-E plus extras (spec §7, P0, all machines).

Registers (octal, console DL11 at 177560):
    RCVSTAT  177560  receiver status   (bit 7 = RCV DONE, bit 6 = RCV IE)
    RCVBUF   177562  receiver buffer   (read-only, clears DONE)
    XMTSTAT  177564  transmitter status (bit 7 = XMT DONE, bit 6 = XMT IE)
    XMTBUF   177566  transmitter buffer (write starts a transmit)
Byte access to either half of RBUF/XBUF is legal (§6.2).

Console traffic flows through graphics/terminal.py to a virtual terminal
frontend (§7); the VT100 state machine (not raw pass-through) is required
because RT-11/RSTS expect the escape semantics (§7, §8).

M2 stub per §19.10.

References: PDP-11 Peripherals Handbook, DL11 section (§21).
"""

from __future__ import annotations


class DL11:
    """DL11 serial line device. M2: full CSR + interrupt + terminal wiring."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 DL11 console + ODT support — M2")
