"""
Q-Bus (spec §6.1) — 11/03 (18-bit, 16-bit DMA) and 11/94 (22-bit Q22-CD,
asynchronous).

Arbitration per §6.1: BIAK (interrupt acknowledge) and BDMG (DMA grant)
daisy chains; 18/22-bit system address space via the map registers
(memory/map_registers.py). The 11/94's asynchronous operation (§2) means
no fixed bus-cycle timing — modeled with the profile's quantum in M6.

M4/M6 stub per §19.10.

References: Q-Bus specification (DEC); LSI-11 and DCJ-11 (J-11) docs (§21).
"""

from __future__ import annotations


def _qbus_not_implemented() -> None:
    """Q-Bus arbitration (BIAK/BDMG, 18/22-bit mapping) lands in M4/M6 (§12)."""
    raise NotImplementedError("§6.1 Q-Bus BIAK/BDMG + 18/22-bit mapping — M4 (11/03) / M6 (11/94)")
