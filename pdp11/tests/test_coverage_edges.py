"""
Edge-coverage tests — M8: pins the defensive/error paths that the §18.2
coverage gate counts (cpu/ >= 90%): WAIT parking, JMP mode-0 reserved trap,
ASH with zero count, interrupt-pending scan with a live device, no-bus
EmulationError paths, FP11 schema validation, FP register-deferred operand.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from pdp11.config.profiles import PROFILES
from pdp11.cpu import interrupts, traps
from pdp11.cpu.core import CPU, read_op
from pdp11.cpu.fp11 import FP11, P_F
from pdp11.errors import EmulationError
from pdp11.system_clock import SystemClock
from pdp11.tests.cpu_golden import make_machine, one_step
from pdp11.tests.test_console import make_board


def test_wait_instruction_parks_cpu():
    cpu, _ = one_step([0o000001], profile_key="11/20")     # WAIT
    assert cpu.state.waiting is True


def test_jmp_mode0_is_reserved():
    cpu, board = one_step([0o000100], mem={0o010: 0o500, 0o012: 0}, profile_key="11/20")
    assert cpu.state.R[7] == 0o500                          # vector 000010 handler
    assert board.ram.read_word(0o010) == 0o500


def test_ash_zero_count_is_identity():
    cpu, _ = one_step([0o072201], regs={1: 0, 2: 0o1234}, profile_key="11/03")  # ASH R1,R2
    assert cpu.state.R[2] == 0o1234                         # count 0: unchanged


def test_interrupt_pending_scan_with_live_device():
    board, _ = make_board("11/20")
    dl11 = next(d for d in board.devices if d.name == "DL11")
    dl11.rx_done = True
    dl11.rx_ie = True
    assert interrupts.pending(board.devices) == (0o000060, 4)   # DL11 RX vector/BR4


def test_no_bus_paths_raise_emulation_error():
    cpu = CPU(PROFILES["11/20"], SystemClock())             # no bus injected
    with pytest.raises(EmulationError):
        cpu.step()
    with pytest.raises(EmulationError):
        read_op(cpu, 0, -1, False)
    with pytest.raises(EmulationError):
        traps.trap(cpu, 0o000004)


def test_fp11_deserialize_schema_validation():
    with pytest.raises(ValueError, match="missing"):
        FP11().deserialize({"fps": 0})
    with pytest.raises(ValueError, match="6 accumulators"):
        FP11().deserialize({"fps": 0, "fec": 0, "fea": 0, "ac": [0]})


def test_ldf_register_deferred_operand():
    # SETF then LDF AC0,(R0) with R0 -> 0o2000 holding 1.0F
    cpu, board = make_machine("11/70", program=[0o170001, 0o172410, 0o000000], pc=0o100)
    board.ram.write_word(0o2000, 0)
    board.ram.write_word(0o2002, 0o040000)                  # 1.0F
    cpu.state.R[0] = 0o2000
    from pdp11.tests.cpu_golden import step_n
    step_n(cpu, 3)
    assert cpu.state.halted
    assert cpu.state.fp._value(cpu.state.fp.ac[0], P_F) == Fraction(1)
