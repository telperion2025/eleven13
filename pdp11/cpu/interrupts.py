"""
Interrupt scheduling: IPL levels and between-instruction sampling (spec §5.5).

The PSW carries the current interrupt priority level (IPL, bits 7-5). A
device interrupt is taken only if its bus request level (BR, 4-7) is
numerically greater than the current IPL. Interrupts are sampled BETWEEN
instructions, never mid-instruction at the state-machine level (Unibus
NPR cycle-stealing mid-instruction is modeled at word-transfer granularity
by the bus layer in M2+; the CPU-facing contract stays per-instruction).

The CPU never polls devices directly (§6.1): the bus/board hands over the
ordered device list, and this module applies the priority rules. Among
devices at the same level, slot order wins — the daisy chain (BR/BG on
Unibus, BIAK on Q-Bus) grants the physically first device (spec §6.1).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from pdp11.bus.abstract_bus import BusDevice
from pdp11.cpu.registers import Reg
from pdp11.cpu.traps import enter_vector

if TYPE_CHECKING:  # pragma: no cover — typing only
    from pdp11.cpu.core import CPU


def pending(devices: Iterable[BusDevice]) -> tuple[int, int] | None:
    """
    Highest-priority pending interrupt as (vector, ipl), or None.

    Devices are scanned in bus (slot) order; the first device whose ipl is
    strictly greater than every other pending ipl wins, matching the
    daisy-chain grant order of the real backplane (§6.1).
    """
    best: tuple[int, int] | None = None
    for dev in devices:
        req = dev.interrupt_pending()
        if req is None:
            continue
        vector, ipl = req
        if best is None or ipl > best[1]:
            best = (vector, ipl)
    return best


def service(cpu: CPU, req: tuple[int, int] | None) -> bool:
    """
    Vector one interrupt whose request the BUS arbiter already granted
    (CPU.step passes bus.poll_interrupt(); §6.1 — the CPU never polls
    devices directly).

    Returns True if the interrupt was vectored. It is taken only if the
    request's BR level outranks the PSW IPL (§5.5).
    """
    if req is None:
        return False
    vector, ipl = req
    st = cpu.state
    if ipl <= st.psw.ipl:
        return False                       # masked by current priority
    # interrupt entry vectors like a trap: PC then PSW pushed, vector loaded;
    # on MMU machines the vector comes from kernel D space and the stack is
    # the new mode's (traps.enter_vector, §5.5/§5.7)
    enter_vector(cpu, vector, st.R[Reg.PC], st.psw.value)
    st.waiting = False                     # WAIT exits on any taken interrupt
    return True
