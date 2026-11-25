"""Headless tests for the optional tkinter GUI front end (pdp11/gui.py).

The sandbox/CI has no display and often no libtk, so everything here avoids
instantiating tk.Tk(): the instructions text and the TkGuiBackend queue logic
are pure Python and fully testable; run_gui() itself is only exercised via
its missing-tkinter error path when tkinter is unavailable.
"""

from __future__ import annotations

import pytest

from pdp11.config.profiles import PROFILES
from pdp11.gui import ODT_HELP, TkGuiBackend, gui_available, instructions_for
from pdp11.main import build_arg_parser, main


def _args(*extra: str):
    return build_arg_parser().parse_args(["--machine", "11/20", *extra])


def test_gui_flag_parses_and_defaults_off() -> None:
    assert _args().gui is False
    assert _args("--gui").gui is True


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
