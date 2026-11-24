"""
Session checkpointing (spec §15) — M8.

Serializes the FULL machine state and restores it exactly:

- CPU: R0-R7, PSW, halted/waiting, per-mode SP banks, FP11 state (fps/fec/fea/ac)
- MMU (11/70, 11/94): MMR0-3 + all six APR sets; Unibus map / Q22-CD map entries
- RAM and boot ROM contents (zlib-compressed, base64 — the binary core image)
- every I/O-page window (devices + PSW/MMU/map windows) via the BusDevice
  serialize()/deserialize() contract (§15 design obligation)
- the deterministic scheduler: cycle counter + event queue (§5.6). Queue
  callbacks are bound methods of the CPU or of an attached window device;
  each is stored as {"obj": "cpu"|address, "name": method} and rebuilt on
  restore. An unserializable callback (e.g. a raw lambda) raises
  EmulationError at save time — never a silently wrong snapshot (§18.3).

Format: a JSON wrapper (format/version/profile/timestamp header, §15) around
a binary core image: the state dict is JSON-encoded, zlib-compressed and
base64'd into the "core" field — still introspectable in a hex editor.
The core is deterministic: identical machine state -> identical core bytes
(the timestamp lives only in the outer header).

Restore is exact and resumable (§11.8): loading a snapshot and continuing
produces the same subsequent bus traffic as an uninterrupted run, given
identical operator input. CLI: --save/--restore (main.py) plus the debugger
`S <file>` command (debugger.py, snapshot at a breakpoint/halt).

User-facing misuse (unreadable file, wrong format/version, profile mismatch)
raises plain ValueError with an actionable message (§18.3 layer 3);
internal invariant breaks raise EmulationError (§18.3 layer 2).
"""

from __future__ import annotations

import base64
import binascii
import inspect
import json
import time
import zlib
from array import array
from collections.abc import Callable
from typing import Any

from pdp11.board import SystemBoard
from pdp11.errors import EmulationError

FORMAT = "eleven13-snapshot"
VERSION = 1


def _pack_words(data: Any) -> str:
    """array('H') -> base64(zlib(raw little-endian bytes)) — the binary core."""
    return base64.b64encode(zlib.compress(bytes(data), 9)).decode("ascii")


def _unpack_words(packed: str, words: int) -> Any:
    """Inverse of _pack_words; length-checked (schema error -> ValueError)."""
    raw = zlib.decompress(base64.b64decode(packed))
    if len(raw) != words * 2:
        raise ValueError("snapshot core has wrong RAM/ROM byte count for this machine")
    return array("H", raw)


def _event_ref(fn: Callable[[], None], board: SystemBoard) -> dict[str, Any]:
    """Serialize one queued callback as {"obj": "cpu" | octal address, "name":
    method name}. Bound methods of the CPU or of an I/O-page window device
    only — anything else is an emulator bug, raised loudly (§18.3)."""
    if not inspect.ismethod(fn):
        raise EmulationError(
            f"unserializable scheduled event {fn!r} — queue callbacks must be bound "
            "methods of the CPU or an attached device (§15/§18.3)")
    owner = fn.__self__
    if owner is board.cpu:
        return {"obj": "cpu", "name": fn.__func__.__name__}
    for addr, dev in board.io_page.windows():
        if dev is owner:
            return {"obj": addr, "name": fn.__func__.__name__}
    raise EmulationError(f"scheduled event owner {owner!r} is not on this board (§18.3)")


def _event_fn(ref: dict[str, Any], board: SystemBoard) -> Callable[[], None]:
    """Rebuild a queued callback from its serialized reference."""
    if ref["obj"] == "cpu":
        owner: Any = board.cpu
    else:
        matches = [dev for addr, dev in board.io_page.windows()
                   if addr == ref["obj"] and dev.name == ref.get("dev_name")]
        if len(matches) != 1:
            raise EmulationError(f"cannot resolve snapshot event ref {ref!r} (§18.3)")
        owner = matches[0]
    fn = getattr(owner, ref["name"])
    if not callable(fn):
        raise EmulationError(f"snapshot event {ref!r} does not resolve to a callable (§18.3)")
    return fn


def snapshot_state(board: SystemBoard) -> dict[str, Any]:
    """The full machine state as a JSON-safe dict (the binary core, pre-pack)."""
    st = board.cpu.state
    clock = board.clock
    windows = board.io_page.windows()
    devices = []
    for addr, dev in windows:
        devices.append({"address": addr, "name": dev.name, "state": dev.serialize()})
    events = []
    for due, seq, fn in clock.pending_events():
        ref = _event_ref(fn, board)
        if ref["obj"] != "cpu":
            for addr, dev in windows:
                if addr == ref["obj"]:
                    ref["dev_name"] = dev.name
        events.append({"due": due, "seq": seq, "ref": ref})
    return {
        "profile": board.profile.key,
        "cpu": {
            "R": list(st.R), "psw": st.psw.value, "halted": st.halted,
            "waiting": st.waiting, "banked": st.banked, "sps": list(st.sps),
            "fp": st.fp.serialize(),
        },
        "mmu": board.mmu.serialize() if board.mmu is not None else None,
        "map": board.map.serialize() if board.map is not None else None,
        "ram": {"words": len(board.ram.data), "data": _pack_words(board.ram.data)},
        "rom": ({"base": board.rom_base, "words": len(board.rom.data),
                 "data": _pack_words(board.rom.data)} if board.rom is not None else None),
        "devices": devices,
        "clock": {"cycle": clock.cycle, "seq": clock.seq, "events": events},
        "cycles_used": board.cycles_used,
    }


