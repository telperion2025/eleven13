"""
CPU registers and Program Status Word (spec §5.1).

The PDP-11's register file is unusual: R6 is the stack pointer and R7 is
the program counter, both addressed like general registers. That means
address-mode calculations run with the SAME hardware for data operands
and for PC-relative code — the core decoder depends on this symmetry.
"""

from __future__ import annotations

from array import array
from enum import IntEnum


class Reg(IntEnum):
    """Register indices; R6=SP and R7=PC per the PDP-11 Processor Handbook."""
    R0 = 0
    R1 = 1
    R2 = 2
    R3 = 3
    R4 = 4
    R5 = 5
    SP = 6   # a.k.a. R6: stack pointer (per-mode stacks on 11/70 & 11/94 only)
    PC = 7   # a.k.a. R7: program counter — also usable as an index register


class PSW:
    """
    The Program Status Word at octal 177776, low byte at 177777.

    Layout (16 bits):
        bit 15..14  current mode (00=kernel, 01=supervisor, 11=user)
        bit 13..12  previous mode        (kernel-only machines: hard 0)
        bit 11..8   register set (11/70+ only, not used in M1)
        bit  7..5   priority level (IPL 0-7)        — internal copies
        bit  4      T (trace trap after each instruction)
        bit  3      N (negative)
        bit  2      Z (zero)
        bit  1      V (overflow)
        bit  0      C (carry)
    The 11/20 has no mode bits: those read as 0 and writes are ignored.
    """

    # bit masks — octal literals per §10.1 of the spec
    N = 0o000010
    Z = 0o000004
    V = 0o000002
    C = 0o000001
    T = 0o000020
    IPL_MASK = 0o000340  # bits 7..5
    Curr_MODE = 0o140000  # bits 15..14
    Prev_MODE = 0o060000  # bits 13..12

    def __init__(self) -> None:
        self._value: int = 0         # raw 16-bit storage, one word, like real HW

    @property
    def value(self) -> int:
        """Raw PSW word, readable as if via MFPS (1177776 on MMU machines)."""
        return self._value

    @value.setter
    def value(self, v: int) -> None:
        """Set the whole PSW (used by RTI/RTT/MTPS — trap handler return)."""
        self._value = v & 0o177777

    # -- individual condition-code read/write (most-used ops) ------------
    def get(self, mask: int) -> bool:
        """Test one condition bit — e.g. psw.get(PSW.N) reads the N flag."""
        return bool(self._value & mask)

    def set(self, mask: int, on: bool) -> None:
        """Set/clear one or more bits, used by every instruction's post-ops."""
        if on:
            self._value |= mask
        else:
            self._value &= ~mask

    @property
    def ipl(self) -> int:
        """Current interrupt priority level 0-7; interrupt mask for §5.5."""
        return (self._value & PSW.IPL_MASK) >> 5


class CPUState:
    """Complete architectural state — the exact contents of a snapshot
    (excluding caches/per-quantum scratch). One field, one hardware
    register; nothing else gets to hide in here (§15 requirement)."""

    def __init__(self) -> None:
        # 8 general registers, 16-bit each (R0..R5, SP, PC)
        self.R: array[int] = array("H", [0] * 8)
        self.psw = PSW()
        self.halted: bool = False    # HALT reached: drop to ODT (§9)
        self.waiting: bool = False   # WAIT reached: park CPU until interrupt (§5.5)
