"""
Unibus (spec §6.1) — 11/20 and 11/70 host bus.

Arbitration per §6.1: NPR (DMA) outranks BR (interrupt) grants; the
BR/BG daisy chain grants in slot order, modeled as the ordered device list
on the board. 18/22-bit... no: Unibus is 18-bit; the 11/70 reaches 22 bits
through the Unibus Map (memory/map_registers.py, M5). Cycle-stealing
mid-instruction is modeled at word-transfer granularity (§5.5).

M2 stub per §19.10: M1 runs the flat MemoryInterface in board.py; this
module takes over arbitration in M2.

References: PDP-11 Unibus Specification (DEC); PDP-11 Peripherals Handbook (§21).
"""

from __future__ import annotations


def _unibus_not_implemented() -> None:
    """Unibus arbitration (NPR/BR daisy chain) lands in M2 (§12)."""
    raise NotImplementedError("§6.1 Unibus NPR/BR arbitration — M2")
