"""
Peripheral modules (spec §7). Each device is (a) its CSR/register file
exposed through the I/O page and (b) a tick()/scheduled-event state machine
for data transfer, errors and interrupts — every device implements the
BusDevice contract including serialize()/deserialize() (§15).

M1: all device modules are documented stubs raising NotImplementedError per
§19.10; the P0 boot set (DL11 console, KW11-L, RK05/RK11, RX01, TU10/TM11)
lands in M2-M3, P1 (DZ11, RL11, PC11) in M4+, P2 (RH70, TQK11, KW11-P) in
M5/M6.
"""
