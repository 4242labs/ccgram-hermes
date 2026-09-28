"""Isolation first: ccgram reads its env at import, so set it before anything imports ccgram."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="ccgram-hermes-test-"))
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
os.environ["ALLOWED_USERS"] = "42"
os.environ["CCGRAM_DIR"] = str(_ROOT / "ccgram")
_cwd = os.getcwd()
os.chdir(_ROOT)  # ccgram's config also loads ./.env
import ccgram.config  # noqa: E402, F401

os.chdir(_cwd)

import pytest  # noqa: E402

from ccgram_hermes import alerts, hermes, patches, provider  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
SENT: list[str] = []
alerts._send = SENT.append  # never reach the network

COMMANDS_PY = '''
COMMAND_REGISTRY = [
    CommandDef("model", "Switch the model", "Config", aliases=("m",)),
    CommandDef("compress", "Compress the context", "Session"),
    CommandDef("approve", "Approve a gateway action", "Gateway", gateway_only=True),
    CommandDef("Bad-Name", "Not a Telegram name", "Misc"),
]
'''


def make_home(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / "state.db") as con:
        con.executescript((FIXTURES / "schema.sql").read_text())
    agent = root / "hermes-agent"
    (agent / "hermes_cli").mkdir(parents=True)
    (agent / "install-stamp.json").write_text(json.dumps({"baseVersion": "0.21.5"}))
    (agent / "hermes_cli" / "commands.py").write_text(COMMANDS_PY)
    return root


PRE_APPLY_FAILS: list[str] = []


@pytest.fixture(scope="session", autouse=True)
def applied():
    hermes.HOME = make_home(_ROOT / "hermes-session")
    PRE_APPLY_FAILS.extend(patches.check())
    patches.apply()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGRAM_DIR", str(tmp_path / "ccgram"))
    monkeypatch.setattr(hermes, "HOME", make_home(tmp_path / "hermes"))
    provider.capabilities.cache_clear()
    SENT.clear()
    yield tmp_path / "hermes"
    provider.capabilities.cache_clear()


class Db:
    """Writable handle on the fixture state.db, playing the part of Hermes."""

    def __init__(self, path: Path):
        self.con = sqlite3.connect(path, isolation_level=None)

    def session(self, sid="s1", *, cwd="/w", source="cli", title=None, at=1.0, archived=0, hidden=0):
        self.con.execute(
            "insert into sessions (id, source, cwd, title, started_at, last_activity_at, archived, hidden) "
            "values (?, ?, ?, ?, ?, ?, ?, ?)", (sid, source, cwd, title, at, at, archived, hidden))
        return sid

    def add(self, role, content=None, *, sid="s1", calls=None, call_id=None, tool=None, ts=1.0):
        tool_calls = json.dumps([{"id": c, "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                                 for c, n, a in calls]) if calls else None
        cur = self.con.execute(
            "insert into messages (session_id, role, content, tool_calls, tool_call_id, tool_name, timestamp) "
            "values (?, ?, ?, ?, ?, ?, ?)", (sid, role, content, tool_calls, call_id, tool, ts))
        return cur.lastrowid


@pytest.fixture
def db(home):
    d = Db(home / "state.db")
    yield d
    d.con.close()
