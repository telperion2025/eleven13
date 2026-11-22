"""
M1 acceptance tests for the skeleton itself (run with: pytest pdp11).

These anchor the commit that satisfies milestone M1: the four profiles
load, the clock is deterministic, and the IO page routes word and byte
accesses with correct overlap detection. CPU golden-instruction tests
(cpu_golden.py) grow alongside each instruction implemented in M1.
"""

import pytest

from pdp11.config.profiles import PROFILES, CpuCap
from pdp11.system_clock import SystemClock


def test_all_four_profiles_load():
    """The four target machines exist with distinct capability sets (§2)."""
    assert sorted(PROFILES) == ["11/03", "11/20", "11/70", "11/94"]
    assert not PROFILES["11/20"].has(CpuCap.EIS)          # KD11-B: no EIS
    assert PROFILES["11/94"].has(CpuCap.CIS)              # J-11: CIS on-chip


def test_clock_is_deterministic_across_replays():
    """Same schedule + same advances -> same event order (§5.6, §11.7)."""
    def build(clock):
        seen = []
        clock.schedule(5, lambda: seen.append("seek"))
        clock.schedule(5, lambda: seen.append("tick"))   # same due cycle: FIFO
        clock.schedule(1, lambda: seen.append("fast"))
        for fn in clock.advance(10):     # §17.3: advance yields callbacks; the
            fn()                          # caller must run them to completion
        return seen

    assert build(SystemClock()) == ["fast", "seek", "tick"]
    assert build(SystemClock()) == ["fast", "seek", "tick"]  # replay-identical


def test_iopage_rejects_overlapping_windows(io_page_with_device):
    """Attach() catches backplane wiring errors at boot, not in use (§6.2)."""
    # fixture io_page_with_device supplies a minimal two-register dummy
    with pytest.raises(ValueError):
        io_page_with_device.attach_duplicate_window()
