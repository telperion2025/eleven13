"""
VT11-class vector graphics device (spec §3 layout, §8): a calligraphic
display processor with a pixel framebuffer, target for the optional pixel
render backend ([pixel] extra, pygame/tkinter).

Cross-checked against SIMH pdp11_vt.c + display/vt11.c and DEC's VT11
Graphic Display Processor manual (EK-VT11-TM-001, bitsavers.org) plus
Al Kossow's VT11 instruction-set posting (brouhaha.com/~eric/.../vt11_inst_set.html):

- register window at 176600 (Unibus; bootstrap ROM at 17766000 on 22-bit),
  0o40 bytes: DPC +0 (display PC; write = start/resume + clear interrupts,
  read = current DPC), MPR +2 (mode/intensity/light-pen control), XPR +4
  (graphplot increment <15:10> + X position <9:0>), YPR +6 (char code
  <15:10> + Y position <9:0>). Offsets 010-036 are VS60-only and raise
  bus timeout (NXM) on a VT11, as in SIMH.
- display list: 16-bit words fetched one per stolen NPR cycle via the
  DMAHost face (like the other DMA devices). Bit 15 set = control word:
  bits 14-11 select the graphic mode (0 CHAR, 1 SHORTV, 2 LONGV, 3 POINT,
  4 GRAPHX, 5 GRAPHY, 6 RELPOINT), bit 10 enables bits 9-7 into the
  intensity register, bit 6 = light-pen interrupt enable, bit 5 = LP
  enable. DNOP = 0o164000, DSTOP = 0o173400, JMPA = 0o140000 | addr.
  Data words (bit 15 clear) per mode: SHORTV <9> intensify, <8:5> dy,
  <4:0> dx (signed); LONGV two words <9:0> dx, dy (10-bit signed);
  POINT two words <9:0> x, y; RELPOINT two words dx, dy; GRAPHX/GRAPHY
  <15:10> step, <9:0> coordinate; CHAR low 6 bits = char code (beam
  advances by the character spacing; no font stroking in v1, §8).
- coordinates are 12-bit (4096x4096 address space, wrapping), clipped to
  the visible area: VR17 1024x1024, VR14 1024x768.
- DSTOP or a failed fetch stops the display processor and posts the STOP
  interrupt at vector base 0320 (BR4; SIMH IPL_VTST=4, default vector
  base 0320). Light-pen/char/name interrupts are not generated in v1
  (no host pointing device wired to the pixel backend yet).
"""

from __future__ import annotations

import logging
from typing import Any

from pdp11.bus.abstract_bus import BusDevice, BusInit, DMAHost
from pdp11.system_clock import SystemClock

_log = logging.getLogger(__name__)

VT11_BASE = 0o176600
DPC, MPR, XPR, YPR = 0o00, 0o02, 0o04, 0o06

VECTOR_BASE = 0o320                 # SIMH default VT vector base (STOP at +0)
BR_LEVEL = 4                        # SIMH IPL_VTST/VTLP/VTCH/VTNM = 4

# control words (bit 15 set)
CW_MODE = 0o100000                  # | mode<<11 | intena<<10 | intensity<<7 | lp
MODE_CHAR, MODE_SVECTOR, MODE_LVECTOR, MODE_POINT = 0, 1, 2, 3
MODE_GRAPHX, MODE_GRAPHY, MODE_RELPOINT = 4, 5, 6
DNOP = 0o164000
DSTOP = 0o173400
JMPA = 0o140000                     # | 10-bit absolute word address

# MPR bits
MPR_STOP = 0x8000
MPR_LPHIT = 0x0080
MPR_SO = 0x0040
MPR_EDGE = 0x0020
MPR_ITAL = 0x0010
MPR_BLINK = 0x0008
MPR_EDGEF = 0x0004
MPR_LPENA = 0x0020                  # write side: LP enable
MPR_LPINT = 0x0040                  # write side: LP interrupt enable

COORD_BITS = 12                     # VT11 12-bit coordinate registers
COORD_MASK = (1 << COORD_BITS) - 1

FB_WIDTH = 1024
FB_HEIGHT_VR17 = 1024
FB_HEIGHT_VR14 = 768

CHAR_SPACING_W = 14                 # VT11 normal horizontal spacing
CHAR_SPACING_H = 24


def _sext(value: int, bits: int) -> int:
    """Sign-extend a `bits`-wide two's-complement field."""
    sign = 1 << (bits - 1)
    return (value & (sign - 1)) - (value & sign)


