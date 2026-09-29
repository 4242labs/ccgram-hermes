import asyncio
from types import SimpleNamespace

from ccgram import bootstrap
from ccgram.handlers.text import text_handler as th
from ccgram.handlers.topics import topic_orchestration as to
from ccgram.multiplexer import herdr

from ccgram_hermes import topics


def test_agent_title():
    assert topics.agent_title({"terminal_title_stripped": "✓ ARCH HS 01 · claude-opus-5 · ~/x"}) == "ARCH HS 01"
    assert topics.agent_title({"terminal_title": "⠋ ALFRED ACC · gpt"}) == "ALFRED ACC"
    assert topics.agent_title({"name": "reviewer", "terminal_title": "✓ X · y"}) == "reviewer"
    assert topics.agent_title({"terminal_title": "Hermes"}) == "Hermes"
    assert topics.agent_title({}) == ""


def test_session_picker():
    long = "Hermes ▸ ALFRED OUTREACH WITH A VERY LONG TITLE"
    wins = [(f"w{i}", long if i == 0 else f"s{i}", "/tmp") for i in range(7)]
    text, kb, ids = th.build_window_picker(wins)
    assert long in text and "1. " in text and "7. " in text
    rows = kb.inline_keyboard
    assert [b.text for b in rows[0]] == ["1", "2", "3", "4", "5"] and [b.text for b in rows[1]] == ["6", "7"]
    assert rows[0][0].callback_data.endswith("0") and len(rows) == 3
    assert ids == [w for w, _, _ in wins]


def test_no_auto_topics(monkeypatch):
    assert bootstrap._handle_new_window is to.handle_new_window
    assert to.handle_new_window.__qualname__.startswith("on_request")
    calls = []

    async def create(event, client, *, target_user_id=None, target_chat_id=None):
        calls.append(target_user_id)
        return True

    wrapped = topics.on_request(create)
    ev = SimpleNamespace(window_id="w1")
    monkeypatch.setattr(to, "_is_window_already_bound", lambda w: False)
    assert asyncio.run(wrapped(ev, None)) is False and calls == []
    assert asyncio.run(wrapped(ev, None, target_user_id=42)) is True and calls == [42]
    monkeypatch.setattr(to, "_is_window_already_bound", lambda w: True)
    assert asyncio.run(wrapped(ev, None)) is True


def test_titles_name_topics():
    rec = {"agent": "hermes", "agent_session": {"source": "herdr", "agent": "hermes", "kind": "session", "value": "s1"},
           "terminal_id": "t1", "pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1", "cwd": "/w",
           "terminal_title_stripped": "✓ ALFRED ACC · gpt-6 · ~/alfred"}
    got = herdr._parse_live_record(rec)
    ref = herdr.HerdrManager._live_ref(got, "Hermes ▸ alfred ▸ 1 ▸ p1")
    assert ref.window_name == "Hermes ▸ ALFRED ACC" and ref.topic_eligible
