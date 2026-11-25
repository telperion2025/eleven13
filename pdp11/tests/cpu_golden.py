"""
CPU golden tests (spec §3): instruction-level assertions over known bit
patterns, checked against the PDP-11 Processor Handbook examples (§10.6).

The headline test is the M1 acceptance transcript (§22.2): a hand-assembled
decrement loop whose full (PC, opcode) trace and final register/PSW state
are pinned exactly, plus a replay-determinism check (§5.6, §11.7/§18.2).

Hand-assembled words used below (octal):
    012700 000005   MOV #5,R0
    005300          DEC R0
    001376          BNE .-2  (offset 0o376 = -2 words, back to the DEC)
    000000          HALT
"""

from __future__ import annotations

from typing import Any

import pytest

from pdp11.board import SystemBoard
from pdp11.config.profiles import PROFILES
from pdp11.cpu.core import CPU
from pdp11.cpu.registers import PSW, Reg
from pdp11.system_clock import SystemClock


def make_machine(profile_key: str = "11/20", ram_words: int = 0o20000,
                 program: list[int] | None = None, pc: int = 0o000100,
                 sp: int = 0o017776) -> tuple[CPU, SystemBoard]:
    """Assemble a machine from its profile; load `program` at octal `pc`,
    set PC/SP, and return (cpu, board) for direct stepping."""
    profile = PROFILES[profile_key]
    clock = SystemClock()
    board = SystemBoard(profile, clock, ram_words=ram_words)
    cpu = board.cpu
    if program:
        for i, word in enumerate(program):
            board.ram.write_word(pc + 2 * i, word)
    cpu.state.R[Reg.PC] = pc
    cpu.state.R[Reg.SP] = sp
    return cpu, board


def step_n(cpu: CPU, n: int) -> list[tuple[int, int]]:
    """Run up to n steps (stopping at HALT); return the (PC, opcode) trace."""
    trace: list[tuple[int, int]] = []
    for _ in range(n):
        if cpu.state.halted:
            break
        pc = cpu.state.R[Reg.PC]
        cpu.step()
        trace.append((pc, cpu.last_op_word))
    return trace


def one_step(program: list[int], regs: dict[int, int] | None = None,
             mem: dict[int, int] | None = None, pc: int = 0o000100,
             profile_key: str = "11/20") -> tuple[CPU, SystemBoard]:
    """Fresh machine, program at `pc`, registers/memory preset, ONE step."""
    cpu, board = make_machine(profile_key=profile_key, program=program, pc=pc)
    for r, v in (regs or {}).items():
        cpu.state.R[r] = v
    for addr, word in (mem or {}).items():
        board.ram.write_word(addr, word)
    cpu.step()
    return cpu, board


def psw_bits(cpu: CPU) -> tuple[bool, bool, bool, bool, bool]:
    """(N, Z, V, C, T) of the PSW."""
    psw = cpu.state.psw
    return (psw.get(PSW.N), psw.get(PSW.Z), psw.get(PSW.V),
            psw.get(PSW.C), psw.get(PSW.T))


# ---------------------------------------------------------------------------
# M1 acceptance transcript: the decrement loop (§22.2)
# ---------------------------------------------------------------------------

DECREMENT_LOOP = [0o012700, 0o000005, 0o005300, 0o001376, 0o000000]
EXPECTED_TRACE = [
    (0o000000, 0o012700),                        # MOV #5,R0
    (0o000004, 0o005300), (0o000006, 0o001376),  # DEC R0 / BNE taken, 4 times
    (0o000004, 0o005300), (0o000006, 0o001376),
    (0o000004, 0o005300), (0o000006, 0o001376),
    (0o000004, 0o005300), (0o000006, 0o001376),
    (0o000004, 0o005300), (0o000006, 0o001376),  # 5th DEC -> 0: BNE falls through
    (0o000010, 0o000000),                        # HALT
]


def test_decrement_loop_golden_transcript():
    """Hand-built decrement loop: exact (PC, opcode) trace, R0=0, Z set,
    machine halted — the §22.2 M1 golden transcript."""
    cpu, _board = make_machine(program=DECREMENT_LOOP, pc=0o000000)
    trace = step_n(cpu, 100)
    assert trace == EXPECTED_TRACE
    assert cpu.state.R[Reg.R0] == 0
    assert cpu.state.psw.get(PSW.Z) and not cpu.state.psw.get(PSW.N)
    assert cpu.state.halted


