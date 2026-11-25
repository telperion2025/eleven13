"""Headless tests for the optional tkinter GUI front end (pdp11/gui.py).

The sandbox/CI has no display and often no libtk, so everything here avoids
instantiating tk.Tk(): the instructions text and the TkGuiBackend queue logic
are pure Python and fully testable; run_gui() itself is only exercised via
its missing-tkinter error path when tkinter is unavailable.
"""

from __future__ import annotations

import pytest

from pdp11.config.profiles import PROFILES
from pdp11.gui import (
    ODT_HELP,
    TkGuiBackend,
    add_recent,
    gui_available,
    instructions_for,
    load_recent,
    setup_to_argv,
)
from pdp11.main import build_arg_parser, main


def _args(*extra: str):
    return build_arg_parser().parse_args(["--machine", "11/20", *extra])


def test_gui_flag_parses_and_defaults_off() -> None:
    assert _args().gui is False
    assert _args("--gui").gui is True


def test_gui_alone_needs_no_machine_flag() -> None:
    args = build_arg_parser().parse_args(["--gui"])
    assert args.gui is True and args.machine is None
    # but without --gui the machine is still mandatory (spec §4.1: no default)
    with pytest.raises(SystemExit):
        main([])


def test_setup_to_argv_maps_every_form_field() -> None:
    argv = setup_to_argv(
        machine="11/03", memory="0o40000",
        disks=["a.img", "b.img"], rls=["rl.img"], tapes=["t.tap"], paper_tapes=["p.pt"],
        bootrom="rom.bin", throttle=True, debug=True, vt11=True,
        save="s.json", restore="r.json",
        powerfail=(True, "0o100000"), powerrestore=(False, ""))
    args = build_arg_parser().parse_args(argv)
    assert args.machine == "11/03" and args.gui is True
    assert args.memory == 0o40000
    assert args.disk == ["a.img", "b.img"] and args.rl == ["rl.img"]
    assert args.tape == ["t.tap"] and args.papertape == ["p.pt"]
    assert args.bootrom == "rom.bin" and args.throttle and args.debug and args.vt11
    assert args.save == "s.json" and args.restore == "r.json"
    assert args.powerfail == "0o100000" and args.powerrestore is None


def test_setup_to_argv_omits_unset_fields() -> None:
    argv = setup_to_argv(machine="11/20", powerfail=(True, ""), powerrestore=(False, "0o200000"))
    args = build_arg_parser().parse_args(argv)
    assert args.disk is None and args.memory is None and args.save is None
    assert args.throttle is False and args.debug is False and args.vt11 is False
    assert args.powerfail == "0o100000"      # flag without value -> const default
    assert args.powerrestore is None


def test_module_imports_headless() -> None:
    import pdp11.gui as gui  # noqa: F401 — import must not require tkinter/display
    assert isinstance(gui_available(), bool)


def test_gui_backend_queue_roundtrip() -> None:
    be = TkGuiBackend()
    assert be.getch() is None                       # non-blocking, nothing pending
    be.push_input(b"1000/\r")
    got = [be.getch() for _ in range(6)]
    assert bytes(got) == b"1000/\r"
    assert be.getch() is None
    be.putch(ord("A"))
    be.putch(0o202)                                 # masked to 7 bits (§8)
    assert be.drain_output() == [ord("A"), 0o002]
    assert be.drain_output() == []                  # drained
    assert be.at_eof() is False
    be.push_input(b"Q\r")
    be.close()
    assert be.at_eof() is False                     # closed but input queue not drained
    while be.getch() is not None:
        pass
    assert be.at_eof() is True                       # closed + queue empty


def test_gui_without_tkinter_fails_with_actionable_message() -> None:
    if gui_available():
        pytest.skip("tkinter importable here; the no-libtk path cannot trigger")
    with pytest.raises(SystemExit, match="tkinter"):
        main(["--machine", "11/20", "--gui"])


@pytest.mark.parametrize("key", sorted(PROFILES))
def test_instructions_cover_every_profile(key: str) -> None:
    args = _args()
    args.machine = key
    text = instructions_for(PROFILES[key], args)
    assert f"PDP-{key}" in text
    assert "QUICK START" in text
    assert "ODT" in text and "@" in text
    for cmd in ("<addr>/", "G [addr]", "Q"):
        assert cmd in text
    assert "BREAK" in text and "Quit" in text
    assert "docs/user-manual.md" in text and "DESIGN.md" in text
    # per-machine power-on PC appears in the boot section
    expected_pc = "000000" if key == "11/20" else "173000"
    assert expected_pc in text
    # capability line reflects the profile
    prof = PROFILES[key]
    assert f"MMU: {'yes' if prof.has_mmu else 'no'}" in text
    assert f"FIS: {'yes' if prof.has_fis else 'no'}" in text


def test_instructions_list_attached_media_and_flags() -> None:
    args = _args("--disk", "images/foo.img", "--tape", "bk.tap", "--debug",
                 "--save", "s.json", "--vt11")
    text = instructions_for(PROFILES["11/20"], args)
    assert "--disk images/foo.img" in text
    assert "--tape bk.tap" in text
    assert "dbg>" in text                            # debugger section on --debug
    assert "--save s.json" in text
    assert "--vt11" in text


def test_odt_help_matches_front_panel_commands() -> None:
    # the cheat sheet must stay in sync with pdp11/control/front_panel.py
    for token in ("<addr>/", "<addr>\\", "G [addr]", "Q"):
        assert token in ODT_HELP


def test_recent_images_roundtrip_dedup_and_order(tmp_path) -> None:
    f = str(tmp_path / "recent.json")
    assert load_recent(f) == []                       # missing file -> empty
    assert add_recent("/a.img", f) == ["/a.img"]
    add_recent("/b.tap", f)
    add_recent("/a.img", f)                           # re-attach moves to top
    assert load_recent(f) == ["/a.img", "/b.tap"]


def test_recent_images_capped_and_corrupt_file_safe(tmp_path) -> None:
    f = str(tmp_path / "recent.json")
    for i in range(20):
        add_recent(f"/img{i}", f, limit=5)
    assert load_recent(f) == [f"/img{i}" for i in range(19, 14, -1)]
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_recent(str(bad)) == []
    bad.write_text('{"not": "a list"}', encoding="utf-8")
    assert load_recent(str(bad)) == []
