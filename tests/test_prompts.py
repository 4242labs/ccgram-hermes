from pathlib import Path
from types import SimpleNamespace

import pytest
from ccgram.handlers.interactive import interactive_ui as iui
from ccgram.window_state_ports import identity_state

from ccgram_hermes import hermes, patches
from ccgram_hermes.provider import HermesProvider, parse_prompt

from .conftest import SENT

FIX = Path(__file__).parent / "fixtures"
ALL = ["Allow once", "Allow this session", "Always allow", "Deny"]


def box(labels, cmd=("chmod 666 ./junk/keep.txt",), more=0):
    rows = ["⚠ approval required · world/other-writable permissions", *(f" {c}" for c in cmd)]
    if more:
        rows.append(f" … +{more} more lines (full text above)")
    rows += [("▸ " if i == 0 else "  ") + f"{i + 1}. {label}" for i, label in enumerate(labels)]
    rows.append(f"↑/↓ select · Enter confirm · 1-{len(labels)} quick pick · Esc/Ctrl+C deny")
    w = 90
    return "\n".join(["some output", " ╔" + "═" * w + "╗", *(f" ║ {r:<{w - 1}}║" for r in rows),
                      " ╚" + "═" * w + "╝", "", "status line"])


def test_working_status():
    prov = HermesProvider()
    assert prov.parse_terminal_status((FIX / "ready.txt").read_text()) is None
    assert prov.parse_terminal_status("⠟ Terminal(\"ls\") (3s)\n(◔_◔) reflecting…") is None
    assert not SENT
    caps = prov.capabilities
    assert not caps.uses_pyte_status_parsing and not caps.uses_pane_title


@pytest.mark.parametrize("labels", [ALL, ["Allow once", "Allow this session", "Deny"], ["Allow once", "Deny"]])
def test_prompt_variants(labels):
    for pane in (box(labels), box(labels, cmd=[f"line {i}" for i in range(10)], more=4)):
        st = parse_prompt(pane)
        assert st and st.is_interactive and st.ui_type == "PermissionPrompt"
        assert "║" not in st.raw_text and "▸" not in st.raw_text and "❯ 1. Allow once" in st.raw_text
        choices = iui.parse_direct_choices(st.raw_text)
        assert [label for _, label in choices] == [f"{i + 1}. {x}" for i, x in enumerate(labels)]
        assert [key for key, _ in choices] == [str(i + 1) for i in range(len(labels))]
    live = parse_prompt((FIX / "prompt.txt").read_text())
    assert [label.split(". ", 1)[1] for _, label in iui.parse_direct_choices(live.raw_text)] == ALL
    assert not SENT

    assert parse_prompt(box(["Allow once", "Maybe"])) is None
    assert parse_prompt(box(labels).replace("quick pick", "pick")) is None
    assert len(SENT) == 1 and "did not parse" in SENT[0]


async def test_repeat_prompt(db, monkeypatch):
    db.session()
    db.add("user", "go")
    db.add("assistant", None, calls=[("c1", "terminal", {"command": "chmod 666 x"})])
    pane = box(ALL)
    window = SimpleNamespace(window_id="@1")

    async def find(_wid):
        return window

    async def capture(_wid):
        return pane

    monkeypatch.setattr(iui, "tmux_manager", SimpleNamespace(find_window_by_id=find, capture_pane=capture))
    monkeypatch.setattr(iui, "get_window_provider", lambda _w: "hermes")
    monkeypatch.setattr(identity_state, "get_session_id", lambda _w: "s1")

    first = await iui._capture_interactive_content("@1")
    assert first and first[1].endswith("\n\n#c1")
    key = (42, 7, 1)
    seq = iui._next_interactive_sequence(key, first[1])
    assert iui._next_interactive_sequence(key, first[1]) == seq

    # answered locally or timed out: no open call, no prompt
    db.add("tool", "done", call_id="c1", tool="terminal")
    assert await iui._capture_interactive_content("@1") is None

    # the same text again, for a new call, is a new prompt
    db.add("assistant", None, calls=[("c2", "terminal", {"command": "chmod 666 x"})])
    again = await iui._capture_interactive_content("@1")
    assert again[1].endswith("#c2")
    assert iui._next_interactive_sequence(key, again[1]) == seq + 1

    assert patches.tag_prompt(("x", "y"), None) is None
    assert hermes.oldest_open_call("s1") == "c2"
