"""
Determinism and measured-throughput tests (spec §5.6, §11.7, §14, §18.2) — M8.

The §18.2 determinism gate in test form: the same scripted run on two fresh
machines produces a cycle-for-cycle identical device-event trace (fingerprints
include the scheduler queue), and a §15 snapshot taken mid-run restores into
a third machine that then continues byte-identically (§11.8). The throughput
test records the first measured instructions/second number (§14) — wall
clock is allowed HERE (test code), never inside the machine core (§19.6).
"""

from __future__ import annotations

import time

from pdp11.control.snapshot import restore_state, snapshot_state
from pdp11.tests.test_console import make_board
from pdp11.tests.test_snapshot import fingerprint, load_spin, step_n

STEPS = 100_000          # scripted run length (decimal, off-metal §10.3)
CHECKPOINT_EVERY = 5_000


def run_with_checkpoints(board, n: int) -> list:
    """Run up to n instructions, fingerprinting every CHECKPOINT_EVERY steps
    and at the end — the 'device-event trace' for §18.2 comparison."""
    fps = []
    cpu = board.cpu
    for i in range(n):
        if cpu.state.halted:
            break
        cpu.step()
        if i % CHECKPOINT_EVERY == 0:
            fps.append(fingerprint(board))
    fps.append(fingerprint(board))
    return fps


def test_replay_is_cycle_for_cycle_identical():
    """§18.2 determinism: two fresh 11/03 machines (DL11+KW11L+DZ11+RX11+RK11
    attached) running the same program produce identical fingerprints at
    every checkpoint, including scheduler-queue timing."""
    board_a, _ = make_board("11/03")
    board_b, _ = make_board("11/03")
    load_spin(board_a)
    load_spin(board_b)
    fps_a = run_with_checkpoints(board_a, STEPS)
    fps_b = run_with_checkpoints(board_b, STEPS)
    assert len(fps_a) == len(fps_b) > 1
    assert fps_a == fps_b
    assert board_a.clock.cycle == board_b.clock.cycle > 0


def test_snapshot_midrun_replays_identically():
    """§11.8: snapshot at the halfway checkpoint, restore into a fresh board,
    continue both — identical final fingerprint (event queue included)."""
    board_a, _ = make_board("11/20")
    load_spin(board_a)
    step_n(board_a, STEPS // 2)
    state = snapshot_state(board_a)
    board_b, _ = make_board("11/20")
    restore_state(board_b, state)
    step_n(board_a, STEPS // 2)
    step_n(board_b, STEPS // 2)
    assert fingerprint(board_a) == fingerprint(board_b)


def test_measured_throughput():
    """§14 first measured throughput number: 1M-instruction spin loop on the
    11/20, wall-clock timed HERE in the test (§19.6 boundary). The floor is a
    sanity check, not a performance claim; the measured value is recorded in
    docs/acceptance-checklist.md."""
    board, _ = make_board("11/20")
    load_spin(board)
    n = 1_000_000
    t0 = time.monotonic()
    step_n(board, n)
    elapsed = time.monotonic() - t0
    ips = n / elapsed
    assert board.clock.cycle > 0
    print(f"\nM8 throughput: {n} instructions in {elapsed:.2f}s -> {ips:,.0f} instr/s "
          f"({board.clock.cycle} bus cycles)")
    assert ips > 10_000          # generous floor — guards against pathological regressions