def test_decrement_loop_replays_identically():
    """Determinism (§5.6, §11.7): two fresh machines produce the identical
    trace, register state and bus-cycle count."""
    cpu1, board1 = make_machine(program=DECREMENT_LOOP, pc=0o000000)
    cpu2, board2 = make_machine(program=DECREMENT_LOOP, pc=0o000000)
    trace1 = step_n(cpu1, 100)
    trace2 = step_n(cpu2, 100)
    assert trace1 == trace2
    assert list(cpu1.state.R) == list(cpu2.state.R)
    assert cpu1.state.psw.value == cpu2.state.psw.value
    assert board1.clock.cycle == board2.clock.cycle
    assert board1.clock.cycle > 0            # cost table advanced the clock (§17.10.6)


# ---------------------------------------------------------------------------
# Single-instruction golden cases (Processor Handbook examples)
# ---------------------------------------------------------------------------

def test_golden_mov_reg():
    cpu, _ = one_step([0o010102], regs={1: 0o123456})            # MOV R1,R2
    assert cpu.state.R[2] == 0o123456
    assert psw_bits(cpu)[:3] == (True, False, False)             # N=1 Z=0 V=0


def test_golden_movb_immediate():
    cpu, _ = one_step([0o112700, 0o000101], regs={0: 0o177777})  # MOVB #0o101,R0
    assert cpu.state.R[0] == 0o177501                            # low byte only; high kept
    assert psw_bits(cpu)[:3] == (False, False, False)


def test_golden_add_carry():
    cpu, _ = one_step([0o060102], regs={1: 1, 2: 0o177777})      # ADD R1,R2
    assert cpu.state.R[2] == 0
    assert psw_bits(cpu) == (False, True, False, True, False)    # Z=1 C=1


def test_golden_add_overflow():
    cpu, _ = one_step([0o060102], regs={1: 1, 2: 0o077777})      # 0o77777+1
    assert cpu.state.R[2] == 0o100000
    assert psw_bits(cpu) == (True, False, True, False, False)    # N=1 V=1 C=0


def test_golden_sub_borrow():
    cpu, _ = one_step([0o160102], regs={1: 1, 2: 0})             # SUB R1,R2
    assert cpu.state.R[2] == 0o177777
    assert psw_bits(cpu) == (True, False, False, True, False)    # N=1 C=borrow


def test_golden_cmp_equal():
    cpu, _ = one_step([0o020102], regs={1: 5, 2: 5})             # CMP R1,R2
    assert cpu.state.R[2] == 5                                   # nothing written
    assert psw_bits(cpu)[:4] == (False, True, False, False)      # Z=1 C=0


def test_golden_bit_bic_bis():
    cpu, _ = one_step([0o030102], regs={1: 0o000017, 2: 0o000010})   # BIT R1,R2
    assert psw_bits(cpu)[:3] == (False, False, False)            # 0o10: Z=0 N=0 V=0
    cpu, _ = one_step([0o040102], regs={1: 0o000017, 2: 0o177777})   # BIC R1,R2
    assert cpu.state.R[2] == 0o177760 and psw_bits(cpu)[0] is True
    cpu, _ = one_step([0o050102], regs={1: 0o007777, 2: 0o170000})   # BIS R1,R2
    assert cpu.state.R[2] == 0o177777


def test_golden_single_operand_group():
    cpu, _ = one_step([0o005003], regs={3: 0o123456})
    assert cpu.state.R[3] == 0
    assert psw_bits(cpu) == (False, True, False, False, False)   # CLR: Z=1 C=0
    cpu, _ = one_step([0o005103], regs={3: 0})
    assert cpu.state.R[3] == 0o177777
    assert psw_bits(cpu)[:4] == (True, False, False, True)       # COM: N=1 C=1
    cpu, _ = one_step([0o005203], regs={3: 0o077777})
    assert cpu.state.R[3] == 0o100000 and psw_bits(cpu)[2] is True   # INC: V=1
    cpu, _ = one_step([0o005303], regs={3: 0o100000})
    assert cpu.state.R[3] == 0o077777 and psw_bits(cpu)[2] is True   # DEC: V=1
    cpu, _ = one_step([0o005403], regs={3: 1})
    assert cpu.state.R[3] == 0o177777
    assert psw_bits(cpu)[:4] == (True, False, False, True)       # NEG: N=1 C=1
    cpu, _ = one_step([0o005703], regs={3: 0})                    # TST R3
    assert psw_bits(cpu) == (False, True, False, False, False)   # Z=1 V=0 C=0


