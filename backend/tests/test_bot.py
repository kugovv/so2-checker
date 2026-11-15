import asyncio
from types import SimpleNamespace

import pytest

from app import bot


def harness(monkeypatch, *, locked=True, webhook=""):
    calls = {"polls": 0, "answers": [], "closed": False, "lock_closed": False}

    class Connection:
        def scalar(self, statement):
            assert "pg_try_advisory_lock" in str(statement)
            return locked

        def close(self):
            calls["lock_closed"] = True

    class FakeBot:
        def __init__(self, token):
            self.session = SimpleNamespace(close=self.close)

        async def close(self):
            calls["closed"] = True

        async def get_webhook_info(self):
            return SimpleNamespace(url=webhook)

        async def set_chat_menu_button(self, **kwargs):
            calls["menu"] = kwargs["menu_button"]

        async def set_my_commands(self, commands):
            calls["commands"] = commands

    class Message:
        def __init__(self, kind):
            self.chat = SimpleNamespace(type=kind)

        async def answer(self, text, **kwargs):
            calls["answers"].append((self.chat.type, text, kwargs))

    class Dispatcher:
        def __init__(self, **kwargs):
            assert kwargs["storage"] is not None
            assert kwargs["events_isolation"] is not None

        def include_router(self, router):
            calls["screenshot_router"] = router

        def message(self, *filters):
            def register(handler):
                self.start_handler = handler
                return handler
            return register

        async def start_polling(self, fake_bot, **kwargs):
            calls["polls"] += 1
            assert kwargs["allowed_updates"] == ["message", "callback_query"]
            await self.start_handler(Message("private"))
            await self.start_handler(Message("group"))

    monkeypatch.setattr(bot, "engine", SimpleNamespace(connect=Connection, dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(bot, "Bot", FakeBot)
    monkeypatch.setattr(bot, "Dispatcher", Dispatcher)
    monkeypatch.setattr(bot, "get_settings", lambda: SimpleNamespace(bot_token="test-only", public_url="https://frontend.example.test/app/", bot_username="test_bot"))
    return calls


def test_start_replies_with_mini_app_button_without_chat_stats(monkeypatch):
    calls = harness(monkeypatch)
    asyncio.run(bot.run())
    assert calls["polls"] == 1
    private, group = calls["answers"]
    assert private[0] == "private" and len(private[1]) < 300
    button = private[2]["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Открыть приложение"
    assert button.web_app.url == "https://frontend.example.test/app/"
    assert calls["menu"].web_app.url == button.web_app.url
    assert calls["commands"][0].command == "start"
    assert any(command.command == "match" for command in calls["commands"])
    assert calls["screenshot_router"].name == "screenshot-ratings"
    assert group[0] == "group" and "reply_markup" not in group[2]
    assert calls["closed"] and calls["lock_closed"]


def test_bot_refuses_competing_webhook_without_deleting_it(monkeypatch):
    calls = harness(monkeypatch, webhook="https://existing.example.test/webhook")
    with pytest.raises(RuntimeError, match="existing webhook"):
        asyncio.run(bot.run())
    assert calls["polls"] == 0
    assert calls["closed"] and calls["lock_closed"]


def test_bot_refuses_second_poller(monkeypatch):
    calls = harness(monkeypatch, locked=False)
    with pytest.raises(RuntimeError, match="Another bot process"):
        asyncio.run(bot.run())
    assert calls["polls"] == 0
