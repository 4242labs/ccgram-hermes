"""Topics open only when you pick a session, the picker is a numbered list, names are agent titles."""

from __future__ import annotations

import re
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

ROW = 5
_LEAD = re.compile(r"^[^\w~/]+")
titles: dict[str, str] = {}


def agent_title(record: dict) -> str:
    """Herdr's agent name, else the first part of the pane title without its status mark."""
    name = record.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    raw = record.get("terminal_title_stripped") or record.get("terminal_title")
    if not isinstance(raw, str):
        return ""
    return _LEAD.sub("", raw.split(" · ")[0]).strip()


def on_request(create):
    """Open a topic for a new session only when a user asked for it, or it is already bound."""

    async def handle_new_window(event, client, *, target_user_id=None, target_chat_id=None) -> bool:
        from ccgram.handlers.topics import topic_orchestration as to

        if target_user_id is None and not to._is_window_already_bound(event.window_id):
            return False
        return await create(event, client, target_user_id=target_user_id, target_chat_id=target_chat_id)

    return handle_new_window


def picker(windows: list[tuple[str, str, str]]) -> tuple[str, InlineKeyboardMarkup, list[str]]:
    from ccgram.handlers.callback_data import CB_WIN_BIND, CB_WIN_CANCEL, CB_WIN_NEW

    home = str(Path.home())
    lines = ["*Pick a session to open here*\n"]
    lines += [f"{i}. `{name}` — {cwd.replace(home, '~')}" for i, (_, name, cwd) in enumerate(windows, 1)]
    nums = [InlineKeyboardButton(str(i), callback_data=f"{CB_WIN_BIND}{i - 1}") for i in range(1, len(windows) + 1)]
    rows = [nums[i:i + ROW] for i in range(0, len(nums), ROW)]
    rows.append([InlineKeyboardButton("➕ New Session", callback_data=CB_WIN_NEW),
                 InlineKeyboardButton("Cancel", callback_data=CB_WIN_CANCEL)])
    return "\n".join(lines), InlineKeyboardMarkup(rows), [wid for wid, _, _ in windows]