def test_golden_adc_sbc():
    # SEC ; ADC R3 with R3=0o177777, C=1 -> 0, Z=1, C=1
    cpu, _ = one_step([0o000261, 0o005503], regs={3: 0o177777})
    cpu.step()
    assert cpu.state.R[3] == 0
    assert psw_bits(cpu)[1] is True and psw_bits(cpu)[3] is True
    # SEC ; SBC R3 with R3=0, C=1 -> 0o177777, C=1, N=1
    cpu, _ = one_step([0o000261, 0o005603], regs={3: 0})
    cpu.step()
    assert cpu.state.R[3] == 0o177777
    assert psw_bits(cpu) == (True, False, False, True, False)


def test_golden_shifts_rotates_swab():
    cpu, _ = one_step([0o006003], regs={3: 3})                   # ROR R3: 3->1, C=1
    assert cpu.state.R[3] == 1 and psw_bits(cpu)[3] is True
    cpu, _ = one_step([0o006103], regs={3: 0o100000})            # ROL R3: bit15->C, 0 in
    assert cpu.state.R[3] == 0 and psw_bits(cpu)[3] is True      # R3=0, C=1
    cpu, _ = one_step([0o006203], regs={3: 0o100002})            # ASR: sign preserved
    assert cpu.state.R[3] == 0o140001 and psw_bits(cpu)[0] is True
    cpu, _ = one_step([0o006303], regs={3: 0o100001})            # ASL: C=1, V=N^C=1
    assert cpu.state.R[3] == 2 and psw_bits(cpu)[3] is True and psw_bits(cpu)[2] is True
    cpu, _ = one_step([0o000303], regs={3: 0o000102})             # SWAB 0x0042 -> 0x4200
    assert cpu.state.R[3] == 0o041000


def test_golden_branches():
    # DEC R0 (R0=2 -> 1, Z=0); BNE -2 taken back to the DEC at 0o100
    cpu, _ = one_step([0o005300, 0o001376], regs={0: 2}, pc=0o000100)
    cpu.step()                                                     # BNE at 0o102, taken
    assert cpu.state.R[Reg.PC] == 0o000100
    # BEQ with Z=0: not taken
    cpu, _ = one_step([0o001400], pc=0o000100)
    assert cpu.state.R[Reg.PC] == 0o000102
    # BCS +2 with C=1: taken -> PC = 0o102 + 2*2 = 0o106
    cpu, _ = make_machine(program=[0o103402], pc=0o000100)
    cpu.state.psw.set(PSW.C, True)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o000106


# ---------------------------------------------------------------------------
# Address modes (§5.2) — including the PC special cases
# ---------------------------------------------------------------------------

def test_address_modes_memory():
    cpu, _ = one_step([0o011102], regs={1: 0o000200}, mem={0o000200: 0o123456})  # MOV (R1),R2
    assert cpu.state.R[2] == 0o123456 and cpu.state.R[1] == 0o000200
    cpu, _ = one_step([0o012102], regs={1: 0o000200}, mem={0o000200: 0o123456})  # MOV (R1)+,R2
    assert cpu.state.R[2] == 0o123456 and cpu.state.R[1] == 0o000202
    cpu, _ = one_step([0o112102], regs={1: 0o000200}, mem={0o000200: 0o123456})  # MOVB (R1)+,R2
    assert cpu.state.R[2] == 0o056 and cpu.state.R[1] == 0o000201                # byte: +1
    cpu, _ = one_step([0o013102], regs={1: 0o000200},
                      mem={0o000200: 0o000300, 0o000300: 0o077777})              # MOV @(R1)+,R2
    assert cpu.state.R[2] == 0o077777 and cpu.state.R[1] == 0o000202
    cpu, b = one_step([0o010146], regs={1: 0o054321})                             # MOV R1,-(SP)
    assert cpu.state.R[Reg.SP] == 0o017774
    assert b.ram.read_word(0o017774) == 0o054321
    cpu, _ = one_step([0o015602], mem={0o017774: 0o000300, 0o000300: 0o055555})  # MOV @-(SP),R2
    assert cpu.state.R[2] == 0o055555 and cpu.state.R[Reg.SP] == 0o017774


