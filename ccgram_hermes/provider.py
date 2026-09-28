"""HermesProvider: Hermes behind ccgram's provider protocol."""

from __future__ import annotations

import dataclasses
import functools
import json
import re
import subprocess
import threading
import time
from typing import Any

from ccgram.multiplexer.herdr import _session_composite, herdr_session_target_id
from ccgram.providers._jsonl import JsonlProvider
from ccgram.providers.base import (
    DiscoveredCommand,
    ProviderCapabilities,
    ResumableSession,
    SessionStartEvent,
    StatusUpdate,
)
from ccgram.providers.pi import PiProvider

from . import alerts, hermes

UI_TYPE = "PermissionPrompt"
LABELS = {"Allow once", "Allow this session", "Always allow", "Deny"}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_OPTION = re.compile(r"^\s*(?:❯\s*)?\d+\.\s+(.+?)\s*$")
_LIVE_TTL_S = 2.0


def parse_prompt(pane_text: str) -> StatusUpdate | None:
    """Turn Hermes' approval box into an interactive status, else None."""
    lines = _ANSI.sub("", pane_text).splitlines()
    top = next((i for i in range(len(lines) - 1, -1, -1) if "⚠ approval required" in lines[i]), None)
    if top is None:
        return None
    body: list[str] = []
    for line in lines[top:]:
        s = line.strip()
        if not s.startswith("║"):
            break
        body.append(re.sub(r"^(\s*)▸", r"\1❯", s[1:].rstrip("║").rstrip()))
    labels = [m.group(1) for m in map(_OPTION.match, body) if m]
    if not (body and "quick pick" in body[-1] and len(labels) >= 2
            and set(labels) <= LABELS and labels[-1] == "Deny"):
        alerts.alert("prompt", "A Hermes approval prompt did not parse. Answer it on Darkseid.")
        return None
    return StatusUpdate(raw_text="\n".join(body).strip(), display_label=UI_TYPE,
                        is_interactive=True, ui_type=UI_TYPE)


_live_lock = threading.Lock()
_live_cache: tuple[float, dict[str, dict]] = (0.0, {})


def live_hermes() -> dict[str, dict]:
    """Herdr session target -> agent record, for Hermes panes."""
    global _live_cache
    with _live_lock:
        if time.monotonic() - _live_cache[0] < _LIVE_TTL_S:
            return _live_cache[1]
        try:
            out = subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True,
                                 timeout=5, check=False).stdout
            agents = json.loads(out)["result"]["agents"]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            agents = []
        res = {}
        for rec in agents:
            comp = _session_composite(rec)
            if comp and comp.agent == "hermes":
                res[herdr_session_target_id(comp)] = rec
        _live_cache = (time.monotonic(), res)
        return res


@functools.cache
def capabilities() -> ProviderCapabilities:
    return dataclasses.replace(HermesProvider._CAPS, builtin_commands=tuple(hermes.commands()))


class HermesProvider(PiProvider):
    _CAPS = ProviderCapabilities(
        name="hermes",
        launch_command="hermes",
        supports_resume=True,
        supports_resume_picker=True,
        supports_continue=True,
        supports_structured_transcript=True,
        supports_incremental_read=True,
    )
    _BUILTINS: dict[str, str] = {}

    @property
    def capabilities(self) -> ProviderCapabilities:
        return capabilities()

    def make_launch_args(self, resume_id: str | None = None, use_continue: bool = False) -> str:
        if resume_id:
            return JsonlProvider.make_launch_args(self, resume_id)
        return "--continue" if use_continue else ""

    def parse_transcript_line(self, line: str) -> dict[str, Any] | None:
        # A line without its newline is still being written: let ccgram retry it.
        return super().parse_transcript_line(line) if line.endswith("\n") else None

    def parse_terminal_status(self, pane_text: str, *, pane_title: str = "") -> StatusUpdate | None:  # noqa: ARG002
        return parse_prompt(pane_text)

    def discover_transcript(self, cwd: str, window_key: str, *, max_age: float | None = None) -> SessionStartEvent | None:  # noqa: ARG002
        target = window_key.split(":", 1)[1] if ":" in window_key else window_key
        rec = live_hermes().get(target)
        sid = ((rec or {}).get("agent_session") or {}).get("value")
        if not sid:
            return None
        return SessionStartEvent(session_id=sid, cwd=rec.get("cwd") or cwd,
                                 transcript_path=str(hermes.mirror(sid)), window_key=window_key)

    def discover_resumable_sessions(self, *, cwd: str | None = None, limit: int | None = None) -> list[ResumableSession]:
        return hermes.resumable_sessions(cwd, limit)

    def discover_commands(self, base_dir: str) -> list[DiscoveredCommand]:  # noqa: ARG002
        return [DiscoveredCommand(name=n, description=d, source="builtin") for n, d in hermes.commands().items()]
