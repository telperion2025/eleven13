"""
Graphics / terminal frontends (spec §8). Console traffic from the DL11
flows through terminal.py to a virtual terminal; only the render backends
touch host I/O (render_backend.py is one of the two modules allowed time.*,
§19.6 — the other is main.py).

Character-encoding contract (§8): the virtual terminals are strictly 7-bit
ASCII devices; host Unicode keys with no 7-bit equivalent are dropped with
a debug-log note, never passed through as multi-byte garbage.

M7 stubs per §19.10.
"""