def test_address_modes_pc_relative():
    cpu, _ = one_step([0o012700, 0o000042], pc=0o000100)          # MOV #0o42,R0 (mode 2 PC)
    assert cpu.state.R[0] == 0o000042 and cpu.state.R[Reg.PC] == 0o000104
    cpu, _ = one_step([0o013700, 0o000300], pc=0o000100,
                      mem={0o000300: 0o000400, 0o000400: 0o066666})             # MOV @#0o300,R0
    assert cpu.state.R[0] == 0o000400                                # @# reads mem[0o300]
    cpu, _ = one_step([0o016700, 0o000010], pc=0o000100,
                      mem={0o000114: 0o123123})                                  # MOV 0o10(PC),R0
    assert cpu.state.R[0] == 0o123123 and cpu.state.R[Reg.PC] == 0o000104
    cpu, _ = one_step([0o017700, 0o000010], pc=0o000100,
                      mem={0o000114: 0o000020, 0o000020: 0o066666})             # MOV @0o10(PC),R0
    assert cpu.state.R[0] == 0o066666
    cpu, _ = one_step([0o016700, 0o177776], pc=0o000100)          # MOV -2(PC),R0: signed disp
    assert cpu.state.R[0] == 0o177776                             # reads back the displacement word


# ---------------------------------------------------------------------------
# Jumps, subroutines, traps, capability gate (§5.4), bus errors (§5.5)
# ---------------------------------------------------------------------------

def test_golden_jmp_jsr_rts():
    cpu, _ = one_step([0o000137, 0o000400])                               # JMP @#0o400
    assert cpu.state.R[Reg.PC] == 0o000400                                # absolute target
    # JSR R5,@#0o400 ; at 0o400: RTS R5
    cpu, b = one_step([0o004537, 0o000400], regs={5: 0o011111},
                      mem={0o000400: 0o000205})
    assert cpu.state.R[Reg.PC] == 0o000400
    assert cpu.state.R[5] == 0o000104                                      # return address
    assert cpu.state.R[Reg.SP] == 0o017774
    assert b.ram.read_word(0o017774) == 0o011111                          # old R5 pushed
    cpu.step()                                                             # RTS R5 at 0o400
    assert cpu.state.R[Reg.PC] == 0o000104 and cpu.state.R[5] == 0o011111


def test_reserved_opcode_traps_to_000010():
    cpu, b = make_machine(program=[0o000007], pc=0o000100)                 # reserved opcode
    b.ram.write_word(0o000010, 0o002000)                                   # vector 000010
    b.ram.write_word(0o000012, 0)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o002000
    assert cpu.state.R[Reg.SP] == 0o017772                                 # two words pushed
    assert b.ram.read_word(0o017774) == 0o000102                           # saved PC (after fetch)
    assert b.ram.read_word(0o017772) == 0                                  # saved PSW


def test_bus_error_odd_word_traps_to_000004():
    # MOV #1,R1 ; MOV @R1,R2 with R1=1 -> odd-address word read -> Unibus timeout
    cpu, b = make_machine(program=[0o012701, 0o000001, 0o011102], pc=0o000100)
    b.ram.write_word(0o000004, 0o003000)                                   # vector 000004
    b.ram.write_word(0o000006, 0)
    cpu.step()                                                             # MOV #1,R1
    cpu.step()                                                             # MOV @R1,R2 -> bus error
    assert cpu.state.R[Reg.PC] == 0o003000
    assert b.ram.read_word(0o017774) == 0o000104  # faulting instruction addr


def test_nonexistent_memory_traps_to_000004():
    cpu, b = make_machine(ram_words=0o100, program=[0o013700, 0o010000], pc=0o000100, sp=0o000176)
    b.ram.write_word(0o000004, 0o003000)
    b.ram.write_word(0o000006, 0)
    cpu.step()                                                             # MOV @#0o10000,R0
    assert cpu.state.R[Reg.PC] == 0o003000


def test_1120_noops_microcoded_only_opcodes():
    """§5.4/§11.5: on the 11/20, MUL (with an immediate operand word) is
    consumed as a no-op — PC advances past the whole instruction, no trap,
    no register/flag change."""
    cpu, b = make_machine(program=[0o070027, 0o000005, 0o000240], pc=0o000100)  # MUL #5,R0
    b.ram.write_word(0o000010, 0o007000)  # reserved vector: must NOT fire
    cpu.state.R[0] = 0o000007
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o000104  # opcode + operand consumed
    assert cpu.state.R[0] == 0o000007                                      # nothing executed
    cpu.step()                                                             # NOP
    assert cpu.state.R[Reg.PC] == 0o000106
    # XOR (double-operand) also no-ops
    cpu, _ = make_machine(program=[0o074102, 0o000240], pc=0o000100)        # XOR R1,R2
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o000102 and cpu.state.R[2] == 0


