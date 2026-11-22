"""
Emulator error taxonomy (spec §18.3) — three layers, never mixed:

1. `BusInit` (bus/abstract_bus.py) — hardware signaling inside devices and
   the bus; models a real bus timeout. Caught only by the machine layer
   (CPU core), never by user code.
2. `EmulationError` — a genuine emulator bug: a broken state invariant
   (dispatch-table hole, snapshot schema mismatch, CPU without a bus).
   These crash loudly with a full state dump (§18.3), never swallowed.
3. Plain Python exceptions (ValueError, OSError, argparse errors) —
   user-facing misuse with actionable messages.
"""

from __future__ import annotations


class EmulationError(Exception):
    """An emulator bug: an internal invariant was violated.

    Raised where continuing would silently corrupt emulated state. The
    top-level handler (debugger dump routine, §18.3) prints registers, PC,
    the last dispatched opcodes and the clock cycle alongside this error.
    """
