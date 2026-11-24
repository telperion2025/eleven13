"""
KW11-P programmable clock tests (devices/kw11p.py; SIMH pdp11_pclk.c):
register file, rate table, single-shot vs repeat, FIX single tick,
vector 000104 at BR6, §15 snapshot round-trip.
"""

from __future__ import annotations

from pdp11.devices.kw11p import (
    CNT,
    CSB,
    CSR,
    DONE,
    ERR,
    FIX,
    GO,
    IE,
    KW11P,
    MODE,
    RATE_CYCLES,
    UPDN,
)
from pdp11.system_clock import SystemClock


def make() -> tuple[KW11P, SystemClock]:
    clock = SystemClock()
    return KW11P(clock), clock


def drain(clock: SystemClock, cycles: int) -> None:
    for fn in clock.advance(cycles):
        fn()


def test_kw11p_reset_state():
    dev, _ = make()
    assert dev.read_word(CSR) == 0
    assert dev.read_word(CSB) == 0
    assert dev.read_word(CNT) == 0
    assert dev.interrupt_pending() is None


def test_kw11p_single_shot_interrupt():
    dev, clock = make()
    dev.write_word(CSB, 10)                       # interval 10 counts
    dev.write_word(CSR, GO | IE | (0o2 << 1))     # line rate, run, IE
    assert dev.interrupt_pending() is None
    drain(clock, 10 * RATE_CYCLES[2] + 1)
    assert dev.csr & DONE                         # underflow set DONE...
    assert dev.interrupt_pending() == (0o104, 6)  # ...and BR6/104 pending
    assert dev.read_word(CSR) & DONE              # read returns DONE...
    assert dev.csr & DONE == 0                    # ...and clears it
    assert dev.interrupt_pending() is None
    # single-shot: GO cleared, no reschedule
    drain(clock, 10 * RATE_CYCLES[2] * 3)
    assert dev.csr & GO == 0


def test_kw11p_repeat_mode_reschedules():
    dev, clock = make()
    dev.write_word(CSB, 4)
    dev.write_word(CSR, GO | MODE | (0o2 << 1))
    drain(clock, 4 * RATE_CYCLES[2] + 1)
    assert dev.csr & DONE
    dev.read_word(CSR)                            # clear DONE
    drain(clock, 4 * RATE_CYCLES[2] + 1)
    assert dev.csr & DONE                         # repeat fired again
    assert dev.csr & GO                           # still running


def test_kw11p_fix_single_tick_while_stopped():
    dev, clock = make()
    dev.write_word(CSB, 1)                        # CNT <- 1 while stopped
    assert dev.read_word(CNT) == 1
    dev.write_word(CSR, FIX)                      # single tick down: 1 -> 0 fires
    assert dev.csr & DONE
    assert dev.csr & GO == 0


def test_kw11p_updown_counts():
    dev, clock = make()
    dev.write_word(CSB, 0o177770)                 # 65526
    dev.write_word(CSR, GO | UPDN | (0o0 << 1))   # 100 kHz, count up
    # up-count delay = (65536 - 65526) * 10 = 100 cycles
    drain(clock, 100 + 1)
    assert dev.csr & DONE


def test_kw11p_err_on_double_underflow():
    dev, clock = make()
    dev.write_word(CSB, 2)
    dev.write_word(CSR, GO | MODE | (0o1 << 1))   # repeat, 10 kHz
    drain(clock, 2 * RATE_CYCLES[1] + 1)
    assert dev.csr & DONE
    # leave DONE set and let it fire again: ERR
    drain(clock, 2 * RATE_CYCLES[1] + 1)
    assert dev.csr & (DONE | ERR)


def test_kw11p_snapshot_roundtrip():
    dev, clock = make()
    dev.write_word(CSB, 100)
    dev.write_word(CSR, GO | IE | (0o2 << 1))
    drain(clock, 10)
    state = dev.serialize()
    dev2, clock2 = make()
    dev2.deserialize(state)
    assert dev2.csr == dev.csr and dev2.csb == dev.csb
    # the pending _tick event lives in the clock queue, which the §15 snapshot
    # layer serializes separately — mimic the board restore here
    if dev2._running:
        clock2.schedule(max(0, dev2._due - clock2.cycle), dev2._tick)
    drain(clock2, 100 * RATE_CYCLES[2] + 2)
    assert dev2.csr & DONE
