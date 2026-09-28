import json

import ccgram
import ccgram.main as ccmain
import pytest
from ccgram.handlers import registry as hr
from ccgram.handlers.interactive import interactive_ui as iui
from telegram.ext import ExtBot

from ccgram_hermes import cli, hermes, patches
from ccgram_hermes.provider import parse_prompt

from .conftest import PRE_APPLY_FAILS, SENT


async def _fake_post(*_a, **_kw):
    pass


BREAK = {
    "ccgram version": lambda mp: mp.setattr(ccgram, "__version__", "9.9.9"),
    "4 upgrade command": lambda mp: mp.setattr(hr, "upgrade_command", None),
    "5 create_bot": lambda mp: mp.setattr(ccmain, "create_bot", object(), raising=False),
    "6 prompt capture": lambda mp: mp.setattr(iui, "_capture_interactive_content", lambda *a: None),
    "7 Bot._post": lambda mp: mp.setattr(ExtBot, "_post", _fake_post, raising=False),
}


def test_guards_pass_on_pinned_ccgram():
    assert PRE_APPLY_FAILS == []
    # applied patches 1–3 are what their own guards look for
    assert set(patches.check()) == {"1 provider registry", "2 provider detection", "3 agent picker"}


@pytest.mark.parametrize("name", BREAK)
def test_guard_detects(name, monkeypatch):
    BREAK[name](monkeypatch)
    assert name in patches.check()


def test_guard_alert(monkeypatch):
    monkeypatch.setattr(patches, "_applied", False)
    monkeypatch.setattr(patches, "check", lambda: ["7 Bot._post"])
    for _ in range(2):
        with pytest.raises(SystemExit) as exc:
            patches.apply()
        assert exc.value.code and exc.value.code != 0
    assert len(SENT) == 1 and "7 Bot._post" in SENT[0] and "Hermes 0.21.5" in SENT[0]


def test_breakage_alert(db, home):
    db.session()
    db.add("user", "hi")

    hermes.check_version()
    assert not SENT
    (home / "hermes-agent" / "install-stamp.json").write_text(json.dumps({"baseVersion": "0.22.0"}))
    hermes.check_version()
    hermes.check_version()
    assert len(SENT) == 1 and "Hermes 0.22.0" in SENT[0]

    assert parse_prompt("║ ⚠ approval required\n║ 1. Yes\n║ 2. No\n") is None
    assert len(SENT) == 2

    db.con.execute("alter table messages rename column active to live")
    for _ in range(2):
        path = hermes.mirror("s1")
        assert hermes.resumable_sessions("/w", None) == []
    assert not path.exists() or path.read_text() == ""
    assert len(SENT) == 3 and "messages.active" in SENT[2]


def test_release(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    state = {
        "window_states": {
            "@1": {"provider_name": "hermes", "provider_manual_override": True, "session_id": "h1",
                   "transcript_path": "/m/h1.jsonl", "initial_provider_name": "hermes", "cwd": "/w"},
            "@2": {"provider_name": "pi", "session_id": "p1", "transcript_path": "/p.jsonl"},
        },
        "user_window_offsets": {"42": {"@1": 10, "@2": 20}},
    }
    (cfg / "state.json").write_text(json.dumps(state))
    (cfg / "monitor_state.json").write_text(json.dumps({"tracked_sessions": {"h1": {}, "p1": {}}}))
    (cfg / "session_map.json").write_text(json.dumps({"ccgram:@1": {"provider_name": "hermes"},
                                                      "ccgram:@2": {"provider_name": "pi"}}))
    assert cli.release(cfg) == 1
    got = json.loads((cfg / "state.json").read_text())
    assert got["window_states"]["@1"] == {"session_id": "", "cwd": "/w"}
    assert got["window_states"]["@2"] == state["window_states"]["@2"]
    assert got["user_window_offsets"] == {"42": {"@2": 20}}
    assert json.loads((cfg / "monitor_state.json").read_text()) == {"tracked_sessions": {"p1": {}}}
    assert json.loads((cfg / "session_map.json").read_text()) == {"ccgram:@2": {"provider_name": "pi"}}
    assert cli.release(cfg) == 0
