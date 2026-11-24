"""
Snapshot save/restore tests (spec §15, §11.8) — M8: in-memory and file
round-trips, mid-run snapshot continuation byte-identical, profile/RAM schema
mismatch rejection, unserializable-event loud failure, MMU-machine snapshots,
and the debugger `S <file>` command.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from pdp11.control.debugger import Debugger
from pdp11.control.snapshot import (
    FORMAT,
    VERSION,
    load_snapshot,
    restore_state,
    save_snapshot,
    snapshot_state,
)
from pdp11.cpu.registers import Reg
from pdp11.errors import EmulationError
from pdp11.graphics.terminal import ConsoleTerminal
from pdp11.tests.test_console import FakeBackend, make_board

# spin loop at 0o1000 (BR .) — device events (KW11-L ticks, DL11 pump) run anyway
SPIN = [0o000777]


def load_spin(board, pc: int = 0o1000) -> None:
    for i, w in enumerate(SPIN):
        board.ram.write_word(pc + 2 * i, w)
    board.cpu.state.R[Reg.PC] = pc
    board.cpu.state.R[Reg.SP] = 0o017776


def fingerprint(board) -> tuple:
    """Full machine fingerprint: cycle, CPU state, RAM hash, device states,
    pending event timing (due, seq) — everything §11.8 demands identical."""
    cpu = board.cpu
    st = cpu.state
    return (
        board.clock.cycle,
        list(st.R), st.psw.value, st.halted, st.waiting, list(st.sps),
        st.fp.serialize(),
        board.mmu.serialize() if board.mmu is not None else None,
        board.map.serialize() if board.map is not None else None,
        hashlib.sha256(bytes(board.ram.data)).hexdigest(),
        None if board.rom is None else hashlib.sha256(bytes(board.rom.data)).hexdigest(),
        board.rom_base,
        [(a, d.name, d.serialize()) for a, d in board.io_page.windows()],
        [(d, s) for d, s, _ in board.clock.pending_events()],
    )


def step_n(board, n: int) -> None:
    cpu = board.cpu
    for _ in range(n):
        if cpu.state.halted:
            break
        cpu.step()


def test_in_memory_roundtrip():
    board_a, _ = make_board("11/20")
    load_spin(board_a)
    step_n(board_a, 500)
    state = snapshot_state(board_a)
    board_b, _ = make_board("11/20")
    restore_state(board_b, state)
    assert fingerprint(board_a) == fingerprint(board_b)
    step_n(board_a, 500)
    step_n(board_b, 500)
    assert fingerprint(board_a) == fingerprint(board_b)


def test_file_roundtrip_and_wrapper(tmp_path):
    board_a, _ = make_board("11/03")          # ROM-boot machine: rom in the snapshot
    load_spin(board_a)
    step_n(board_a, 300)
    path = str(tmp_path / "snap.json")
    save_snapshot(board_a, path)
    with open(path, encoding="utf-8") as fh:
        wrapper = json.load(fh)
    assert wrapper["format"] == FORMAT and wrapper["version"] == VERSION
    assert wrapper["profile"] == "11/03" and "created" in wrapper and "core" in wrapper
    board_b, _ = make_board("11/03")
    restore_state(board_b, load_snapshot(path))
    assert fingerprint(board_a) == fingerprint(board_b)


def test_midrun_snapshot_continues_byte_identically(tmp_path):
    """§11.8: snapshot mid-run, restore into a fresh machine, continue BOTH —
    the subsequent outcome (bus traffic, device events, cycles) is identical."""
    board_a, _ = make_board("11/20")
    load_spin(board_a)
    step_n(board_a, 1000)
    path = str(tmp_path / "mid.json")
    save_snapshot(board_a, path)
    board_b, _ = make_board("11/20")
    restore_state(board_b, load_snapshot(path))
    board_c, _ = make_board("11/20")           # control: uninterrupted twin of A
    load_spin(board_c)
    step_n(board_c, 1000)
    assert fingerprint(board_a) == fingerprint(board_c)   # A and C were in sync
    step_n(board_a, 2000)
    step_n(board_b, 2000)
    step_n(board_c, 2000)
    assert fingerprint(board_a) == fingerprint(board_c)
    assert fingerprint(board_b) == fingerprint(board_c)   # restored B == uninterrupted C


def test_profile_mismatch_rejected(tmp_path):
    board_a, _ = make_board("11/20")
    path = str(tmp_path / "s.json")
    save_snapshot(board_a, path)
    board_70, _ = make_board("11/70")
    with pytest.raises(ValueError, match="profile"):
        restore_state(board_70, load_snapshot(path))


def test_ram_size_mismatch_rejected(tmp_path):
    from pdp11.main import assemble, build_arg_parser
    board_a, _ = make_board("11/20")
    path = str(tmp_path / "s.json")
    save_snapshot(board_a, path)
    args = build_arg_parser().parse_args(["--machine", "11/20", "--memory", "0o10000"])
    board_b = assemble(args, ConsoleTerminal(FakeBackend()))
    with pytest.raises(ValueError, match="RAM"):
        restore_state(board_b, load_snapshot(path))


def test_corrupt_and_foreign_files_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "other", "version": 1}')
    with pytest.raises(ValueError, match="not an"):
        load_snapshot(str(bad))
    good = tmp_path / "good.json"
    board_a, _ = make_board("11/20")
    save_snapshot(board_a, str(good))
    good.write_text(good.read_text()[:-5])     # truncate the core
    with pytest.raises(ValueError):
        load_snapshot(str(good))
    with pytest.raises(ValueError, match="cannot read"):
        load_snapshot(str(tmp_path / "missing.json"))


def test_unserializable_event_fails_loudly():
    """§18.3 layer 2: a raw lambda in the event queue must raise EmulationError
    at save time — never a silently wrong snapshot."""
    board, _ = make_board("11/20")
    board.clock.schedule(10, lambda: None)
    with pytest.raises(EmulationError, match="unserializable"):
        snapshot_state(board)


def test_1170_mmu_map_snapshot():
    board_a, _ = make_board("11/70")
    step_n(board_a, 200)
    state = snapshot_state(board_a)
    board_b, _ = make_board("11/70")
    restore_state(board_b, state)
    assert fingerprint(board_a) == fingerprint(board_b)
    assert state["mmu"] is not None and state["map"] is not None


def test_debugger_S_command_saves(tmp_path):
    board, backend = make_board("11/20")
    path = str(tmp_path / "bp.json")
    dbg = Debugger(board, ConsoleTerminal(backend))
    backend.input = bytearray(f"S {path}\nQ\n".encode())   # scripted host input
    assert dbg.run() == "quit"
    state = load_snapshot(path)
    assert state["profile"] == "11/20"
    assert f"snapshot saved to {path}" in backend.text()
