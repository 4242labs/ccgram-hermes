"""Read-only access to Hermes' own files: state.db, command registry, version."""

from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from ccgram.providers.base import ResumableSession
from ccgram.utils import ccgram_dir

from . import alerts

HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
KNOWN_VERSION_PREFIX = "0.21."
PRUNE_AFTER_S = 7 * 86400

REQUIRED = {
    "messages": {"id", "session_id", "role", "content", "tool_call_id", "tool_calls",
                 "tool_name", "timestamp", "active"},
    "sessions": {"id", "source", "cwd", "title", "started_at", "last_activity_at",
                 "message_count", "archived", "hidden"},
}
_COMMAND_NAME = re.compile(r"^[a-z0-9_]{1,32}$")
_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    return sqlite3.connect((HOME / "state.db").as_uri() + "?mode=ro", uri=True, timeout=2)


def schema_ok(con: sqlite3.Connection) -> bool:
    missing = sorted(
        f"{table}.{col}"
        for table, cols in REQUIRED.items()
        for col in cols - {r[1] for r in con.execute(f"pragma table_info({table})")}
    )
    if missing:
        alerts.alert("columns", f"Hermes state.db lacks {', '.join(missing)}. Hermes replies stop until fixed.")
    return not missing


def version() -> str:
    try:
        return json.loads((HOME / "hermes-agent" / "install-stamp.json").read_text())["baseVersion"]
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


def check_version() -> None:
    v = version()
    if not v.startswith(KNOWN_VERSION_PREFIX):
        alerts.alert("version", f"Hermes {v} is untested with ccgram-hermes (built for {KNOWN_VERSION_PREFIX}x).")


# ── Transcript mirror ────────────────────────────────────────────────────


def mirror_dir() -> Path:
    return ccgram_dir() / "hermes" / "transcripts"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def _entry(row: tuple) -> dict | None:
    """One messages row as a Pi-style envelope, so ccgram's Pi parsing applies."""
    mid, role, content, tool_calls, tool_name, call_id, ts = row
    if role == "assistant":
        blocks: list[dict] = [{"type": "text", "text": content}] if content else []
        try:
            calls = json.loads(tool_calls or "[]")
        except ValueError:
            calls = []
        for tc in calls if isinstance(calls, list) else []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {"raw": fn.get("arguments")}
            blocks.append({
                "type": "toolCall",
                "id": tc.get("id") or tc.get("call_id") or "",
                "name": fn.get("name") or "unknown",
                "arguments": args if isinstance(args, dict) else {"raw": args},
            })
        msg = {"role": "assistant", "content": blocks}
    elif role == "tool":
        msg = {"role": "toolResult", "toolCallId": call_id or "", "toolName": tool_name or "",
               "content": content or ""}
    elif role == "user":
        msg = {"role": "user", "content": content or ""}
    else:
        return None
    return {"type": "message", "hermes_id": mid, "timestamp": _iso(ts), "message": msg}


def _last_id(path: Path) -> int:
    """Cut a torn last line, then return the last complete line's hermes_id."""
    try:
        f = path.open("rb+")
    except FileNotFoundError:
        return 0
    with f:
        size = pos = f.seek(0, 2)
        buf = b""
        while pos and buf.count(b"\n") < 2:
            step = min(1 << 16, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
        cut = buf.rfind(b"\n") + 1
        if pos + cut < size:
            f.truncate(pos + cut)
        if not cut:
            return 0
        return json.loads(buf[:cut].split(b"\n")[-2])["hermes_id"]


def mirror(session_id: str) -> Path:
    """Append the session's new active rows to its mirror file; return its path."""
    d = mirror_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    d.chmod(0o700)
    path = d / f"{session_id}.jsonl"
    with _lock:
        last = _last_id(path)
        with closing(connect()) as con:
            if not schema_ok(con):
                return path
            rows = con.execute(
                "select id, role, content, tool_calls, tool_name, tool_call_id, timestamp "
                "from messages where session_id = ? and active = 1 and id > ? order by id",
                (session_id, last),
            ).fetchall()
        lines = "".join(json.dumps(e) + "\n" for e in map(_entry, rows) if e)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(lines)
    return path


def prune(bound: set[str]) -> None:
    """Delete mirror files not bound to a window and untouched for 7 days."""
    import time

    for p in mirror_dir().glob("*.jsonl") if mirror_dir().is_dir() else ():
        if str(p) not in bound and time.time() - p.stat().st_mtime > PRUNE_AFTER_S:
            p.unlink(missing_ok=True)


# ── Prompts, resume, commands ────────────────────────────────────────────


def oldest_open_call(session_id: str) -> str | None:
    """Oldest tool call of the current turn with no result row yet."""
    with closing(connect()) as con:
        rows = con.execute(
            "select role, tool_calls, tool_call_id from messages "
            "where session_id = ? and active = 1 and role in ('user', 'assistant', 'tool') order by id",
            (session_id,),
        ).fetchall()
    open_calls: list[str] = []
    for role, tool_calls, call_id in rows:
        if role == "user":
            open_calls = []
        elif role == "assistant" and tool_calls:
            try:
                open_calls += [tc.get("id") or tc.get("call_id") for tc in json.loads(tool_calls)]
            except (ValueError, AttributeError, TypeError):
                continue
        elif role == "tool" and call_id in open_calls:
            open_calls.remove(call_id)
    return next((c for c in open_calls if c), None)


def resumable_sessions(cwd: str | None, limit: int | None) -> list[ResumableSession]:
    sql = (
        "select s.id, coalesce(nullif(s.title, ''), (select substr(m.content, 1, 80) from messages m "
        "where m.session_id = s.id and m.role = 'user' order by m.id limit 1), ''), s.cwd, "
        "coalesce(s.last_activity_at, s.started_at), s.message_count from sessions s "
        "where s.source in ('cli', 'tui') and s.archived = 0 and s.hidden = 0"
    )
    args: list = []
    if cwd:
        sql += " and s.cwd = ?"
        args.append(cwd)
    sql += " order by 4 desc limit ?"
    args.append(limit if limit else -1)
    with closing(connect()) as con:
        if not schema_ok(con):
            return []
        rows = con.execute(sql, args).fetchall()
    return [
        ResumableSession(session_id=sid, summary=summary or "", cwd=scwd or "",
                         provider_name="hermes", mtime=mtime or 0.0, msg_count=count)
        for sid, summary, scwd, mtime, count in rows
    ]


def commands() -> dict[str, str]:
    """Hermes' own slash commands, parsed from its registry without importing it."""
    try:
        tree = ast.parse((HOME / "hermes-agent" / "hermes_cli" / "commands.py").read_text())
    except (OSError, SyntaxError, ValueError):
        return {}
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "CommandDef"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        gw = kw.get("gateway_only")
        if isinstance(gw, ast.Constant) and gw.value:
            continue
        vals = [a.value for a in node.args[:2] if isinstance(a, ast.Constant)]
        if len(vals) == 2 and all(isinstance(v, str) for v in vals) and _COMMAND_NAME.match(vals[0]):
            out[vals[0]] = vals[1]
    return out
