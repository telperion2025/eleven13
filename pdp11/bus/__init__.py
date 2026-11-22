"""
Bus layer: the BusDevice contract (abstract_bus.py) and the concrete bus
arbitration modules — Unibus (unibus.py, M2), Q-Bus (qbus.py, M4/M6) and
Massbus (massbus.py, M5). Devices and memory attach to a bus; the CPU only
ever sees the bus through the single MemoryInterface (board.py).
"""
