"""
M5: the 11/70 MMU (spec §5.7) — flat vs enabled translation, the I/D space
split via MMR3, page-fault aborts (NR/RO/PL) and their vectors (000250 with
MMR0 TENB, else 000004), the APR/MMR I/O-page windows, per-mode SP banking,
and the MMU instructions (SPL/MTPS/MFPS/MARK/MFPI) on the 11/70 versus their
no-op/reserved behavior on the 11/20 (§5.4 capability gate).
"""

from __future__ import annotations

import pytest

from pdp11.cpu.mmu import (
    MD_KER,
    MD_SUP,
    MD_USR,
    MMR0_MME,
    MMR0_NR,
    MMR0_PL,
    MMR0_RO,
    MMR0_TENB,
    MMR3_KDS,
    MMR3_SDS,
    MMR3_UDS,
    MMU,
    PDR_A,
    PDR_ED,
    PDR_W,
    SPACE_D,
    SPACE_I,
    MMUAbort,
)
from pdp11.cpu.registers import PSW, Reg
from pdp11.tests.cpu_golden import make_machine, one_step, step_n

RW_PAGE = 0o077406          # PDR: ACF 6 (read/write), ED=0, PLF 0o77 (full page)


def _enable_kernel_flat(board) -> None:
    """Map kernel I/D page 0 identity and page 7 to the I/O page. The MMU is
    still off, so these writes go through the flat 0o160000-0o200000 alias."""
    board.write_word(0o172340, 0)          # KIPAR0
    board.write_word(0o172300, RW_PAGE)    # KIPDR0
    board.write_word(0o172360, 0)          # KDPAR0
    board.write_word(0o172320, RW_PAGE)    # KDPDR0
    board.write_word(0o172356, 0o177600)   # KIPAR7 -> I/O page
    board.write_word(0o172316, RW_PAGE)    # KIPDR7
    board.write_word(0o172376, 0o177600)   # KDPAR7
    board.write_word(0o172336, RW_PAGE)    # KDPDR7


# -- translation unit tests ----------------------------------------------------

def test_mmu_off_is_flat():
    _cpu, board = make_machine("11/70", ram_words=0o200000)
    assert board.mmu is not None and not board.mmu.enabled
    board.ram.write_word(0o100000, 0o5150)
    assert board.read_word(0o100000) == 0o5150
    assert board.mmu.translate(0o100000, MD_KER) == 0o100000


def test_translate_expand_up_and_access_bits():
    mmu = MMU()
    mmu.mmr0 = MMR0_MME
    mmu.par[SPACE_I][MD_KER][0] = 0o2000
    mmu.pdr[SPACE_I][MD_KER][0] = RW_PAGE
    assert mmu.translate(0o000123, MD_KER, is_instruction=True) == (0o2000 << 6) | 0o123
    assert mmu.pdr[SPACE_I][MD_KER][0] & PDR_A              # accessed bit set
    mmu.translate(0o000000, MD_KER, is_instruction=True, is_write=True)
    assert mmu.pdr[SPACE_I][MD_KER][0] & PDR_W              # written bit set


def test_translate_expand_down_page_length():
    mmu = MMU()
    mmu.mmr0 = MMR0_MME
    mmu.par[SPACE_I][MD_KER][7] = 0o177600
    mmu.pdr[SPACE_I][MD_KER][7] = 0o000006 | PDR_ED          # stack page, PLF 0
    # expand-down limit = 0o20000 - ((0+1)<<8) = 0o17400 (page 7 = VA 160000+)
    assert mmu.translate(0o177777, MD_KER, is_instruction=True) == (0o177600 << 6) | 0o17777
    with pytest.raises(MMUAbort) as exc:
        mmu.translate(0o177377, MD_KER, is_instruction=True)   # off 0o17377 < 0o17400
    assert exc.value.reason == MMR0_PL
    assert mmu.mmr2 == 0o177377


