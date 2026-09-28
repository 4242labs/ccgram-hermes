import asyncio
import subprocess
from types import SimpleNamespace

import pytest
from ccgram import bot
from ccgram.handlers import agent_command as ac
from ccgram.handlers import registry as hr
from ccgram.handlers.messaging_pipeline import message_sender
from ccgram.handlers.recovery import recovery_banner
from ccgram.providers import get_provider_for_window
from ccgram.session_resolver import session_resolver
from ccgram.window_state_ports import identity_state
from telegram.ext import CommandHandler, TypeHandler

from ccgram_hermes import hermes, patches, silence
from ccgram_hermes.provider import HermesProvider

AGNOSTIC = ("start", "sessions", "unbind", "rollback", "sync", "panes", "split", "screenshot", "live",
            "toolbar", "send", "verbose", "toolcalls", "last", "recall", "restore")
PARITY = dict.fromkeys(AGNOSTIC, "same as Pi") | {
    "history": "mirror", "commands": "hermes registry", "resume": "hermes sessions",
    "agent": "picker", "provider": "picker", "upgrade": "blocked",
}


def test_parity_matrix():
    assert set(hr.COMMAND_NAMES) == set(PARITY), "classify the new ccgram command in PARITY"
    app = bot.create_bot()
    cmds = {c: h.callback for h in app.handlers[0] if isinstance(h, CommandHandler) for c in h.commands}
    assert set(cmds) == set(PARITY)
    for name, cb in cmds.items():
        if name == "upgrade":
            assert cb is patches.upgrade_blocked
        else:
            assert cb.__module__.startswith("ccgram."), name
    (gate,) = app.handlers[-2]
    assert isinstance(gate, TypeHandler) and gate.callback is silence.gate
    assert "hermes" in ac._PROVIDER_ORDER and "hermes" in ac._VALID_NAMES
    import ccgram.providers as P

    assert P.detect_provider_from_command("hermes --continue") == "hermes"
    assert P.detect_provider_from_command("/opt/bin/hermes") == "hermes"
    assert P.detect_provider_from_command("pi") == "pi"
    assert isinstance(get_provider_for_window("@1", provider_name="hermes"), HermesProvider)


async def test_history(db, monkeypatch):
    db.session()
    db.add("user", "hi")
    db.add("assistant", "hello", calls=[("c1", "terminal", {"command": "ls"})])
    db.add("tool", "a.txt", call_id="c1", tool="terminal")
    path = hermes.mirror("s1")

    async def resolve(_wid):
        return SimpleNamespace(file_path=str(path))

    monkeypatch.setattr(session_resolver, "resolve_session_for_window", resolve)
    monkeypatch.setattr(identity_state, "get_provider_name", lambda _w: "hermes")
    msgs, total = await session_resolver.get_recent_messages("@1")
    assert total == len(msgs) >= 3
    assert {"hi", "hello"} <= {m["text"] for m in msgs}


def test_restore(monkeypatch):
    monkeypatch.setattr(recovery_banner.window_query, "get_window_provider", lambda _w: "hermes")
    labels = [b.text for row in recovery_banner.build_recovery_keyboard("@1").inline_keyboard for b in row]
    assert any("Continue" in t for t in labels) and any("Resume" in t for t in labels)
    assert HermesProvider().make_launch_args(use_continue=True) == "--continue"
    assert HermesProvider().make_launch_args() == ""


def test_commands():
    prov = HermesProvider()
    assert set(prov.capabilities.builtin_commands) == {"model", "compress"}
    found = {c.name: c.description for c in prov.discover_commands("/w")}
    assert found == {"model": "Switch the model", "compress": "Compress the context"}


def test_resume(db):
    db.session("old", at=1.0, title="Old work")
    db.session("new", at=5.0)
    db.add("user", "fix the build " + "x" * 100, sid="new")
    db.session("tg", source="telegram", at=9.0)
    db.session("arch", archived=1, at=9.0)
    db.session("hid", hidden=1, at=9.0)
    db.session("else", cwd="/x", at=9.0)
    got = HermesProvider().discover_resumable_sessions(cwd="/w")
    assert [s.session_id for s in got] == ["new", "old"]
    assert got[0].summary == ("fix the build " + "x" * 100)[:80] and got[1].summary == "Old work"
    assert all(s.provider_name == "hermes" for s in got)
    assert [s.session_id for s in HermesProvider().discover_resumable_sessions(cwd="/w", limit=1)] == ["new"]
    assert HermesProvider().make_launch_args(resume_id="new") == "--resume new"
    with pytest.raises(ValueError):
        HermesProvider().make_launch_args(resume_id="x; rm -rf ~")


async def test_upgrade_blocked(monkeypatch):
    replies = []

    async def safe_reply(_msg, text, **_kw):
        replies.append(text)

    def boom(*_a, **_kw):
        raise AssertionError("upgrade ran something")

    monkeypatch.setattr(message_sender, "safe_reply", safe_reply)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    await patches.upgrade_blocked(SimpleNamespace(effective_user=SimpleNamespace(id=42), message=object()), None)
    await patches.upgrade_blocked(SimpleNamespace(effective_user=SimpleNamespace(id=7), message=object()), None)
    assert replies == [patches.UPGRADE_REPLY, "You are not authorized to use this bot."]
