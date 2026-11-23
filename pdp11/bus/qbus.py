"""
Q-Bus (spec §6.1) — 11/03 (18-bit address space, 16-bit DMA in M4) and
11/94 (22-bit Q22-CD, asynchronous, M6).

Arbitration per §6.1: BIAK (interrupt-acknowledge) and BDMG (DMA-grant)
daisy chains. The grant passes down the chain exactly like the Unibus
BR/BG and NPR lines, so the semantics are the same: highest request level
wins, ties go to the physically first slot, and BDMG is a single
first-slot-wins grant. The CPU never polls devices (§6.1): the board asks
this arbiter via poll_interrupt()/service_dma(), same face as Unibus.

Addressing: addr_mask exposes the profile's physical width (18 bits on the
11/03, 22 on the 11/94). M4 keeps DMA at 16 bits (§2: "Q-Bus DMA, 16-bit
only"); the 18/22-bit map registers land with map_registers.py in M6.
The 11/94's asynchronous operation (no fixed bus-cycle timing) is modeled
with the profile quantum in M6.

References: Q-Bus specification (DEC); LSI-11 and DCJ-11 (J-11) docs (§21).
"""

from __future__ import annotations

from pdp11.bus.abstract_bus import BusDevice

# Q-Bus BIRQ levels map to the same 4-7 priority scale the PSW IPL uses.
BIRQ_MIN = 4
BIRQ_MAX = 7


class QBus:
    """Q-Bus arbitration state: the ordered device list (backplane slots)
    plus the BIAK/BDMG grant logic. Memory/register routing lives in the
    board and memory/io_page (§6.2); this class owns ONLY who gets the bus."""

    def __init__(self, physical_bits: int = 18) -> None:
        self.devices: list[BusDevice] = []       # slot order = daisy-chain order
        self.addr_mask: int = (1 << physical_bits) - 1   # 0o777777 at 18 bits

    def attach(self, device: BusDevice) -> None:
        """Attach a device in the next backplane slot (append-only, like
        plugging into the next free Q-Bus slot)."""
        self.devices.append(device)

    # -- grants -------------------------------------------------------------
    def grant_dma(self) -> BusDevice | None:
        """BDMG grant: first slot (daisy chain) currently asserting dma_request()."""
        for dev in self.devices:
            if dev.dma_request():
                return dev
        return None

    def grant_interrupt(self) -> tuple[int, int] | None:
        """BIAK grant: highest pending BIRQ level wins; ties go to the lowest
        slot index. Returns (vector, level) or None."""
        best: tuple[int, int] | None = None
        best_ipl = -1
        for dev in self.devices:                 # slot order = chain order
            req = dev.interrupt_pending()
            if req is None:
                continue
            vector, ipl = req
            if not BIRQ_MIN <= ipl <= BIRQ_MAX:
                continue                         # BIRQ levels are 4-7; ignore others
            if ipl > best_ipl:                   # strictly greater: first max wins
                best = (vector, ipl)
                best_ipl = ipl
        return best

    def arbitrate(self) -> tuple[str, BusDevice] | tuple[str, tuple[int, int]] | None:
        """One arbitration pass: BDMG (DMA) outranks BIAK (§6.1).
        Returns ("dma", device), ("intr", (vector, ipl)), or None."""
        dma = self.grant_dma()
        if dma is not None:
            return ("dma", dma)
        req = self.grant_interrupt()
        if req is not None:
            return ("intr", req)
        return None

    # -- cycle stealing -------------------------------------------------------
    @staticmethod
    def word_cycles(n_words: int) -> int:
        """Bus cycles consumed by a BDMG transfer: one per 16-bit word (§5.5).
        (The Q-Bus cycle is nominally 300 ns vs the Unibus 150 ns, §5.6 —
        the count is still one bus cycle per word; the quantum difference
        only matters for --throttle pacing in main.py.)"""
        return n_words
