"""
SIMH compatibility utilities (spec §16, §3 layout): importers/exporters for
legacy formats beyond the v1 core set (e.g. dump-style images) — clearly
marked outside the v1 core scope. The v1 hard requirement is byte-compatible
read/write of the formats in container.py, including .tap (§11.10).

M8 stub per §19.10.
"""

from __future__ import annotations


def _simh_compat_not_implemented() -> None:
    """Legacy format importers/exporters land in M8 (§12, §16)."""
    raise NotImplementedError("§16 SIMH legacy importers/exporters — M8")
