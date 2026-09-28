"""/silence: one global, fail-closed mute. Nothing is buffered or replayed."""

from __future__ import annotations

import contextvars
from contextlib import suppress

from ccgram.utils import ccgram_dir
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

_BLOCKED_PREFIXES = ("send", "edit", "forward", "copy")
_replying = contextvars.ContextVar("ccgram_hermes_silence_reply", default=False)
_orig_post = Bot._post
CB = "hsil:"


def _path():
    return ccgram_dir() / "hermes" / "silence"


def is_on() -> bool:
    return _path().exists()


def set_on(on: bool) -> None:
    p = _path()
    if on:
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        p.touch(mode=0o600)
    else:
        p.unlink(missing_ok=True)


def label() -> str:
    return "Silence is on. Only /silence replies." if is_on() else "Silence is off."


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("On", callback_data=CB + "on"),
        InlineKeyboardButton("Off", callback_data=CB + "off"),
    ]])


async def gated_post(self, endpoint, data=None, **kwargs):
    if (
        (endpoint.startswith(_BLOCKED_PREFIXES) or endpoint == "setMessageReaction")
        and not _replying.get()
        and is_on()
    ):
        raise TelegramError("silenced by ccgram-hermes")
    return await _orig_post(self, endpoint, data, **kwargs)


def _silence_arg(text: str | None) -> str | None:
    """Argument of a /silence command ('' when bare), None if not /silence."""
    parts = (text or "").split()
    if not parts:
        return None
    head = parts[0].lower()
    if head != "/silence" and not head.startswith("/silence@"):
        return None
    return parts[1].lower() if len(parts) > 1 else ""


async def gate(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -2: handle /silence and its buttons, stop everything else while on."""
    from ccgram.config import config

    user = update.effective_user
    allowed = user is not None and config.is_user_allowed(user.id)
    cq = update.callback_query
    msg = update.message
    arg = _silence_arg(msg.text) if msg and not cq else None
    handled = allowed and ((cq and (cq.data or "").startswith(CB)) or arg is not None)
    if handled:
        token = _replying.set(True)
        try:
            with suppress(TelegramError):
                if cq:
                    set_on(cq.data == CB + "on")
                    await cq.answer()
                    await cq.edit_message_text(label())
                elif arg in ("on", "off"):
                    set_on(arg == "on")
                    await msg.reply_text(label())
                else:
                    await msg.reply_text(label(), reply_markup=_keyboard())
        finally:
            _replying.reset(token)
        raise ApplicationHandlerStop
    if is_on():
        raise ApplicationHandlerStop