class Framebuffer:
    """1-bit pixel framebuffer (VR17 1024x1024 / VR14 1024x768 visible)."""

    def __init__(self, width: int = FB_WIDTH, height: int = FB_HEIGHT_VR17) -> None:
        self.width = width
        self.height = height
        self.bits = bytearray((width * height) // 8)

    def clear(self) -> None:
        self.bits = bytearray(len(self.bits))

    def set_pixel(self, x: int, y: int) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = y * self.width + x
            self.bits[i >> 3] |= 1 << (i & 7)

    def pixel(self, x: int, y: int) -> int:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = y * self.width + x
            return (self.bits[i >> 3] >> (i & 7)) & 1
        return 0

    def draw_line(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Bresenham; endpoints included; clips to the visible area."""
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.set_pixel(x0, y0)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy


class VT11(BusDevice):
    """VT11 display processor: DPC/MPR/XPR/YPR register file plus the
    display-list engine rendering into a Framebuffer. One display-file
    word is fetched per stolen NPR(BDMG) cycle through the DMAHost face."""

    window_size = 0o40
    name = "VT11"

    def __init__(self, clock: SystemClock, bus: DMAHost, base: int = VT11_BASE,
                 crt: str = "VR17") -> None:
        self._clock = clock
        self._bus = bus
        self.base = base
        height = FB_HEIGHT_VR14 if crt == "VR14" else FB_HEIGHT_VR17
        self.fb = Framebuffer(FB_WIDTH, height)
        self.crt = crt
        self.reset()

    # -- lifecycle -------------------------------------------------------------
    def reset(self) -> None:
        self._dpc = 0
        self._mode = MODE_CHAR
        self._intensity = 4
        self._lp_int = False
        self._lp_ena = False
        self._internal_stop = False
        self._lp_hit = False
        self._x = 0                      # 12-bit beam position (wrapping)
        self._y = 0
        self._char_buf = 0
        self._gstep = 0                  # graphplot increment
        self._running = False
        self._word_no = -1               # -1 = control mode, >=0 = data word idx
        self._pending: list[int] = []    # data words of a multi-word op
        self._int_pending = False
        self._fetches = 0

    # -- register window ---------------------------------------------------------
    def read_word(self, offset: int) -> int:
        reg = offset & 0o177776
        if reg == DPC:
            return self._dpc & 0o177777
        if reg == MPR:
            return (self._mpr_bits()) & 0o177777
        if reg == XPR:
            return ((self._gstep & 0o77) << 10) | ((self._x >> 2) & 0o1777)
        if reg == YPR:
            return ((self._char_buf & 0o77) << 10) | ((self._y >> 2) & 0o1777)
        raise BusInit(f"VT11: VS60-only register at octal {oct(self.base + reg)} "
                      "does not respond on a VT11 (NXM)")

    def write_word(self, offset: int, value: int) -> None:
        reg = offset & 0o177776
        v = value & 0o177777
        if reg == DPC:
            self._dpc = v & 0o177776         # even; bit 0 = resume flag, ignored
            self._internal_stop = False
            self._int_pending = False        # DPC write clears VT interrupts (SIMH)
            self._running = True             # start the display processor
            self._word_no = -1
            self._pending = []
            return
        if reg == MPR:
            self._mode = (v >> 11) & 0xF
            if v & 0o400:                    # bit 10: load intensity from 9-7
                self._intensity = (v >> 7) & 7
            self._lp_int = bool(v & MPR_LPINT)
            self._lp_ena = bool(v & MPR_LPENA)
            return
        if reg == XPR:
            self._gstep = (v >> 10) & 0o77
            self._x = ((v & 0o1777) << 2) & COORD_MASK
            return
        if reg == YPR:
            self._char_buf = (v >> 10) & 0o77
            self._y = ((v & 0o1777) << 2) & COORD_MASK
            return
        raise BusInit(f"VT11: VS60-only register at octal {oct(self.base + reg)} "
                      "does not respond on a VT11 (NXM)")

    def _mpr_bits(self) -> int:
        v = (self._mode & 0xF) << 11
        v |= (self._intensity & 7) << 8
        if self._internal_stop:
            v |= MPR_STOP
        if self._lp_hit:
            v |= MPR_LPHIT
        return v

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

    # -- display processor engine --------------------------------------------------
    def dma_request(self) -> bool:
        return self._running

    def tick(self) -> None:
        """Fetch and execute one display-file word (one stolen bus cycle)."""
        if not self._running:
            return
        try:
            w = self._bus.read_word(self._dpc & 0o177777)
        except BusInit:
            w = DSTOP                          # SIMH vt_fetch: NXM -> DSTOP
        self._fetches += 1
        self._dpc = (self._dpc + 2) & 0o177777
        self._execute(w & 0o177777)

    def _execute(self, w: int) -> None:
        if w & 0x8000:                          # control word
            self._word_no = -1
            self._pending = []
            m = (w >> 11) & 0xF                 # bits 14-11
            if m <= 7:                          # set graphic mode (+intensity/LP)
                self._mode = m
                if w & 0o400:                   # bit 10: load intensity from bits 9-7
                    self._intensity = (w >> 7) & 7
                self._lp_int = bool(w & 0o100)  # bit 6: LP interrupt enable
                self._lp_ena = bool(w & 0o40)   # bit 5: LP enable
            elif w == DSTOP:
                self._stop()
            elif w == DNOP:
                pass
            elif m == 8:                        # JMPA: 0o140000 | 10-bit word addr
                self._dpc = (w & 0o1777) << 1
            else:
                pass                            # other control ops: no-op advance
            return
        # data word, interpreted in the current graphic mode
        if self._mode in (MODE_LVECTOR, MODE_POINT, MODE_RELPOINT):
            self._pending.append(w)
            if len(self._pending) < 2:
                return                          # need the second word
            w1, w2 = self._pending
            self._pending = []
            if self._mode == MODE_POINT:
                self._move_to(w1 & 0o1777, w2 & 0o1777)
                self.fb.set_pixel(self._x, self._y)
            elif self._mode == MODE_RELPOINT:
                self._move_by(_sext(w1 & 0o1777, 10), _sext(w2 & 0o1777, 10))
                self.fb.set_pixel(self._x, self._y)
            else:                               # long vector
                self._draw_to(self._x + (_sext(w1 & 0o1777, 10) << 2),
                              self._y + (_sext(w2 & 0o1777, 10) << 2))
            return
        if self._mode == MODE_SVECTOR:
            intensify = (w >> 9) & 1
            dx = _sext(w & 0o37, 5)
            dy = _sext((w >> 5) & 0o17, 4)
            if intensify:
                self._draw_to(self._x + (dx << 2), self._y + (dy << 2))
            else:
                self._move_by(dx, dy)
            return
        if self._mode in (MODE_GRAPHX, MODE_GRAPHY):
            self._gstep = (w >> 10) & 0o77
            coord = (w & 0o1777) << 2
            if self._mode == MODE_GRAPHX:
                self._x = coord & COORD_MASK
                self.fb.set_pixel(self._x, self._y)
            else:
                self._y = coord & COORD_MASK
                self.fb.set_pixel(self._x, self._y)
            return
        if self._mode == MODE_CHAR:
            self._char_buf = w & 0o77
            self._x = (self._x + CHAR_SPACING_W) & COORD_MASK   # no font stroke in v1
            return
        # spare mode 7: treat as no-op data
        self._word_no = -1

    def _move_to(self, x: int, y: int) -> None:
        self._x = (x << 2) & COORD_MASK
        self._y = (y << 2) & COORD_MASK

    def _move_by(self, dx: int, dy: int) -> None:
        self._x = (self._x + (dx << 2)) & COORD_MASK
        self._y = (self._y + (dy << 2)) & COORD_MASK

    def _draw_to(self, x: int, y: int) -> None:
        x &= COORD_MASK
        y &= COORD_MASK
        self.fb.draw_line(self._x, self._y, x, y)
        self._x = x
        self._y = y

    def _stop(self) -> None:
        self._running = False
        self._internal_stop = True
        self._int_pending = True               # STOP interrupt at vector base

    # -- interrupts (§6.1) ------------------------------------------------------------
    def interrupt_pending(self) -> tuple[int, int] | None:
        if self._int_pending:
            return (VECTOR_BASE, BR_LEVEL)
        return None

    # -- snapshot contract (§15) ------------------------------------------------------
    def serialize(self) -> dict[str, Any]:
        return {"base": self.base, "crt": self.crt, "dpc": self._dpc,
                "mode": self._mode, "intensity": self._intensity,
                "lp_int": self._lp_int, "lp_ena": self._lp_ena,
                "internal_stop": self._internal_stop, "lp_hit": self._lp_hit,
                "x": self._x, "y": self._y, "char_buf": self._char_buf,
                "gstep": self._gstep, "running": self._running,
                "word_no": self._word_no, "pending": list(self._pending),
                "int_pending": self._int_pending, "fetches": self._fetches,
                "fb": bytes(self.fb.bits).hex()}

    def deserialize(self, state: dict[str, Any]) -> None:
        keys = ("base", "crt", "dpc", "mode", "intensity", "lp_int", "lp_ena",
                "internal_stop", "lp_hit", "x", "y", "char_buf", "gstep",
                "running", "word_no", "pending", "int_pending", "fetches", "fb")
        for key in keys:
            if key not in state:
                raise ValueError(f"VT11 snapshot missing {key!r}")
        self.base = state["base"]
        self.crt = state["crt"]
        height = FB_HEIGHT_VR14 if self.crt == "VR14" else FB_HEIGHT_VR17
        self.fb = Framebuffer(FB_WIDTH, height)
        self.fb.bits = bytearray(bytes.fromhex(state["fb"]))
        self._dpc = state["dpc"]
        self._mode = state["mode"]
        self._intensity = state["intensity"]
        self._lp_int = state["lp_int"]
        self._lp_ena = state["lp_ena"]
        self._internal_stop = state["internal_stop"]
        self._lp_hit = state["lp_hit"]
        self._x = state["x"]
        self._y = state["y"]
        self._char_buf = state["char_buf"]
        self._gstep = state["gstep"]
        self._running = state["running"]
        self._word_no = state["word_no"]
        self._pending = list(state["pending"])
        self._int_pending = state["int_pending"]
        self._fetches = state["fetches"]
