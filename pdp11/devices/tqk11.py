"""
TK50/TU81 tape via TQK11 (Q-Bus) (spec §7, P2; 11/94).

Full UQSSP/MSCP implementation, cross-checked against SIMH pdp11_tq.c,
pdp11_uqssp.h and pdp11_mscp.h (and DEC's PDP-11 Peripherals Handbook /
TK50-TU81 manuals via bitsavers.org/bits/DEC/pdp11):

- register window at 174500: IP at +0 (read = poll command ring, write =
  re-init), SA at +2 (read = status, write = host startup data), 4-byte
  window (SIMH IOLN_TQ; Q-Bus 16-bit I/O-page address 174500, Unibus 774500).
- 4-step init handshake on SA (SIMH CST_S1..S4): step 1 carries the
  interrupt vector (SA_S1H_VEC<<2) and ring-length exponents; steps 2/3
  hand over the 22-bit communications-region address; step 4 (SA_S4H_GO)
  brings the controller up and installs the command/response ring
  descriptors at the comm region (rsp ring at comm+0, cmd ring right
  after it; interrupt flag words at comm-2 (RI) and comm-4 (CI)).
- command/response rings of 32-bit descriptors (UQ_DESC_OWN/F/addr):
  the host offers command entries with OWN set, the controller releases
  them (OWN clear, F set); the controller writes responses into
  host-offered response entries and posts the ring interrupt flag.
- each ring entry points at a packet with a 4-byte UQSSP header (length,
  credits/type/CID) at addr-4 followed by the MSCP packet (word-indexed
  fields per pdp11_mscp.h: ref d[2..3], unit d[4], opcode/flags d[6],
  modifier d[7], byte count d[8..9], buffer addr d[10..11], tape position
  d[16..17], record size d[18..19]).
- tape opcodes against a SIMH .tap container (TapImage, §16/§21), same
  byte-compatible media as tm11.py: RD/WR/WTM/ERG/ERS/POS plus the control
  set SCC/GUS/ONL/AVL/ABO/GCS (DAP/FLU are success nops; anything else is
  ST_CMD|I_OPCD). Errors follow MSCP status codes (OFL/WPR/TMK/RDT/HST...).

Interrupt: vector programmed in init step 1 (SIMH: vec = SA_S1H_VEC<<2,
0 until programmed), BR level 5 (SIMH IPL_TQ; legal BIRQ 4-7 on Q-Bus).
The request latch is level-derived like the other devices here and drops
on IP-write re-init (the bus arbiter has no INTA hook, §6.1).

Transfer engine: one bus word (packet/descriptor) or byte (record data)
per stolen NPR/BDMG cycle through the DMAHost face, like tm11.py;
tape-motion latency is RECORD_CYCLES per command (SIMH tq_xtime, scaled
per §22.4).
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice, BusInit, DMAHost
from pdp11.disk_images.container import TapImage
from pdp11.system_clock import SystemClock

_log = logging.getLogger(__name__)

TQK11_BASE = 0o174500
IP = 0o00                       # offset 0: init (write) / poll (read)
SA = 0o02                       # offset 2: status (read) / startup (write)

DEFAULT_VECTOR = 0              # SIMH tq_reset: vec = 0 until init step 1
BR_LEVEL = 5                    # SIMH IPL_TQ (BIRQ 4-7 on Q-Bus)

# SA register bits (SIMH pdp11_uqssp.h)
SA_ER = 0x8000                  # error
SA_S4, SA_S3, SA_S2, SA_S1 = 0x4000, 0x2000, 0x1000, 0x0800
SA_S1C_NV = 0x0400              # fixed vector NI
SA_S1C_Q22 = 0x0200             # Q22 device
SA_S1C_MP = 0x0040              # mapping
SA_S1H_VL = 0x8000              # host step-1 reply: valid
SA_S1H_WR = 0x4000              # wrap mode
SA_S1H_IE = 0x0080              # interrupt enable
SA_S1H_VEC = 0x007F             # vector / 4
SA_S2H_CLO = 0xFFFE             # comm address lo
SA_S2H_PI = 0x0001              # adapter purge interrupt
SA_S3H_PP = 0x8000              # purge/poll test
SA_S3H_CHI = 0x7FFF             # comm address hi
SA_S4H_GO = 0x0001              # go

def _s1h_cq(x: int) -> int: return 1 << ((x >> 11) & 7)
def _s1h_rq(x: int) -> int: return 1 << ((x >> 8) & 7)
def _s2c_ec(x: int) -> int: return (x >> 8) & 0xFF

# comm-region offsets (SIMH SA_COMM_*)
COMM_PI, COMM_CI, COMM_RI = -6, -4, -2

# ring descriptor bits (SIMH UQ_DESC_*)
UQ_DESC_OWN = 0x80000000
UQ_DESC_F = 0x40000000
UQ_ADDR = 0x003FFFFE

# packet buffer word indices (SIMH d[], buffer loaded at desc addr - 4:
# UQ header = d[0..1], MSCP packet fields from d[2])
UQ_HLNT, UQ_HCTC = 0, 1
CMD_REFL, CMD_REFH, CMD_UN, CMD_OPC, CMD_MOD = 2, 3, 4, 6, 7
RW_BCL, RW_BCH, RW_BAL, RW_BAH = 8, 9, 10, 11
RW_POSL, RW_POSH, RW_RSZL, RW_RSZH = 16, 17, 18, 19
RSP_OPF, RSP_STS = 6, 7
ONL_MLUN, ONL_UFL = 8, 9
ONL_UIDA, ONL_UIDB, ONL_UIDC, ONL_UIDD = 12, 13, 14, 15
ONL_MEDL, ONL_MEDH, ONL_FMT, ONL_SPD = 16, 17, 18, 19
ONL_MAXL, ONL_MAXH, ONL_NREC = 20, 21, 22
POS_RCL, POS_RCH, POS_TMCL, POS_TMCH = 8, 9, 10, 11

UQ_HCTC_V_TYP, UQ_HCTC_V_CID = 4, 8
UQ_TYP_SEQ = 0
UQ_CID_TMSCP = 1                # TQK11 speaks TMSCP, not disk MSCP
UQ_CID_DUP = 2

# MSCP opcodes (pdp11_mscp.h)
OP_ABO, OP_GCS, OP_GUS, OP_SCC = 1, 2, 3, 4
OP_AVL, OP_ONL, OP_SUC, OP_DAP = 8, 9, 10, 11
OP_ACC, OP_CCD, OP_ERS, OP_FLU, OP_ERG = 16, 17, 18, 19, 22
OP_CMP, OP_RD, OP_WR, OP_WTM, OP_POS, OP_FMT, OP_AVA = 32, 33, 34, 36, 37, 47, 64
OP_END = 0x80
MOTION_OPS = frozenset({OP_ACC, OP_CMP, OP_RD, OP_WR, OP_WTM, OP_ERG, OP_ERS, OP_POS})

# modifiers
MD_RWD, MD_REV = 0x0002, 0x0008

# end flags
EF_EOT, EF_SXC, EF_LOG = 0x0008, 0x0010, 0x0020

# status codes
ST_SUC, ST_CMD, ST_OFL = 0, 1, 3
ST_WPR, ST_CMP, ST_HST = 6, 7, 9
ST_TMK, ST_RDT = 14, 16
SB_OFL_NV = 1 << 5
SB_WPR_HW = 256 << 5
SB_HST_NXM = 3 << 5
I_OPCD = 8 << 8

# response packet lengths (pdp11_mscp.h)
ABO_LNT, GCS_LNT, AVL_LNT, SCC_LNT = 16, 20, 12, 32
GUS_LNT_T, ONL_LNT, SUC_LNT = 44, 44, 44
ERS_LNT, ERG_LNT, FLU_LNT, WTM_LNT, POS_LNT, RW_LNT_T = 12, 12, 32, 32, 32, 36
RSP_LNT_MIN = 12

UID_TAPE = 3                    # MSCP unit class: tape
UF_RMV, UF_WPH = 0x0080, 0x2000
TF_CTP = 0x0200                 # TK50 cartridge tape format
TQ_MAXFR = 1 << 16              # max transfer (SIMH TQ_MAXFR)

# fatal error codes (pdp11_uqssp.h)
PE_PWE, PE_QRE, PE_ICI, PE_PIE, PE_PPF = 2, 6, 14, 20, 21

# controller init states (SIMH CST_*)
CST_S1, CST_S1_WR, CST_S2, CST_S3 = 1, 2, 3, 4
CST_S3_PPA, CST_S3_PPB, CST_S4, CST_UP = 5, 6, 7, 8

# tape-motion latency in bus cycles (SIMH tq_xtime=500, scaled per §22.4)
RECORD_CYCLES = 0o2000

# drive models (SIMH drv_tab): TK50=5, TU81=8
DRIVE_MODELS = {"TK50": 5, "TU81": 8}


class _DmaFault(Exception):
    """A DMA access faulted (NXM -> bus timeout, BusInit)."""


class TQK11(BusDevice):
    """TQK11 Q-Bus MSCP tape controller (TK50/TU81), unit 0. IP/SA register
    file plus the command/response ring engine; packets and record data
    move one word/byte per stolen NPR(BDMG) cycle via the DMAHost face."""

    window_size = 0o4           # IP at +0, SA at +2
    name = "TQK11"

    def __init__(self, clock: SystemClock, bus: DMAHost,
                 tape: TapImage | None = None, base: int = TQK11_BASE,
                 writable: bool = True, drive: str = "TU81") -> None:
        self._clock = clock
        self._bus = bus
        self.base = base
        self.tape = tape
        self.writable = writable
        self.drive_model = DRIVE_MODELS.get(drive, DRIVE_MODELS["TU81"])
        self.reset()

    # -- lifecycle -------------------------------------------------------------
    def reset(self) -> None:
        """IP write = re-init: controller announces init step 1 on SA."""
        self._csta = CST_S1
        self._sa = SA_S1 | SA_S1C_Q22 | SA_S1C_MP
        self._saw = 0
        self._s1dat = 0
        self._comm = 0
        self._prgi = 0
        self._vector = DEFAULT_VECTOR
        self._cq: dict[str, int] = {"ioff": COMM_CI, "ba": 0, "lnt": 0, "idx": 0}
        self._rq: dict[str, int] = {"ioff": COMM_RI, "ba": 0, "lnt": 0, "idx": 0}
        self._int_pending = False
        self._rsp_queue: list[list[int]] = []   # responses waiting for a free entry
        self._pending: list[list[int]] = []     # fetched commands awaiting exec
        self._gen: Any = None                   # active DMA job generator
        self._req: Any = None
        self._ret: Any = None
        self._fault = False

    # -- register window ---------------------------------------------------------
    def read_word(self, offset: int) -> int:
        reg = offset & 0o177776
        if reg == IP:
            if self._csta == CST_S3_PPB:            # purge/poll test: IP read -> step 4
                self._step4()
            elif self._csta == CST_UP:
                self._poll()
            return 0                                # IP reads zero (SIMH tq_rd)
        if reg == SA:
            return self._sa & 0o177777
        return 0

    def write_word(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        v = value & 0o177777
        if reg == IP:
            self.reset()                            # IP write = re-init (SIMH tq_wr)
            return
        if reg == SA:
            self._saw = v
            self._init_step()
            return

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

    # -- init handshake (SIMH tq_quesvc init cases) --------------------------------
    def _init_int(self) -> None:
        if (self._s1dat & SA_S1H_IE) and self._vector:
            self._int_pending = True

    def _init_step(self) -> None:
        saw = self._saw
        if self._csta == CST_S1:
            if saw & SA_S1H_VL:
                if saw & SA_S1H_WR:                 # wrap mode: echo forever
                    self._sa = saw
                    self._csta = CST_S1_WR
                else:
                    self._s1dat = saw
                    self._vector = (saw & SA_S1H_VEC) << 2
                    self._sa = SA_S2 | (_s2c_ec(saw) << 8)
                    self._csta = CST_S2
                    self._init_int()
        elif self._csta == CST_S1_WR:
            self._sa = saw
        elif self._csta == CST_S2:
            self._comm = saw & SA_S2H_CLO
            self._prgi = saw & SA_S2H_PI
            self._sa = SA_S3 | (self._s1dat & 0xFF)  # echo of step-1 info
            self._csta = CST_S3
            self._init_int()
        elif self._csta == CST_S3:
            self._comm |= (saw & SA_S3H_CHI) << 16
            if saw & SA_S3H_PP:                     # purge/poll test
                self._sa = 0
                self._csta = CST_S3_PPA
            else:
                self._step4()
        elif self._csta == CST_S3_PPA:
            if saw:
                self._fatal(PE_PPF)
            else:
                self._csta = CST_S3_PPB
        elif self._csta == CST_S4:
            if saw & SA_S4H_GO:
                self._csta = CST_UP
                self._sa = 0

    def _step4(self) -> None:
        """Init step 4: install the rings at the comm region and zero it."""
        self._rq = {"ioff": COMM_RI, "ba": self._comm,
                    "lnt": _s1h_rq(self._s1dat) << 2, "idx": 0}
        self._cq = {"ioff": COMM_CI, "ba": self._comm + self._rq["lnt"],
                    "lnt": _s1h_cq(self._s1dat) << 2, "idx": 0}
        base = self._comm + COMM_CI
        lnt = self._cq["lnt"] + self._rq["lnt"]
        jobs = [("w", (base + 2 * i) & 0o3777777, 0) for i in range(max(0, lnt) // 2)]
        self._start(self._seq_gen(jobs))
        self._sa = SA_S4 | (self.drive_model << 4) | 1   # adapter #, version
        self._csta = CST_S4
        self._init_int()

    def _fatal(self, err: int) -> None:
        """Fatal port error: SA_ER | code (SIMH tq_fatal)."""
        self._sa = SA_ER | (err & 0o777)
        _log.error("TQK11 fatal port error %o", err)

    # -- DMA job engine: one bus word/byte per stolen cycle -------------------------
    def dma_request(self) -> bool:
        return self._gen is not None

    def _start(self, gen: Any) -> None:
        if self._gen is not None:
            return                                  # engine busy; kicked later
        self._gen = gen
        self._fault = False
        try:
            self._req = next(gen)
        except StopIteration:
            self._gen = None
            self._kick()

    def _kick(self) -> None:
        """Start the next queued job when the engine goes idle."""
        if self._gen is not None:
            return
        if self._pending:
            self._start(self._exec_gen(self._pending.pop(0)))
        elif self._rsp_queue:
            pkt = self._rsp_queue.pop(0)
            self._start(self._flush_gen(pkt))

    def _seq_gen(self, jobs: list[tuple]) -> Any:
        yield from jobs

    def _flush_gen(self, pkt: list[int]) -> Any:
        yield from self._putpkt(pkt)

    def _rd(self, addr: int) -> Any:
        v = yield ("r", addr & 0o3777777)
        if v is None and self._fault:
            raise _DmaFault
        return v

    def _rd_byte(self, addr: int) -> Any:
        v = yield ("rb", addr & 0o3777777)
        if v is None and self._fault:
            raise _DmaFault
        return v

    def _wr(self, addr: int, val: int) -> Any:
        yield ("w", addr & 0o3777777, val & 0o177777)

    def _wr_byte(self, addr: int, val: int) -> Any:
        yield ("wb", addr & 0o3777777, val & 0o377)

    def tick(self) -> None:
        """Advance the active DMA job by one bus operation (one stolen cycle)."""
        if self._gen is None:
            return
        req = self._req
        self._fault = False
        try:
            if req[0] == "r":
                self._ret = self._bus.read_word(req[1])
            elif req[0] == "rb":
                self._ret = self._bus.read_byte(req[1])
            elif req[0] == "w":
                self._bus.write_word(req[1], req[2])
                self._ret = None
            elif req[0] == "wb":
                self._bus.write_byte(req[1], req[2])
                self._ret = None
        except BusInit:
            self._fault = True
            self._ret = None
        try:
            self._req = self._gen.send(self._ret)
        except StopIteration:
            self._gen = None
            self._kick()

    # -- ring helpers (SIMH tq_getdesc/tq_putdesc) ----------------------------------
    def _getdesc(self, ring: dict[str, int]) -> Any:
        lo = yield from self._rd(ring["ba"] + ring["idx"])
        hi = yield from self._rd(ring["ba"] + ring["idx"] + 2)
        return lo | (hi << 16)

    def _putdesc(self, ring: dict[str, int], desc: int) -> Any:
        addr = ring["ba"] + ring["idx"]
        prvd = yield from self._getdesc(ring)
        newd = ((desc & ~UQ_DESC_OWN & 0xFFFFFFFF) | UQ_DESC_F) & 0xFFFFFFFF
        yield from self._wr(addr, newd & 0xFFFF)
        yield from self._wr(addr + 2, (newd >> 16) & 0xFFFF)
        ring["idx"] = (ring["idx"] + 4) & (ring["lnt"] - 1)
        if ring["lnt"] <= 4 or (prvd & UQ_DESC_OWN):
            yield from self._ring_int(ring)

    def _ring_int(self, ring: dict[str, int]) -> Any:
        yield from self._wr((self._comm + ring["ioff"]) & 0o3777777, 1)
        if self._vector:
            self._int_pending = True

    # -- polling and command dispatch (SIMH tq_quesvc / tq_getpkt) -------------------
    def _poll(self) -> None:
        """IP read while UP: poll the command ring once (SIMH tq_pip)."""
        if self._csta != CST_UP or self._gen is not None:
            return
        self._start(self._poll_gen())

    def _poll_gen(self) -> Any:
        desc = yield from self._getdesc(self._cq)
        if not (desc & UQ_DESC_OWN):
            return                                  # ring empty
        addr = desc & UQ_ADDR
        # fetch the fixed-size packet buffer (SIMH TQ_PKT_SIZE_W = 32 words,
        # header included at addr-4); the header length word is pkt[0]
        pkt = [0] * 32
        for i in range(len(pkt)):
            pkt[i] = yield from self._rd((addr - 4 + 2 * i) & 0o3777777)
        yield from self._putdesc(self._cq, desc)    # release the descriptor
        typ = (pkt[UQ_HCTC] >> UQ_HCTC_V_TYP) & 0xF
        cid = (pkt[UQ_HCTC] >> UQ_HCTC_V_CID) & 0xFF
        if typ != UQ_TYP_SEQ:
            self._fatal(PE_PIE)
            return
        if cid == UQ_CID_DUP:                        # datagram: reject politely
            pkt[CMD_OPC] = OP_END
            pkt[RSP_STS] = ST_CMD | I_OPCD
            yield from self._putpkt(self._build_response(pkt, 0, ST_CMD | I_OPCD, 0, RSP_LNT_MIN))
            return
        if cid != UQ_CID_TMSCP:
            self._fatal(PE_ICI)
            return
        opc = pkt[CMD_OPC] & 0xFF
        if opc in MOTION_OPS:
            # tape-motion latency, then execute (SIMH tq_xtime)
            self._clock.schedule(RECORD_CYCLES,
                                 lambda p=pkt: (self._pending.append(p), self._kick()))
        else:
            yield from self._exec_gen(pkt)

    # -- MSCP command execution (SIMH tq_mscp / tq_rdwr) -----------------------------
    def _exec_gen(self, pkt: list[int]) -> Any:
        opc = pkt[CMD_OPC] & 0xFF
        mod = pkt[CMD_MOD] & 0xFFFF
        unit = pkt[CMD_UN] & 0xFF
        bc = pkt[RW_BCL] | (pkt[RW_BCH] << 16)
        ba = pkt[RW_BAL] | (pkt[RW_BAH] << 16)
        sts = ST_SUC
        efl = 0
        rsz = 0
        lnt = RW_LNT_T
        online = self.tape is not None and unit == 0

        if opc in MOTION_OPS:
            if not online:
                sts = ST_OFL | SB_OFL_NV
                efl |= EF_SXC
            elif opc in (OP_WR, OP_WTM, OP_ERG, OP_ERS) and not self.writable:
                sts = ST_WPR | SB_WPR_HW
                efl |= EF_SXC

        if sts == ST_SUC:
            if opc == OP_SCC:
                lnt = SCC_LNT
            elif opc in (OP_GUS, OP_ONL, OP_SUC):
                lnt = GUS_LNT_T if opc == OP_GUS else ONL_LNT
                self._fill_unit_status(pkt, unit, online)
            elif opc == OP_AVL:
                lnt = AVL_LNT
            elif opc == OP_ABO:
                lnt = ABO_LNT
            elif opc == OP_GCS:
                lnt = GCS_LNT
            elif opc in (OP_DAP, OP_FLU):
                lnt = FLU_LNT if opc == OP_FLU else AVL_LNT
            elif opc in (OP_RD, OP_ACC, OP_CMP):
                rec = self.tape.read_record() if self.tape else -1
                if rec == -1:                       # EOM
                    efl |= EF_EOT
                elif rec is None:                   # tape mark
                    sts = ST_TMK
                    efl |= EF_SXC
                else:
                    n = min(len(rec), bc)
                    rsz = len(rec)
                    if len(rec) > bc:
                        sts = ST_RDT
                        efl |= EF_SXC
                    if opc == OP_RD and sts in (ST_SUC, ST_RDT):
                        try:
                            for i in range(n):
                                yield from self._wr_byte(ba + i, rec[i])
                        except _DmaFault:
                            pkt[RW_BCL] = 0
                            pkt[RW_BCH] = 0
                            sts = ST_HST | SB_HST_NXM
                            efl |= EF_LOG | EF_SXC
                            rsz = 0
                        else:
                            pkt[RW_BCL] = n & 0xFFFF
                            pkt[RW_BCH] = (n >> 16) & 0xFFFF
                    elif opc == OP_CMP and sts == ST_SUC:
                        try:
                            for i in range(n):
                                m = yield from self._rd_byte(ba + i)
                                if m != rec[i]:
                                    sts = ST_CMP
                                    efl |= EF_SXC
                                    break
                        except _DmaFault:
                            sts = ST_HST | SB_HST_NXM
                            efl |= EF_LOG | EF_SXC
                    # OP_ACC: reposition only, no data movement
                    if opc != OP_CMP or sts == ST_CMP:
                        pkt[RW_BCL] = pkt[RW_BCL] if opc == OP_RD else (n & 0xFFFF)
            elif opc == OP_WR:
                buf = bytearray()
                try:
                    for i in range(bc):
                        buf.append((yield from self._rd_byte(ba + i)) & 0xFF)
                except _DmaFault:
                    pkt[RW_BCL] = 0
                    pkt[RW_BCH] = 0
                    sts = ST_HST | SB_HST_NXM
                    efl |= EF_LOG | EF_SXC
                else:
                    self.tape.write_record(bytes(buf))
                    rsz = bc
                    pkt[RW_BCL] = bc & 0xFFFF
                    pkt[RW_BCH] = (bc >> 16) & 0xFFFF
            elif opc == OP_WTM:
                lnt = WTM_LNT
                self.tape.write_mark()
            elif opc == OP_ERG:
                lnt = ERG_LNT                       # erase gap: nop on .tap
            elif opc == OP_ERS:
                lnt = ERS_LNT
                self.tape.write_mark()              # end-of-medium marker
                self.tape.write_mark()
            elif opc == OP_POS:
                lnt = POS_LNT
                nrec = pkt[POS_RCL] | (pkt[POS_RCH] << 16)
                ntmk = pkt[POS_TMCL] | (pkt[POS_TMCH] << 16)
                skrec = sktmk = 0
                if mod & MD_RWD:
                    self.tape.rewind()
                else:
                    for _ in range(nrec + ntmk):
                        r = self.tape.space_back() if (mod & MD_REV) else self.tape.read_record()
                        if r == -1:
                            break                   # BOT / EOM
                        if r is None:
                            sktmk += 1
                        else:
                            skrec += 1
                pkt[POS_RCL] = skrec & 0xFFFF
                pkt[POS_RCH] = (skrec >> 16) & 0xFFFF
                pkt[POS_TMCL] = sktmk & 0xFFFF
                pkt[POS_TMCH] = (sktmk >> 16) & 0xFFFF
            else:
                sts = ST_CMD | I_OPCD
                lnt = RSP_LNT_MIN

        if opc in MOTION_OPS:
            pos = self.tape.pos if self.tape else 0
            pkt[RW_POSL] = pos & 0xFFFF
            pkt[RW_POSH] = (pos >> 16) & 0xFFFF
            pkt[RW_RSZL] = rsz & 0xFFFF
            pkt[RW_RSZH] = (rsz >> 16) & 0xFFFF

        yield from self._putpkt(self._build_response(pkt, opc, sts, efl, lnt))

    def _fill_unit_status(self, pkt: list[int], unit: int, online: bool) -> None:
        pkt[ONL_MLUN] = unit
        ufl = 0
        if online:
            ufl |= UF_RMV                           # removable media
            if not self.writable:
                ufl |= UF_WPH
            if self.tape is not None and self.tape.bot:
                ufl |= 0x0040                       # at BOT
        pkt[ONL_UFL] = ufl & 0xFFFF
        pkt[ONL_UIDA] = unit
        pkt[ONL_UIDB] = 0
        pkt[ONL_UIDC] = 0
        pkt[ONL_UIDD] = (UID_TAPE << 8) | self.drive_model
        pkt[ONL_MEDL] = 0
        pkt[ONL_MEDH] = 0
        pkt[ONL_FMT] = TF_CTP
        pkt[ONL_SPD] = 0
        pkt[ONL_MAXL] = TQ_MAXFR & 0xFFFF
        pkt[ONL_MAXH] = (TQ_MAXFR >> 16) & 0xFFFF
        pkt[ONL_NREC] = 0

    def _build_response(self, pkt: list[int], opc: int, sts: int, efl: int, lnt: int) -> list[int]:
        """Build the response packet in the same buffer (SIMH tq_putr)."""
        rsp = pkt[:]
        rsp[UQ_HLNT] = lnt
        rsp[UQ_HCTC] = (UQ_TYP_SEQ << UQ_HCTC_V_TYP) | (UQ_CID_TMSCP << UQ_HCTC_V_CID)
        rsp[RSP_OPF] = ((opc | OP_END) & 0xFF) | ((efl & 0xFF) << 8)
        rsp[RSP_STS] = sts & 0xFFFF
        return rsp

    def _putpkt(self, pkt: list[int]) -> Any:
        """Write a response into the next host-offered rsp ring entry
        (SIMH tq_putpkt)."""
        desc = yield from self._getdesc(self._rq)
        if not (desc & UQ_DESC_OWN):
            self._rsp_queue.append(pkt)             # ring full: queue it
            return
        addr = desc & UQ_ADDR
        lnt = pkt[UQ_HLNT] + 4                      # header + packet
        for i in range(lnt // 2):
            yield from self._wr((addr - 4 + 2 * i) & 0o3777777, pkt[i] & 0xFFFF)
        yield from self._putdesc(self._rq, desc)

    # -- interrupts (§6.1) ------------------------------------------------------------
    def interrupt_pending(self) -> tuple[int, int] | None:
        if self._int_pending and self._vector:
            return (self._vector, BR_LEVEL)
        return None

    # -- snapshot contract (§15) ------------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {"base": self.base, "csta": self._csta, "sa": self._sa,
                "saw": self._saw, "s1dat": self._s1dat, "comm": self._comm,
                "prgi": self._prgi, "vector": self._vector,
                "cq": dict(self._cq), "rq": dict(self._rq),
                "int_pending": self._int_pending, "writable": self.writable,
                "drive_model": self.drive_model,
                "rsp_queue_len": len(self._rsp_queue), "pending_len": len(self._pending),
                "tape_path": self.tape.path if self.tape else None,
                "tape_pos": self.tape.pos if self.tape else 0}

    def deserialize(self, state: dict[str, Any]) -> None:
        keys = ("base", "csta", "sa", "saw", "s1dat", "comm", "prgi", "vector",
                "cq", "rq", "int_pending", "writable", "drive_model",
                "rsp_queue_len", "pending_len", "tape_path", "tape_pos")
        for key in keys:
            if key not in state:
                raise ValueError(f"TQK11 snapshot missing {key!r}")
        self.base = state["base"]
        self._csta = state["csta"]
        self._sa = state["sa"]
        self._saw = state["saw"]
        self._s1dat = state["s1dat"]
        self._comm = state["comm"]
        self._prgi = state["prgi"]
        self._vector = state["vector"]
        self._cq = dict(state["cq"])
        self._rq = dict(state["rq"])
        self._int_pending = state["int_pending"]
        self.writable = state["writable"]
        self.drive_model = state["drive_model"]
        self._rsp_queue = []
        self._pending = []
        self._gen = None                            # in-flight DMA jobs are not restorable
        if self.tape is not None:
            self.tape.pos = state["tape_pos"]
