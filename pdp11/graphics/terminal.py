"""
Abstract terminal frontend interface (spec §8, §3 layout). The DL11 console
connects to a virtual terminal through this interface; host keyboard input
maps to the terminal's input queue and terminal output flows back out,
isolated behind TerminalFrontend/RenderBackend — no other module touches
host I/O (§8).

M7 stub per §19.10.
"""

from __future__ import annotations


def _terminal_not_implemented() -> None:
    """Terminal frontend interface lands in M7 (§12)."""
    raise NotImplementedError("§8 TerminalFrontend interface — M7")
