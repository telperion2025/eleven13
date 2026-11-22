"""
CPU core: registers and PSW (registers.py), the fetch/decode/execute loop
(core.py), instruction handlers (instructions/), MMU (mmu.py), the FP11
arithmetic engine (fp11.py), interrupt scheduling (interrupts.py) and trap
dispatch (traps.py).

The core is a pure state machine: no wall-clock reads, no host I/O, no
threading — all timing flows through system_clock.SystemClock (spec §5.6).
"""
