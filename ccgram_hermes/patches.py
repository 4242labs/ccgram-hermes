"""The seven runtime patches onto ccgram. Every target is checked before any is applied."""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from collections.abc import Callable

CCGRAM_VERSION = "4.12.3"
PICKER_BEFORE = ("antigravity", "claude", "codex", "gemini", "pi", "shell")
UPGRADE_REPLY = "Upgrade ccgram-hermes instead: uv tool upgrade ccgram-hermes"
_applied = False


def _guards() -> list[tuple[str, Callable[[], bool]]]:
    import ccgram
    import ccgram.main as main
    import ccgram.providers as P
    from ccgram import bot
    from ccgram.handlers import agent_command as ac
    from ccgram.handlers import registry as hr
    from ccgram.handlers.interactive import interactive_ui as iui
    from ccgram.window_state_ports import identity_state
    from telegram import Bot
    from telegram.ext import ExtBot

    P._ensure_registered()
    return [
        ("ccgram version", lambda: ccgram.__version__ == CCGRAM_VERSION),
        ("1 provider registry", lambda: callable(P.registry.register) and not P.registry.is_valid("hermes")),
        ("2 provider detection", lambda: P.detect_provider_from_command("hermes") != "hermes"),
        ("3 agent picker", lambda: ac._PROVIDER_ORDER == PICKER_BEFORE and ac._VALID_NAMES == frozenset(PICKER_BEFORE) | {"auto"}),
        ("4 upgrade command", lambda: callable(hr.upgrade_command)
            and 'CommandSpec("upgrade", upgrade_command)' in inspect.getsource(hr.register_all)),
        ("5 create_bot", lambda: callable(bot.create_bot) and not hasattr(main, "create_bot")
            and "from .bot import create_bot" in inspect.getsource(main)),
        ("6 prompt capture", lambda: inspect.iscoroutinefunction(iui._capture_interactive_content)
            and inspect.getsource(iui).count("_capture_interactive_content(") == 2
            and callable(iui.get_window_provider) and callable(identity_state.get_session_id)),
        ("7 Bot._post", lambda: "_post" not in ExtBot.__dict__ and inspect.iscoroutinefunction(Bot._post)),
    ]


def check() -> list[str]:
    """Names of the patch targets that are not what this plugin was built against."""
    try:
        guards = _guards()
    except Exception as exc:  # noqa: BLE001
        return [f"import: {type(exc).__name__}: {exc}"]
    fails = []
    for name, ok in guards:
        try:
            good = ok()
        except Exception:  # noqa: BLE001
            good = False
        if not good:
            fails.append(name)
    return fails


def tag_prompt(got: tuple[str, str], session_id: str | None) -> tuple[str, str] | None:
    """Tie a Hermes prompt to its tool call, so a new prompt with the same text is new."""
    from . import hermes

    call = hermes.oldest_open_call(session_id) if session_id else None
    return (got[0], f"{got[1]}\n\n#{call}") if call else None


async def upgrade_blocked(update, _context) -> None:
    from ccgram.config import config
    from ccgram.handlers.messaging_pipeline.message_sender import safe_reply

    user = update.effective_user
    if not user or not update.message:
        return
    if not config.is_user_allowed(user.id):
        await safe_reply(update.message, "You are not authorized to use this bot.")
        return
    await safe_reply(update.message, UPGRADE_REPLY)


def apply() -> None:
    global _applied
    if _applied:
        return
    from . import alerts

    fails = check()
    if fails:
        alerts.alert("guard", "ccgram-hermes stopped. Changed in ccgram: " + ", ".join(fails), wait=True)
        sys.exit("ccgram-hermes: patch guard failed: " + ", ".join(fails))

    import ccgram.providers as P
    from ccgram import bot
    from ccgram.handlers import agent_command as ac
    from ccgram.handlers import registry as hr
    from ccgram.handlers.interactive import interactive_ui as iui
    from ccgram.utils import ccgram_dir
    from ccgram.window_state_ports import identity_state
    from telegram import Bot, Update
    from telegram.ext import TypeHandler

    from . import hermes, silence
    from .provider import HermesProvider

    # 1
    P.registry.register("hermes", HermesProvider)

    # 2
    detect = P.detect_provider_from_command

    def detect_provider_from_command(cmd: str) -> str:
        base = cmd.strip().split()[0].rsplit("/", 1)[-1].lower() if cmd.strip() else ""
        return "hermes" if base == "hermes" or base.startswith("hermes-") else detect(cmd)

    P.detect_provider_from_command = detect_provider_from_command

    # 3
    ac._PROVIDER_ORDER = tuple(sorted((*PICKER_BEFORE, "hermes")))
    ac._VALID_NAMES = frozenset(ac._PROVIDER_ORDER) | {"auto"}

    # 4
    hr.upgrade_command = upgrade_blocked

    # 5
    create = bot.create_bot

    def create_bot():
        app = create()
        app.add_handler(TypeHandler(Update, silence.gate), group=-2)
        return app

    bot.create_bot = create_bot

    # 6
    capture = iui._capture_interactive_content

    async def _capture_interactive_content(window_id: str, pane_id: str | None = None):
        got = await capture(window_id, pane_id=pane_id)
        if got is None or iui.get_window_provider(window_id) != "hermes":
            return got
        return await asyncio.to_thread(tag_prompt, got, identity_state.get_session_id(window_id))

    iui._capture_interactive_content = _capture_interactive_content

    # 7
    Bot._post = silence.gated_post

    _applied = True
    hermes.check_version()
    try:
        states = json.loads((ccgram_dir() / "state.json").read_text()).get("window_states", {})
    except (OSError, ValueError):
        states = {}
    hermes.prune({s.get("transcript_path", "") for s in states.values()})
