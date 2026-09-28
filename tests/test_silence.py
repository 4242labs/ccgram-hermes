from types import SimpleNamespace

import pytest
from telegram import Bot
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop

from ccgram_hermes import alerts, silence


class Msg:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kw):
        assert silence._replying.get()
        self.replies.append((text, kw))


class Query:
    def __init__(self, data):
        self.data = data
        self.edits = []

    async def answer(self):
        pass

    async def edit_message_text(self, text, **_kw):
        self.edits.append(text)


def upd(text=None, *, user=42, data=None):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user), message=Msg(text) if text is not None else None,
                           callback_query=Query(data) if data else None)


@pytest.fixture
def posted(monkeypatch):
    calls = []

    async def post(_self, endpoint, data=None, **_kw):
        calls.append(endpoint)
        return True

    monkeypatch.setattr(silence, "_orig_post", post)
    return calls


async def test_silence(posted):
    assert Bot._post is silence.gated_post
    bot = object()

    u = upd("/silence on")
    with pytest.raises(ApplicationHandlerStop):
        await silence.gate(u, None)
    assert silence.is_on() and u.message.replies[0][0].startswith("Silence is on")

    with pytest.raises(ApplicationHandlerStop):
        await silence.gate(upd("hello"), None)
    with pytest.raises(ApplicationHandlerStop):
        await silence.gate(upd(data="other:1"), None)
    for endpoint in ("sendMessage", "editMessageText", "forwardMessage", "copyMessage", "setMessageReaction"):
        with pytest.raises(TelegramError):
            await silence.gated_post(bot, endpoint, {})
    await silence.gated_post(bot, "getUpdates", {})
    assert not alerts.alert("columns", "x")

    # strangers cannot flip it
    with pytest.raises(ApplicationHandlerStop):
        await silence.gate(upd("/silence off", user=7), None)
    assert silence.is_on()

    # bare shows On/Off buttons
    u = upd("/silence@ccgram_bot")
    with pytest.raises(ApplicationHandlerStop):
        await silence.gate(u, None)
    buttons = [b.callback_data for row in u.message.replies[0][1]["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["hsil:on", "hsil:off"]

    # survives a restart: state is a file
    assert (silence._path()).exists()

    u = upd(data="hsil:off")
    with pytest.raises(ApplicationHandlerStop):
        await silence.gate(u, None)
    assert not silence.is_on() and u.callback_query.edits == ["Silence is off."]
    assert await silence.gate(upd("hello"), None) is None
    assert await silence.gate(upd("/silence on", user=7), None) is None
    assert not silence.is_on()
    assert posted == ["getUpdates"]


async def test_no_replay(posted):
    bot = object()
    silence.set_on(True)
    for i in range(3):
        with pytest.raises(TelegramError):
            await silence.gated_post(bot, "sendMessage", {"text": f"old {i}"})
    silence.set_on(False)
    await silence.gated_post(bot, "sendMessage", {"text": "new"})
    assert posted == ["sendMessage"]
