"""
DZ11 8-line asynchronous multiplexer (spec §7, P1; 11/03, 11/70, 11/94).

Register window at octal 160100, 8 bytes (four words) — SIMH pdp11_dz.c
IOLN_DZ = 010:
    DZCSR  160100  control/status (read/write)
    DZRBUF 160102  receive buffer (read-only; write = DZLPR line parameter)
    DZTCR  160104  transmission control (read/write): xmit-enable low byte,
                   DTR high byte
    DZMSR  160106  modem status (read-only: ring low byte, carrier high)
    DZTDR  160106  transmit data (write-only; char low byte, break high)

DZCSR bits (SIMH dz.c): <15> TRDY xmit ready, <14> TIE xmit int enb,
<13> SA silo alarm, <12> SAE silo alarm enb, <10:8> TLINE xmit line,
<7> RDONE rcv done, <6> RIE rcv int enb, <5> MSE master scan enb,
<4> CLR clear, <3> MAINT. Writable: MSE|RIE|SAE|TIE.

DZRBUF read: <7:0> char, <10:8> line, <15> VALID; reading pops the silo
and clears RDONE when it empties. Interrupts: receive vector 000300,
transmit vector 000304; BR level 5 on Unibus, 4 (BIRQ4) on Q-Bus
(E11 default note — flagged §22.4). Level-based: RX fires while
RIE & (RDONE or SA), TX while TIE & TRDY.

v1 models 8 lines; line 0 may carry a TerminalFrontend (host I/O stays in
graphics/, §8), the other lines are disconnected (no input, output
dropped). Transfers are scheduled through SystemClock — never wall clock
(§5.6). Timing constants are approximations of the real DZ11 bit times,
flagged for the SIMH cross-check (§22.4).

References: PDP-11 Peripherals Handbook, DZ11 section (§21); SIMH
pdp11_dz.c (bit layout cross-checked §22.4); EK-DZ110-UG-002.
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice
from pdp11.graphics.terminal import TerminalFrontend
from pdp11.system_clock import SystemClock

DZ11_BASE = 0o160100
DZCSR = 0o00
DZRBUF = 0o02   # read: receive buffer; write: DZLPR line parameter
DZTCR = 0o04
DZMSR_DZTDR = 0o06   # read: modem status; write: transmit data

# DZCSR bits
CSR_MAINT = 0o000010
CSR_CLR = 0o000020
CSR_MSE = 0o000040       # master scan enable
CSR_RIE = 0o000100       # receive interrupt enable
CSR_RDONE = 0o000200     # receive done (read-only)
CSR_TLINE_MASK = 0o001700
CSR_TLINE_SHIFT = 8
CSR_SAE = 0o010000       # silo alarm enable
CSR_SA = 0o020000        # silo alarm (read-only)
CSR_TIE = 0o040000       # transmit interrupt enable
CSR_TRDY = 0o100000      # transmit ready (read-only)
CSR_RW = CSR_MSE | CSR_RIE | CSR_SAE | CSR_TIE

# DZRBUF bits
RBUF_CHAR = 0o000377
RBUF_RLINE_SHIFT = 8
RBUF_VALID = 0o100000

VECTOR_RX = 0o000300
VECTOR_TX = 0o000304
BR_LEVEL_UNIBUS = 5
BR_LEVEL_QBUS = 4        # BIRQ4 on Q-Bus (E11 default; flagged §22.4)

LINES = 8
SILO_ALARM = 16          # silo alarm level (SIMH DZ_SILO_ALM)
TX_CYCLES = 0o1000       # ~one char time per transmit (~77 µs at 150 ns)
INPUT_POLL_CYCLES = 0o310  # input scan period (like the DL11 pump)

_log = logging.getLogger(__name__)


class DZ11(BusDevice):
    """DZ11 multiplexer: CSR register file + per-line silo state machine.
    Line 0 connects to `terminal` (None = disconnected)."""

    window_size = 0o10       # four word registers = 8 bytes
    name = "DZ11"

    def __init__(self, clock: SystemClock, terminal: TerminalFrontend | None = None,
                 base: int = DZ11_BASE, br_level: int = BR_LEVEL_UNIBUS) -> None:
        self._clock = clock
        self._terminal = terminal
        self.base = base
        self.br_level = br_level
        self.csr = CSR_TRDY
        self.rbuf = 0
        self.lpr = 0
        self.tcr = 0
        self.msr = 0
        self.tdr = 0
        self._silo: list[int] = []       # received words: char | line<<8 | VALID
        self._tx_line = 0
        self._tx_byte = 0                # pending TX byte (serializable event arg, §15)
        self.reset()

    # -- lifecycle -----------------------------------------------------------
    def reset(self) -> None:
        """Bus init / CSR CLR: ready, interrupts off, silo empty."""
        self.csr = CSR_TRDY
        self.rbuf = 0
        self._silo.clear()
        self._clock.schedule(INPUT_POLL_CYCLES, self._scan)

    def _scan(self) -> None:
        """Scheduled input scan (master scan enable on): move one host byte
        from line 0's terminal into the silo and set RDONE. Reschedules."""
        if self.csr & CSR_MSE and self._terminal is not None and len(self._silo) < SILO_ALARM:
            b = self._terminal.read()
            if b is not None:
                self._silo.append((b & RBUF_CHAR) | RBUF_VALID)   # line 0
                self.csr |= CSR_RDONE
                if len(self._silo) >= SILO_ALARM and self.csr & CSR_SAE:
                    self.csr |= CSR_SA
        self._clock.schedule(INPUT_POLL_CYCLES, self._scan)

    def _tx_done(self) -> None:
        """Scheduled transmit completion: emit on the pending line, set TRDY.
        Bound method (not a lambda) so the event queue stays serializable (§15)."""
        byte, line = self._tx_byte, self._tx_line
        if line == 0 and self._terminal is not None:
            self._terminal.write(byte)
        self.csr |= CSR_TRDY
        _log.debug("DZ11 tx line %d byte %o complete", line, byte)

    # -- register window -------------------------------------------------------
    def read_word(self, offset: int) -> int:
        reg = offset & 0o177776
        if reg == DZCSR:
            return self.csr & 0o177777
        if reg == DZRBUF:
            # pop the silo; clear RDONE/SA when it empties
            self.csr &= ~CSR_SA
            if self._silo:
                self.rbuf = self._silo.pop(0)
                if not self._silo:
                    self.csr &= ~CSR_RDONE
            else:
                self.rbuf = 0
                self.csr &= ~CSR_RDONE
            return self.rbuf & 0o177777
        if reg == DZTCR:
            return self.tcr & 0o177777
        if reg == DZMSR_DZTDR:
            return self.msr & 0o177777
        return 0

    def write_word(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        v = value & 0o177777
        if reg == DZCSR:
            if v & CSR_CLR:
                self.reset()
                return
            self.csr = (self.csr & ~CSR_RW) | (v & CSR_RW)
            if not v & CSR_MSE:                       # scan off: clear status bits
                self.csr &= ~(CSR_SA | CSR_RDONE | CSR_TRDY)
            # interrupt requests are level-derived; see interrupt_pending()
        elif reg == DZRBUF:
            self.lpr = v                              # DZLPR: line params (stored, NI)
        elif reg == DZTCR:
            self.tcr = v
            if not (v & (1 << ((self.csr & CSR_TLINE_MASK) >> CSR_TLINE_SHIFT))):
                self.csr &= ~CSR_TRDY                 # selected line disabled
        elif reg == DZMSR_DZTDR:
            self.tdr = v
            if self.csr & CSR_MSE:
                line = (self.csr & CSR_TLINE_MASK) >> CSR_TLINE_SHIFT
                self.csr &= ~CSR_TRDY                 # busy while transmitting
                self._tx_byte = v & 0o377
                self._tx_line = line
                self._clock.schedule(TX_CYCLES, self._tx_done)

    def read_byte(self, offset: int) -> int:
        word = self.read_word(offset & 0o177776)
        return word & 0o377 if offset % 2 == 0 else (word >> 8) & 0o377

    def write_byte(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        cur = self.read_word(reg)
        if offset % 2 == 0:
            self.write_word(reg, (cur & 0o177400) | (value & 0o377))
        else:
            self.write_word(reg, (cur & 0o000377) | ((value & 0o377) << 8))

    # -- interrupts (§6.1: polled by the bus arbiter) ---------------------------
    def interrupt_pending(self) -> tuple[int, int] | None:
        if (self.csr & CSR_RIE) and (
                (self.csr & CSR_SAE and self.csr & CSR_SA) or (self.csr & CSR_RDONE)):
            return (VECTOR_RX, self.br_level)
        if (self.csr & CSR_TIE) and (self.csr & CSR_TRDY):
            return (VECTOR_TX, self.br_level)
        return None

    # -- snapshot contract (§15) --------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {"base": self.base, "br_level": self.br_level, "csr": self.csr,
                "rbuf": self.rbuf, "lpr": self.lpr, "tcr": self.tcr, "msr": self.msr,
                "tdr": self.tdr, "silo": list(self._silo),
                "tx_byte": self._tx_byte, "tx_line": self._tx_line}

    def deserialize(self, state: dict[str, Any]) -> None:
        keys = ("base", "br_level", "csr", "rbuf", "lpr", "tcr", "msr", "tdr", "silo",
                "tx_byte", "tx_line")
        for key in keys:
            if key not in state:
                raise ValueError(f"DZ11 snapshot missing {key!r}")
        self.base = state["base"]
        self.br_level = state["br_level"]
        self.csr = state["csr"]
        self.rbuf = state["rbuf"]
        self.lpr = state["lpr"]
        self.tcr = state["tcr"]
        self.msr = state["msr"]
        self.tdr = state["tdr"]
        self._silo = list(state["silo"])
        self._tx_byte = state["tx_byte"]
        self._tx_line = state["tx_line"]