def restore_state(board: SystemBoard, state: dict[str, Any]) -> None:
    """Restore a snapshot_state() dict into `board`. The profile must match
    (ValueError, user-facing §18.3); unknown/missing keys are schema errors."""
    if not isinstance(state, dict) or "profile" not in state:
        raise ValueError("not an eleven13 snapshot state (missing 'profile')")
    if state["profile"] != board.profile.key:
        raise ValueError(
            f"snapshot is for profile {state['profile']!r}, this machine is "
            f"{board.profile.key!r} — refusing to restore (§15)")
    cpu = state["cpu"]
    for key in ("R", "psw", "halted", "waiting", "banked", "sps", "fp"):
        if key not in cpu:
            raise ValueError(f"snapshot cpu state missing {key!r}")
    ram = state["ram"]
    if ram.get("words") != len(board.ram.data):
        raise ValueError("snapshot RAM word count does not match this machine (§15)")
    board.ram.data = _unpack_words(ram["data"], len(board.ram.data))
    if state["rom"] is not None:
        if board.rom is None or state["rom"].get("words") != len(board.rom.data):
            raise ValueError("snapshot ROM does not match this machine (§15)")
        board.rom.data = _unpack_words(state["rom"]["data"], len(board.rom.data))
        board.rom_base = state["rom"]["base"]
    if board.mmu is not None:
        if state["mmu"] is None:
            raise ValueError("snapshot has no MMU state for an MMU machine (§15)")
        board.mmu.deserialize(state["mmu"])
    if board.map is not None:
        if state["map"] is None:
            raise ValueError("snapshot has no map state for a mapped machine (§15)")
        board.map.deserialize(state["map"])
    windows = {(addr, dev.name): dev for addr, dev in board.io_page.windows()}
    for entry in state["devices"]:
        key = (entry["address"], entry["name"])
        if key not in windows:
            raise ValueError(f"snapshot device {entry['name']!r} at "
                             f"{entry['address']:06o} is not attached on this machine (§15)")
        windows[key].deserialize(entry["state"])
    st = board.cpu.state
    st.R = array("H", cpu["R"])
    st.psw.value = cpu["psw"]
    st.halted = cpu["halted"]
    st.waiting = cpu["waiting"]
    st.banked = cpu["banked"]
    st.sps = array("H", cpu["sps"])
    st.fp.deserialize(cpu["fp"])
    board.cycles_used = state.get("cycles_used", 0)
    clock_state = state["clock"]
    for key in ("cycle", "seq", "events"):
        if key not in clock_state:
            raise ValueError(f"snapshot clock state missing {key!r} (§15)")
    entries = [(e["due"], e["seq"], _event_fn(e["ref"], board)) for e in clock_state["events"]]
    board.clock.load_queue(clock_state["cycle"], clock_state["seq"], entries)


def save_snapshot(board: SystemBoard, path: str) -> None:
    """Write the JSON-wrapped binary-core snapshot to `path` (§15 format)."""
    core = base64.b64encode(
        zlib.compress(json.dumps(snapshot_state(board), sort_keys=True,
                                 separators=(",", ":")).encode("utf-8"), 9)
    ).decode("ascii")
    wrapper = {
        "format": FORMAT,
        "version": VERSION,
        "profile": board.profile.key,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),  # header only (§15)
        "core": core,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(wrapper, fh, indent=2)
        fh.write("\n")


def load_snapshot(path: str) -> dict[str, Any]:
    """Read + validate a snapshot file; returns the inner state dict.
    Bad format/version/corrupt core -> ValueError with an actionable message."""
    try:
        with open(path, encoding="utf-8") as fh:
            wrapper = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read snapshot {path}: {exc}") from exc
    if not isinstance(wrapper, dict) or wrapper.get("format") != FORMAT:
        raise ValueError(f"{path}: not an {FORMAT} file (bad or missing 'format')")
    if wrapper.get("version") != VERSION:
        raise ValueError(f"{path}: snapshot version {wrapper.get('version')} != {VERSION}")
    try:
        raw = zlib.decompress(base64.b64decode(wrapper["core"]))
        return json.loads(raw.decode("utf-8"))
    except (KeyError, binascii.Error, zlib.error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: corrupt snapshot core image: {exc}") from exc
