"""
Graphics / terminal frontends (spec §8). Console traffic from the DL11
flows through terminal.py to a virtual terminal; only the render backends
touch host I/O (render_backend.py is one of the two modules allowed time.*,
§19.6 — the other is main.py).

Character-encoding contract (§8): the virtual terminals are strictly 7-bit
ASCII devices; host Unicode keys with no 7-bit equivalent are dropped with
a debug-log note, never passed through as multi-byte garbage.

M7 implements the VT100/VT52 terminals (vt100.py), the LA36 hardcopy
(la36.py), and the shared BackendTerminal input queue (terminal.py); the
curses text-grid backend and the optional pixel backend land post-v1 (§8);
framebuffer.py is the VT11-class vector display (DPC/MPR/XPR/YPR at 176600
plus a pixel framebuffer for the pixel backend), attached with `--vt11`.
"""
