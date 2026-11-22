"""
RL01/RL02 disk + RL11 controller (spec §7, P1; 11/03, 11/70, 11/94).

Registers (octal, RL11 at 174400): RLCS control/status, RLBA bus address,
RLDA disk address, RLMP multipurpose, RLWC word count, RLDA... per the
Peripherals Handbook RL11 register table.

M4 stub per §19.10.

References: PDP-11 Peripherals Handbook, RL11 section (§21).
"""

from __future__ import annotations


class RL11:
    """RL11 controller with RL01/RL02 drives. M4."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("§7 RL01/RL02 + RL11 — M4")