def test_fis_reserved_on_kd11b_without_eis_cap():
    """Sec 19.10/5.4: on the 11/20 (KD11-B, no EIS cap) 0o075xxx is not in the
    KD11B no-op set -- it traps reserved through 000010, never silently runs.
    (On the 11/03 the KEF11 FIS executes -- pinned by test_fis.py.)"""
    cpu, b = make_machine(profile_key="11/20", program=[0o075005], pc=0o000100)
    b.ram.write_word(0o000010, 0o004000)                           # reserved vector
    b.ram.write_word(0o000012, 0)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o004000                         # trapped to handler


def test_tbit_trace_trap():
    # Set PSW T via the 177776 window (as console/MTPS would); the NEXT
    # instruction executes, then the trace trap fires through 000014 (§5.5).
    cpu, b = make_machine(program=[0o000240], pc=0o000100)                 # NOP
    b.ram.write_word(0o000014, 0o004000)                                   # vector 000014
    b.ram.write_word(0o000016, 0)
    b.write_word(0o177776, PSW.T)                                          # T := 1
    assert cpu.state.psw.get(PSW.T)
    cpu.step()                                                             # NOP, then T-bit trap
    assert cpu.state.R[Reg.PC] == 0o004000


class _FakeDev:
    """Minimal interrupt source for priority tests (§5.5)."""

    def __init__(self, vector: int, ipl: int) -> None:
        self._req: tuple[int, int] | None = (vector, ipl)

    def interrupt_pending(self) -> tuple[int, int] | None:
        return self._req

    def dma_request(self) -> bool:
        return False                       # BusDevice default (M2 arbitration)


def test_interrupt_priority_logic():
    cpu, b = make_machine(program=[0o000240], pc=0o000100)                 # NOP
    b.ram.write_word(0o000100, 0o005000)                                   # vector 0100
    b.ram.write_word(0o000102, 0o000340)                                   #   PC=0o5000, new IPL=7
    cpu.devices.append(_FakeDev(0o000100, 7))
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o005000                                 # IPL 7 > 0: taken
    assert cpu.state.psw.ipl == 7
    # masked when PSW IPL >= request level
    cpu2, _b2 = make_machine(program=[0o000240], pc=0o000100)
    cpu2.devices.append(_FakeDev(0o000100, 4))
    cpu2.state.psw.value = 0o000340                                        # IPL 7 masks level 4
    cpu2.step()
    assert cpu2.state.R[Reg.PC] == 0o000102                                # not taken


def test_psw_register_window_at_177776():
    cpu, b = make_machine(program=[0o012737, 0o000340, 0o177776], pc=0o000100)
    cpu.step()                                                             # MOV #0o340,@#0o177776
    assert cpu.state.psw.ipl == 7
    cpu.state.psw.value = 0
    b.write_byte(0o177776, 0o340)  # low byte holds IPL/T/cond
    assert cpu.state.psw.ipl == 7
    b.write_word(0o177776, 0o140000)  # mode bits: ignored on 11/20
    assert cpu.state.psw.value & 0o140000 == 0


def test_clock_events_drain_during_run():
    """§5.6: scheduled device events fire in (due, seq) order while the CPU
    runs; the CPU never polls — it drains due entries after each instruction."""
    cpu, board = make_machine(program=DECREMENT_LOOP, pc=0o000000)
    fired: list[str] = []
    board.clock.schedule(2, lambda: fired.append("a"))
    board.clock.schedule(2, lambda: fired.append("b"))                     # same cycle: FIFO
    step_n(cpu, 100)
    assert fired == ["a", "b"]
    assert board.clock.cycle >= 2


def test_ram_byte_word_little_endian():
    """§6.2/ram.py: low byte at even address, high byte at odd; word access
    round-trips both halves."""
    from pdp11.memory.ram import RAM
    ram = RAM(0o100)
    ram.write_word(0o000010, 0o125715)                                 # 0xAB CD
    assert ram.read_byte(0o000010) == 0o315                            # low byte, even
    assert ram.read_byte(0o000011) == 0o253                            # high byte, odd
    ram.write_byte(0o000011, 0o377)
    assert ram.read_word(0o000010) == 0o177715
    ram.write_byte(0o000010, 0o001)
    assert ram.read_word(0o000010) == 0o177401


def test_serialization_contract_roundtrip():
    """§15: every BusDevice implements serialize/deserialize (RAM here)."""
    from pdp11.memory.ram import RAM
    ram = RAM(0o10)
    ram.write_word(0, 0o123456)
    state: dict[str, Any] = ram.serialize()
    ram2 = RAM(0o10)
    ram2.deserialize(state)
    assert ram2.read_word(0) == 0o123456
    with pytest.raises(ValueError):
        RAM(0o20).deserialize(state)                                   # schema mismatch
