"""
pdp11 — a modular DEC PDP-11 emulator.

Emulates the PDP-11/03, 11/20, 11/70 and 11/94 from selectable machine
profiles. Machines are assembled at startup from a MachineProfile
(see config/profiles.py): CPU capabilities, bus type, memory layout and
default device set are all profile-driven, never hard-coded.

Subsystems:
    cpu/      instruction set, MMU, FPU, CIS, traps, interrupts
    bus/      Unibus, Q-Bus, Massbus arbitration and device contract
    memory/   RAM, ROM, I/O page, Unibus/Q-Bus map registers
    devices/  DL11, DZ11, RK05, RX01, RH70/RP06, TM11, clocks, ...
    graphics/ terminal frontends (VT100/VT52/LA36) and render backends
    control/  ODT front panel, debugger, snapshots, ROM loader
"""

__version__ = "0.1.0"  # M1: skeleton + 11/20 base CPU only
