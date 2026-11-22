"""
Session checkpointing (spec §15): serialize the full CPU state, MMU/map
state, RAM/ROM contents, every device's registers and internal state (via
the BusDevice serialize()/deserialize() contract), and the scheduler's
event queue + cycle counter. Restore is exact and resumable — a snapshot
taken mid-boot continues to a byte-identical subsequent outcome (§11.8).

Format: JSON-wrapped with a binary core image (§15); CLI --save/--restore
plus debugger snapshot-at-breakpoint commands.

M8 stub per §19.10.
"""

from __future__ import annotations


def _snapshot_not_implemented() -> None:
    """Snapshot save/restore lands in M8 (§12, §15)."""
    raise NotImplementedError("§15 snapshot save/restore — M8")
