"""
Peripheral modules (spec §7). Each device is (a) its CSR/register file
exposed through the I/O page and (b) a tick()/scheduled-event state machine
for data transfer, errors and interrupts — every device implements the
BusDevice contract including serialize()/deserialize() (§15).

M1 made all device modules documented stubs raising NotImplementedError per
§19.10; M2-M8 landed the P0/P1 set (DL11, KW11-L, RK11/RK05, RX11, DZ11,
RH70/RP) and post-M8 added TM11/TU10, RL11/RL01/RL02, PC11, KW11-P and the
TQK11 MSCP command-ring layer (register/ring/packet layouts cross-checked
against SIMH pdp11_tm/rl/pt/pclk/tq.c + pdp11_uqssp.h/pdp11_mscp.h — see
docs/acceptance-checklist.md). No loud stubs remain; the CIS CVT* ops
landed too (cpu/instructions/cis.py) — unused opcodes trap reserved (§5.8).
"""
