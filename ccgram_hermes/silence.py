"""/silence: one global, fail-closed mute that also closes every session topic. Nothing is replayed."""

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


async def close_topics(bot) -> int:
    """Unbind every topic, sessions keep running, then delete it from Telegram."""
    from ccgram.handlers.cleanup import clear_topic_state
    from ccgram.telegram_client import PTBTelegramClient
    from ccgram.thread_router import thread_router

    client = PTBTelegramClient(bot)
    closed = 0
    for user_id, chat_id, thread_id, window_id in list(thread_router.iter_thread_bindings_with_chat()):
        chat = chat_id if chat_id is not None else thread_router.resolve_chat_id(user_id, thread_id)
        with suppress(Exception):
            await clear_topic_state(user_id, thread_id, client, None, window_id, chat, window_dead=False)
        thread_router.unbind_thread(user_id, thread_id, chat_id=chat_id)
        with suppress(TelegramError):
            closed += bool(await bot.delete_forum_topic(chat, thread_id))
    return closed


def label() -> str:
    return "Silence is on. Session topics are closed. Only /silence replies." if is_on() else "Silence is off."


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
            if is_on() and (cq or arg == "on"):
                await close_topics(_context.bot)
        finally:
            _replying.reset(token)
        raise ApplicationHandlerStop
    if is_on():
        raise ApplicationHandlerStop
