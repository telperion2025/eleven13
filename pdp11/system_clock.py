"""
Deterministic system clock and event scheduler (spec §5.6).

Binding timing model:
    - The quantum is ONE BUS CYCLE (nominally 150 ns Unibus / 300 ns Q-Bus).
    - Devices never poll; they push (due_cycle, callback) entries onto a
      priority queue. The CPU drains all entries due at-or-before the
      current cycle after each instruction.
    - NO wall-clock reads inside this module: identical event sequences
      replay identically, which is what makes §11 criterion 7 testable.

Divergence from host time (running faster or slower than a real 11) is
correct and intended; throttle-to-real-time is applied only in main.py.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterator


class SystemClock:
    """Single monotonic bus-cycle counter plus a scheduled-event queue."""

    def __init__(self) -> None:
        self._cycle: int = 0        # bus cycles elapsed since power-on
        self._queue: list[tuple[int, int, Callable[[], None]]] = []  # (due, seq, fn)
        self._seq: int = 0          # tiebreaker: FIFO among equal due cycles

    # -- queries ---------------------------------------------------------
    @property
    def cycle(self) -> int:
        """Current bus-cycle count (deterministic; never derived from time.*)."""
        return self._cycle

    # -- scheduling -------------------------------------------------------
    def schedule(self, after_cycles: int, callback: Callable[[], None]) -> None:
        """
        Arrange for callback() to run once the given number of bus cycles
        have elapsed after the CURRENT cycle. Devices use this for seek
        completion, tape gaps, clock ticks (KW11-L: every 16_667 cycles at
        60 Hz — see devices/kw11l.py), never for polling.

        after_cycles may be 0: the callback fires on the next drain().
        """
        self._seq += 1              # stable order for events due on the same cycle
        heapq.heappush(self._queue, (self._cycle + after_cycles, self._seq, callback))

    # -- advancing ----------------------------------------------------------
    def advance(self, cycles: int) -> Iterator[Callable[[], None]]:
        """
        Consume `cycles` bus cycles (the CPU calls this with the per-instruction
        cost table value after each instruction) and yield every event whose
        deadline has arrived, in (due_cycle, seq) order — the same order the
        real backplane would see.
        """
        self._cycle += int(cycles)  # int() guards against float leakage into the model
        while self._queue and self._queue[0][0] <= self._cycle:
            _, _, fn = heapq.heappop(self._queue)
            yield fn                # callers must run events to completion before continuing
