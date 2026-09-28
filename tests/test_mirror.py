import hashlib
import json
import sqlite3
import stat

import pytest

from ccgram_hermes import hermes
from ccgram_hermes.provider import HermesProvider


def read(path):
    prov = HermesProvider()
    entries = [e for line in path.read_text().splitlines(keepends=True) if (e := prov.parse_transcript_line(line))]
    return prov.parse_transcript_entries(entries, {})[0]


def ids(path):
    return [json.loads(line)["hermes_id"] for line in path.read_text().splitlines()]


def test_mirror_roundtrip(db):
    db.session()
    db.add("user", "hi")
    db.add("assistant", "hello")
    path = hermes.mirror("s1")
    assert [m.text for m in read(path) if m.role == "assistant"] == ["hello"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    # tool-only turn
    db.add("assistant", None, calls=[("c1", "terminal", {"command": "ls"})])
    db.add("tool", "a.txt", call_id="c1", tool="terminal")
    hermes.mirror("s1")
    assert ids(path) == [1, 2, 3, 4]

    # replaced turn: a row deactivated before it was mirrored is never sent
    gone = db.add("assistant", "draft")
    db.con.execute("update messages set active = 0 where id >= ?", (gone,))
    db.add("assistant", "final")
    hermes.mirror("s1")
    assert ids(path) == [1, 2, 3, 4, 6]
    assert "draft" not in path.read_text()

    # display_order below id: id order wins
    a = db.add("assistant", "one")
    b = db.add("assistant", "two")
    db.con.execute("update messages set display_order = ? where id = ?", (1, b))
    db.con.execute("update messages set display_order = ? where id = ?", (2, a))
    hermes.mirror("s1")
    assert ids(path)[-2:] == [a, b]

    # torn last line: ccgram skips it, the next mirror cuts it
    with path.open("a") as f:
        f.write('{"type": "mess')
    assert HermesProvider().parse_transcript_line('{"type": "mess') is None
    db.add("assistant", "after")
    hermes.mirror("s1")
    assert ids(path)[-1] == a + 2
    assert all(line.endswith("\n") for line in path.read_text().splitlines(keepends=True))

    # nothing new: nothing appended, nothing repeated
    before = path.read_text()
    hermes.mirror("s1")
    assert path.read_text() == before
    assert len(ids(path)) == len(set(ids(path)))


def test_parse_tools(db):
    db.session()
    db.add("user", "list files")
    db.add("assistant", "Looking.", calls=[("c1", "terminal", {"command": "ls"})])
    db.add("tool", "a.txt\nb.txt", call_id="c1", tool="terminal")
    db.add("assistant", "Two files.")
    msgs = read(hermes.mirror("s1"))
    kinds = [(m.role, m.content_type) for m in msgs]
    assert ("assistant", "tool_use") in kinds
    assert ("assistant", "tool_result") in kinds or ("user", "tool_result") in kinds
    use = next(m for m in msgs if m.content_type == "tool_use")
    result = next(m for m in msgs if m.content_type == "tool_result")
    assert use.tool_use_id == result.tool_use_id == "c1"
    assert "a.txt" in result.text
    assert [m.text for m in msgs if m.content_type == "text" and m.role == "assistant"] == ["Looking.", "Two files."]


def test_state_db_read_only(db, home, monkeypatch):
    db.session()
    db.add("user", "hi")
    db.add("assistant", "yo", calls=[("c1", "terminal", {})])
    digest = hashlib.sha256((home / "state.db").read_bytes()).hexdigest()

    opened = []
    real = sqlite3.connect

    def spy(target, *args, **kwargs):
        opened.append((str(target), kwargs.get("uri")))
        return real(target, *args, **kwargs)

    monkeypatch.setattr(hermes.sqlite3, "connect", spy)
    hermes.mirror("s1")
    hermes.oldest_open_call("s1")
    hermes.resumable_sessions("/w", None)
    assert opened and all(t.endswith("?mode=ro") and uri for t, uri in opened)

    with pytest.raises(sqlite3.OperationalError, match="readonly"), hermes.connect() as con:
        con.execute("delete from messages")
    assert hashlib.sha256((home / "state.db").read_bytes()).hexdigest() == digest