def test_abort_no_access_and_read_only():
    mmu = MMU()
    mmu.mmr0 = MMR0_MME | MMR0_TENB
    mmu.pdr[SPACE_I][MD_KER][2] = 0                          # ACF 0: no access
    with pytest.raises(MMUAbort) as exc:
        mmu.translate(0o040000, MD_KER, is_instruction=True)
    assert exc.value.reason == MMR0_NR
    assert exc.value.vector == 0o250                         # TENB -> MMU trap vector
    assert mmu.mmr2 == 0o040000 and mmu.mmr0 & MMR0_NR
    mmu.pdr[SPACE_I][MD_KER][2] = 1                          # ACF 1: read-only
    assert mmu.translate(0o040000, MD_KER, is_instruction=True) == 0  # PAR 0 -> PA off
    with pytest.raises(MMUAbort) as exc2:
        mmu.translate(0o040000, MD_KER, is_instruction=True, is_write=True)
    assert exc2.value.reason == MMR0_RO
    mmu.mmr0 &= ~MMR0_TENB
    with pytest.raises(MMUAbort) as exc3:                    # TENB clear -> bus error
        mmu.translate(0o040000, MD_KER, is_instruction=True, is_write=True)
    assert exc3.value.vector == 0o000004


def test_id_space_split_mmr3():
    mmu = MMU()
    assert not mmu.dspace_enabled(MD_KER)
    mmu.mmr3 = MMR3_UDS | MMR3_KDS
    assert mmu.dspace_enabled(MD_USR) and mmu.dspace_enabled(MD_KER)
    assert not mmu.dspace_enabled(MD_SUP)
    assert mmu.space_for(MD_KER, is_instruction=False) == SPACE_D
    assert mmu.space_for(MD_KER, is_instruction=True) == SPACE_I   # fetches always I
    mmu.mmr3 = 0
    assert mmu.space_for(MD_KER, is_instruction=False) == SPACE_I


# -- register windows on the board ----------------------------------------------

def test_apr_and_mmr_windows():
    _cpu, board = make_machine("11/70")
    board.write_word(0o172340, 0o123456)                     # KIPAR0
    assert board.mmu.par[SPACE_I][MD_KER][0] == 0o123456
    assert board.read_word(0o172340) == 0o123456
    board.write_word(0o172320, RW_PAGE)                      # KDPDR0
    assert board.mmu.pdr[SPACE_D][MD_KER][0] == RW_PAGE
    board.write_word(0o172516, MMR3_SDS)                     # MMR3
    assert board.mmu.mmr3 == MMR3_SDS
    assert board.read_word(0o177572) == 0
    board.write_word(0o177572, MMR0_MME | MMR0_TENB)         # MMR0 (enables MMU)
    assert board.mmu.enabled and board.mmu.mmr0 == MMR0_MME | MMR0_TENB


# -- CPU-level page faults --------------------------------------------------------

def test_cpu_page_fault_traps_to_250_with_tenb():
    cpu, board = make_machine("11/70", program=[0o016001, 0o000000], pc=0o100)
    cpu.state.R[0] = 0o040000                                # MOV @R0,R1 ; HALT; page 2 unmapped
    _enable_kernel_flat(board)
    board.write_word(0o172516, MMR3_UDS | MMR3_KDS)
    board.write_word(0o177572, MMR0_MME | MMR0_TENB)
    board.ram.write_word(0o250, 0o1000)                      # vector 250 -> handler
    board.ram.write_word(0o252, 0o000000)
    board.ram.write_word(0o1000, 0o000000)                   # handler: HALT
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o1000                     # trap through 250 taken
    assert board.mmu.mmr2 == 0o040000                        # faulting VA recorded
    assert board.mmu.mmr0 & MMR0_NR
    sp = cpu.state.R[Reg.SP]
    assert board.ram.read_word(sp + 2) == 0o000100           # pushed old PC
    cpu.step()
    assert cpu.state.halted and cpu.state.R[Reg.PC] == 0o1002


def test_cpu_page_fault_vector_004_without_tenb():
    cpu, board = make_machine("11/70", program=[0o016001], pc=0o100)
    cpu.state.R[0] = 0o040000
    _enable_kernel_flat(board)
    board.write_word(0o172516, MMR3_KDS)
    board.write_word(0o177572, MMR0_MME)                     # no TENB
    board.ram.write_word(0o004, 0o2000)                      # bus error vector
    board.ram.write_word(0o006, 0o000000)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o2000
    assert board.mmu.mmr2 == 0o040000


# -- modes, banking, and the MMU instructions ------------------------------------

