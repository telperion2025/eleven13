"""
Extended debugger (spec §9, --debug): breakpoints, watchpoints, MMU page
dumps, interrupt-pending display, single-step trace with disassembly,
device statistics, container inspection — plus the performance
instrumentation of §14 (instructions-per-second, per-module cycle costs,
function-level timing dumps) and the §18.3 EmulationError crash dump
(registers, PC, last 8 dispatched opcodes, clock cycle).

M7 stub per §19.10.
"""

from __future__ import annotations


def _debugger_not_implemented() -> None:
    """Extended debugger + performance instrumentation land in M7/M8 (§12)."""
    raise NotImplementedError("§9/§14 debugger + instrumentation — M7/M8")
