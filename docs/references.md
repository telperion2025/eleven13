# Primary References (spec §21 — authoritative over implementer memory)

All register layouts, timing, and quirk claims cite back to this list;
consult these before finalizing any module:

- **PDP-11 Processor Handbook** (Digital Equipment Corporation, 1975/1976) —
  instruction set, base-cycle timing tables, address modes.
- **PDP-11 04/24/34/45/55/60/70 Processor Handbooks** — per-machine deltas;
  the 1973 edition covers the 11/20's non-microcoded subset.
- **PDP-11/70 Processor Handbook / Intelligent System Manual** — MMU,
  three-mode operation, Unibus map, Massbus (EK-1170B-MM-001).
- **DCJ-11 (J-11) chip documentation / PDP-11 documentation set** — 11/94
  on-chip units, 22-bit Q-Bus mapping.
- **PDP-11 Peripherals Handbook** (1976/1979) — RK11/RK05, RL11, RX11, PC11,
  DL11, DZ11, TM11, RH70 registers and programming examples.
- **DEC MAINDEC diagnostics** for each machine (SIMH kits / bitsavers) — used
  directly as test artifacts (§16, §11).
- **SIMH source code** — precedent for the §5.6 timing model and §16 SIMH
  container formats.
- **The PDP-11 FAQ** (archived Usenet FAQ) and community field guides —
  sanity checks on per-machine capability flags (EIS/CIS/FPU presence, odd
  memory sizes).