def test_per_mode_sp_banking():
    cpu, _board = make_machine("11/70")
    cpu.state.R[Reg.SP] = 0o1000
    cpu.state.sps[3] = 0o2000
    cpu.state.switch_mode(3, 0)
    assert cpu.state.R[Reg.SP] == 0o2000                     # user SP bank loaded
    cpu.state.switch_mode(0, 3)
    assert cpu.state.R[Reg.SP] == 0o1000                     # kernel SP restored
    cpu20, _b20 = make_machine("11/20")
    cpu20.state.R[Reg.SP] = 0o1000
    cpu20.state.switch_mode(3, 0)                            # no banking on 11/20
    assert cpu20.state.R[Reg.SP] == 0o1000


def test_psw_mode_bits_11_70_vs_11_20():
    cpu, board = make_machine("11/70")
    board.write_word(0o177776, 0o140000)                     # PSW <- current mode 3
    assert (cpu.state.psw.value & PSW.Curr_MODE) == 0o140000
    cpu20, board20 = make_machine("11/20")
    board20.write_word(0o177776, 0o140000)
    assert (cpu20.state.psw.value & PSW.Curr_MODE) == 0      # dropped without MMU


def test_spl_sets_ipl_1170_noop_1120():
    cpu, _ = one_step([0o000237], profile_key="11/70")       # SPL 7
    assert (cpu.state.psw.value & PSW.IPL_MASK) == 0o340
    cpu20, _ = one_step([0o000237], profile_key="11/20")     # KD11-B no-op
    assert (cpu20.state.psw.value & PSW.IPL_MASK) == 0


def test_mtps_mfps_byte():
    cpu, _ = make_machine("11/70", program=[0o106400, 0o106701], pc=0o100)  # MTPS R0 ; MFPS R1
    cpu.state.R[0] = 0o000252
    step_n(cpu, 2)
    assert cpu.state.psw.value & 0o377 == 0o252
    assert cpu.state.R[1] == 0o252


def test_mark_frame():
    cpu, board = make_machine("11/70", program=[0o006402], pc=0o100, sp=0o1000)  # MARK 2
    cpu.state.R[5] = 0o2000
    board.ram.write_word(0o106, 0o777)
    cpu.step()
    assert cpu.state.R[Reg.PC] == 0o2000                     # PC <- R5
    assert cpu.state.R[5] == 0o777                           # R5 <- (SP)+
    assert cpu.state.R[Reg.SP] == 0o110                      # SP = PC + 2*nn, popped


def test_mfpi_reads_previous_mode_space():
    cpu, board = make_machine("11/70", ram_words=0o200000,
                              program=[0o006537, 0o2000, 0o000000], pc=0o100)
    board.write_word(0o172340, 0)                            # KIPAR0 identity
    board.write_word(0o172300, RW_PAGE)                      # KIPDR0
    board.write_word(0o177640, 0o2000)                       # UIPAR0 -> PA 0o200000
    board.write_word(0o177600, RW_PAGE)                      # UIPDR0
    board.write_word(0o177572, MMR0_MME)
    board.ram.write_word(0o202000, 0o777)                    # user I-space VA 0o2000
    cpu.state.psw.value = 0o030000                           # previous mode = user
    cpu.step()                                               # MFPI @#0o2000
    assert board.ram.read_word(0o2000) == 0o777              # landed in current (kernel) space
    assert board.ram.read_word(0o202000) == 0o777            # previous-space source untouched


def test_double_fault_halts_cpu():
    """A page fault while fetching the abort vector (kernel D page 0
    unmapped) is a double fault: the real 11/70 stops the CPU (§22.4) —
    the abort must not escape step() as an uncaught exception."""
    cpu, board = make_machine("11/70", program=[0o016001], pc=0o100)  # MOV @R0,R1
    cpu.state.R[0] = 0o040000                                # page 2 unmapped
    board.write_word(0o172340, 0)                            # KIPAR0 identity (I space)
    board.write_word(0o172300, RW_PAGE)                      # KIPDR0
    # KDPAR0/KDPDR0 left 0: the vector fetch in kernel D space faults too
    board.write_word(0o172516, MMR3_KDS)
    board.write_word(0o177572, MMR0_MME | MMR0_TENB)
    cpu.step()
    assert cpu.state.halted
