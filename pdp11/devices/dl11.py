"""
DL11 serial line — the console DL11-E (spec §7, P0, all machines; M2).

Registers (octal, console DL11 at 177560), PDP-11 Peripherals Handbook:
    RCVSTAT  177560  receiver status    bit 7 = DONE, bit 6 = IE (read/write)
    RCVBUF   177562  receiver buffer    (read-only; reading clears DONE)
    XMTSTAT  177564  transmitter status bit 7 = DONE (ready), bit 6 = IE
    XMTBUF   177566  transmitter buffer (write starts a transmit)

Byte access to either half of RBUF/XBUF is legal (§6.2).

Interrupts: receiver vector 000060, transmitter vector 000064, both BR4.
If both are pending, the receiver is reported first (documented choice;
cross-check against SIMH per §22.4).

Timing (§5.6, deterministic): the transmitter completes TX_CYCLES bus cycles
after XMTBUF is written (scheduled event, never wall clock); input is pumped
from the terminal every INPUT_POLL_CYCLES. Values are approximations of
real DL11 bit times, refined against SIMH traces in M3 (§22.4).

Console traffic flows to a virtual terminal via graphics/terminal.py (§7) —
this module never touches host I/O (§8).

References: PDP-11 Peripherals Handbook, DL11 section (§21).
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice
from pdp11.graphics.terminal import TerminalFrontend
from pdp11.system_clock import SystemClock

_log = logging.getLogger(__name__)

# Register offsets from the window base (octal) — §10.2 format.
RCVSTAT = 0o177560
RCVBUF = 0o177562
XMTSTAT = 0o177564
XMTBUF = 0o177566

DONE = 0o000200   # bit 7: transfer complete / ready
IE = 0o000100     # bit 6: interrupt enable

VECTOR_RX = 0o000060   # receiver interrupt vector
VECTOR_TX = 0o000064   # transmitter interrupt vector
BR_LEVEL = 4           # both DL11 interrupts are BR4

TX_CYCLES = 0o1000         # ~512 cycles (~77 µs at 150 ns) per transmitted byte
INPUT_POLL_CYCLES = 0o310  # input pump period: 200 bus cycles


class DL11(BusDevice):
    """Console DL11-E: CSR window + scheduled transmit/input state machine."""

    window_size = 8            # four 16-bit registers = 8 bytes (177560-177567)
    name = "DL11"

    def __init__(self, clock: SystemClock, terminal: TerminalFrontend,
                 base: int = RCVSTAT) -> None:
        self._clock = clock
        self._terminal = terminal
        self.base = base
        self.rxbuf = 0             # buffered received byte
        self.rx_done = False
        self.rx_ie = False
        self.tx_done = True        # transmitter ready at reset
        self.tx_ie = False
        self._tx_byte = 0          # pending transmit byte (serializable event arg, §15)
        self._pump_on = False
        self.reset()

    # -- lifecycle -----------------------------------------------------------
    def reset(self) -> None:
        """Bus init: transmitter ready, receiver empty, interrupts disabled;
        (re)start the deterministic input pump (§5.6)."""
        self.tx_done = True
        self.rx_done = False
        self.rx_ie = False
        self.tx_ie = False
        self._pump_on = True
        self._clock.schedule(INPUT_POLL_CYCLES, self._pump)

    def _pump(self) -> None:
        """Scheduled input poll: move one queued host byte into RCVBUF and
        raise DONE. Only reads when the buffer is empty (a read would discard
        a queued byte while DONE is set). Reschedules itself — devices
        schedule events, never wall-clock (§5.6)."""
        if self._pump_on:
            if not self.rx_done:
                b = self._terminal.read()
                if b is not None:
                    self.rxbuf = b & 0o377
                    self.rx_done = True
                    _log.debug("DL11 RX byte %o -> DONE", b)
            self._clock.schedule(INPUT_POLL_CYCLES, self._pump)

    def _tx_complete(self) -> None:
        """Scheduled transmit completion: emit the pending byte, set DONE.
        Bound method (not a lambda) so the event queue stays serializable
        for snapshots (§15)."""
        byte = self._tx_byte
        self._terminal.write(byte)
        self.tx_done = True
        _log.debug("DL11 TX byte %o complete", byte)

    # -- register window (offsets are byte offsets from base) ------------------
    def _reg(self, offset: int) -> int:
        if offset == 0:
            return (DONE if self.rx_done else 0) | (IE if self.rx_ie else 0)
        if offset == 2:
            return self.rxbuf
        if offset == 4:
            return (DONE if self.tx_done else 0) | (IE if self.tx_ie else 0)
        return 0                            # XMTBUF reads as 0 (write-only)

    def read_word(self, offset: int) -> int:
        word = self._reg(offset & 0o177776)  # register index = offset >> 1
        if offset == 2:                      # RCVBUF read clears DONE
            self.rx_done = False
        return word & 0o177777

    def write_word(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        if reg == 0:                         # RCVSTAT: only IE is writable
            self.rx_ie = bool(value & IE)
        elif reg == 4:                       # XMTSTAT: only IE is writable
            self.tx_ie = bool(value & IE)
        elif reg == 6:                       # XMTBUF: start a transmit
            self.tx_done = False
            self._tx_byte = value & 0o377
            self._clock.schedule(TX_CYCLES, self._tx_complete)

    def read_byte(self, offset: int) -> int:
        word = self.read_word(offset & 0o177776)
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        if offset % 2 == 0:
            self.write_word(offset & 0o177776, value & 0o377)
        else:
            self.write_word(offset & 0o177776, (value & 0o377) << 8)

    # -- interrupts (§6.1: polled by the bus arbiter) ---------------------------
    def interrupt_pending(self) -> tuple[int, int] | None:
        if self.rx_done and self.rx_ie:
            return (VECTOR_RX, BR_LEVEL)
        if self.tx_done and self.tx_ie:
            return (VECTOR_TX, BR_LEVEL)
        return None

    # -- snapshot contract (§15) ------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {"rxbuf": self.rxbuf, "rx_done": self.rx_done, "rx_ie": self.rx_ie,
                "tx_done": self.tx_done, "tx_ie": self.tx_ie, "tx_byte": self._tx_byte,
                "base": self.base}

    def deserialize(self, state: dict[str, Any]) -> None:
        for key in ("rxbuf", "rx_done", "rx_ie", "tx_done", "tx_ie", "tx_byte", "base"):
            if key not in state:
                raise ValueError(f"DL11 snapshot missing {key!r}")
        self.rxbuf = state["rxbuf"]
        self.rx_done = state["rx_done"]
        self.rx_ie = state["rx_ie"]
        self.tx_done = state["tx_done"]
        self.tx_ie = state["tx_ie"]
        self._tx_byte = state["tx_byte"]
        self.base = state["base"]
