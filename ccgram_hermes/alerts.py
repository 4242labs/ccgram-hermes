"""Rate-limited warnings to the operator's Telegram chat."""

from __future__ import annotations

import json
import logging
import os
import threading
import time

import httpx
from ccgram.utils import ccgram_dir

from . import silence

HOURLY = 3600
_API = "https://" + "api." + "tele" + "gram.org"
_lock = threading.Lock()
log = logging.getLogger(__name__)


def _send(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    users = [u.strip() for u in os.environ.get("ALLOWED_USERS", "").split(",") if u.strip()]
    if not token or not users:
        return
    try:
        httpx.post(f"{_API}/bot{token}/sendMessage", json={"chat_id": int(users[0]), "text": text},
                   timeout=10)
    except Exception as exc:  # noqa: BLE001
        # The exception text can carry the URL, which holds the token.
        log.warning("ccgram-hermes alert not sent: %s", type(exc).__name__)


def alert(kind: str, text: str, *, wait: bool = False) -> bool:
    """Send one warning per kind per hour. Silence mutes it. True if sent."""
    if silence.is_on():
        return False
    path = ccgram_dir() / "hermes" / "alerts.json"
    with _lock:
        try:
            sent = json.loads(path.read_text())
        except (OSError, ValueError):
            sent = {}
        now = time.time()
        if now - sent.get(kind, 0) < HOURLY:
            return False
        sent[kind] = now
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps(sent))
    from . import hermes

    body = f"⚠ ccgram-hermes: {text}\nHermes {hermes.version()}"
    if wait:
        _send(body)
    else:
        threading.Thread(target=_send, args=(body,), daemon=True).start()
    return True
