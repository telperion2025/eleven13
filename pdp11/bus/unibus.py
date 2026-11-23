"""
Unibus (spec §6.1) — 11/20 and 11/70 host bus: arbitration and device slots.

Arbitration rules (§6.1):
    - NPR (DMA) outranks BR (interrupt) grants.
    - BR/BG interrupt daisy chain: the highest BR level (4-7) wins; among
      devices on the same level the grant passes down the chain, so the
      physically first slot wins. Priority is slot order, as on the real
      backplane — devices attach in backplane order via attach().
    - NPR is a single daisy-chained grant line: the first slot asserting
      dma_request() wins.
    - The CPU never polls devices (§6.1): the board asks the Unibus for the
      grant (poll_interrupt / service_dma) and the CPU acts on the result.

Cycle stealing (§5.5): NPR transfers steal bus cycles from the CPU at word
granularity — one bus cycle (~150 ns) per 16-bit word moved, charged to the
instruction executing when the steal happens (board.service_dma counts the
words the device actually moves through the routing layer).

Addressing: the Unibus itself is 18-bit; the 11/20 only generates 16-bit
addresses (profile.physical_bits) and the 11/70 reaches 22 bits through the
Unibus Map in M5. addr_mask exposes the profile's physical width.

References: PDP-11 Unibus Specification (DEC); PDP-11 Peripherals Handbook (§21).
"""

from __future__ import annotations

from pdp11.bus.abstract_bus import BusDevice

# Unibus BR levels are 4-7 (IPL bits 7-5 of the PSW) — §5.5.
BR_MIN = 4
BR_MAX = 7


class Unibus:
    """Unibus arbitration state: the ordered device list (backplane slots)
    plus the grant logic. Memory/register routing itself lives in the board
    and memory/io_page (§6.2); this class owns ONLY who gets the bus."""

    def __init__(self, physical_bits: int = 18) -> None:
        self.devices: list[BusDevice] = []       # slot order = daisy-chain order
        self.addr_mask: int = (1 << physical_bits) - 1   # 0o777777 at 18 bits

    def attach(self, device: BusDevice) -> None:
        """Attach a device in the next backplane slot (append-only, like
        plugging into the next free slot)."""
        self.devices.append(device)

    # -- grants -------------------------------------------------------------
    def grant_dma(self) -> BusDevice | None:
        """NPR grant: first slot (daisy chain) currently asserting dma_request()."""
        for dev in self.devices:
            if dev.dma_request():
                return dev
        return None

    def grant_interrupt(self) -> tuple[int, int] | None:
        """BR/BG grant: highest pending BR level wins; ties go to the lowest
        slot index. Returns (vector, ipl) or None."""
        best: tuple[int, int] | None = None
        best_ipl = -1
        for dev in self.devices:                 # slot order = chain order
            req = dev.interrupt_pending()
            if req is None:
                continue
            vector, ipl = req
            if not BR_MIN <= ipl <= BR_MAX:
                continue                         # BR levels are 4-7; ignore others
            if ipl > best_ipl:                   # strictly greater: first max wins
                best = (vector, ipl)
                best_ipl = ipl
        return best

    def arbitrate(self) -> tuple[str, BusDevice] | tuple[str, tuple[int, int]] | None:
        """One arbitration pass: NPR outranks BR (§6.1).
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
        """Bus cycles consumed by an NPR transfer: one per 16-bit word (§5.5)."""
        return n_words
