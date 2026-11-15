import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.screenshot_bot import (
    MAX_IMAGE_BYTES, ScreenshotController, create_screenshot_router, nickname_key, save_confirmed_screenshot,
)


def scoreboard(*, mode="competitive", rows=None):
    return SimpleNamespace(
        mode=mode, score=(8, 6), match_id="2188356974", map_name="Sandstone", warnings=[],
        rows=rows if rows is not None else [
            SimpleNamespace(nickname="hamy", kills=22, deaths=7, assists=0, team="ct", game_score=46, rank=1),
            SimpleNamespace(nickname="lov|Nagi", kills=12, deaths=11, assists=3, team="ct", game_score=31, rank=2),
        ],
    )


class FakeMessage:
    def __init__(self, text=None, *, caption=None, user_id=91, photo=True, kind="private", size=20, document=None):
        self.chat = SimpleNamespace(type=kind, id=user_id)
        self.from_user = SimpleNamespace(id=user_id)
        self.text, self.caption, self.document = text, caption, document
        self.photo = [SimpleNamespace(file_id="test-file", file_size=size)] if photo else []
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self


class FakeQuery:
    def __init__(self, message, data):
        self.message, self.data = message, data
        self.from_user = SimpleNamespace(id=message.chat.id)
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class DownloadBot:
    def __init__(self, payload=b"unit-test-image"):
        self.payload, self.downloads = payload, 0

    async def download(self, file_id, *, destination, timeout):
        assert file_id == "test-file" and timeout == 20
        self.downloads += 1
        destination.write(self.payload)


@asynccontextmanager
async def harness(board=None, *, analyzer=None, saver=None, public_url=None, ttl=600, clock=None):
    async def default_analyzer(payload):
        assert payload == b"unit-test-image"
        return board or scoreboard()
    async def default_saver(telegram_id, payload):
        return {"match": {}, "best": None, "is_new_best": False}

    async def no_nickname(_):
        return None
    kwargs = {"ttl": ttl, "cooldown": 0, "saver": saver or default_saver, "nickname_loader": no_nickname, "public_url": public_url}
    if clock:
        kwargs["clock"] = clock
    controller = ScreenshotController(analyzer or default_analyzer, **kwargs)
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=123, chat_id=91, user_id=91))
    try:
        yield controller, state, DownloadBot(), storage
    finally:
        await controller.close()
        await storage.close()


async def click(controller, state, message, action):
    data = await state.get_data()
    query = FakeQuery(message, f"shot:{data['nonce']}:{action}")
    await controller.callback(query, state)
    return query


def buttons(message):
    return [button for row in message.answers[-1][1]["reply_markup"].inline_keyboard for button in row]


def test_caption_requires_kda_confirmation_and_automatically_uses_match_rounds():
    async def scenario():
        async with harness() as (controller, state, bot, _):
            message = FakeMessage(caption="Ник: hamy")
            await controller.upload(message, state, bot)
            data = await state.get_data()
            assert data["phase"] == "confirm"
            assert "K/D/A: 22/7/0" in message.answers[-1][0]
            assert not any("Рейтинг за матч:" in text for text, _ in message.answers)
            await click(controller, state, message, "confirm")
            assert "🟢 Рейтинг за матч: 1.78" in message.answers[-1][0]
            assert "22/7/0" in message.answers[-1][0]
            assert "14 раундам матча" in message.answers[-1][0]
            assert "Полное участие предполагается" in message.answers[-1][0]
            assert await state.get_data() == {} and await state.get_state() is None
    asyncio.run(scenario())


def test_nickname_can_arrive_after_photo():
    async def scenario():
        async with harness() as (controller, state, bot, _):
            message = FakeMessage()
            await controller.upload(message, state, bot)
            assert (await state.get_data())["phase"] == "nickname"
            text = FakeMessage("lov|Nagi", photo=False)
            await controller.text(text, state)
            assert "K/D/A: 12/11/3" in text.answers[-1][0]
            assert (await state.get_data())["phase"] == "confirm"
    asyncio.run(scenario())


def test_case_width_and_invisible_space_matching_does_not_change_display_name():
    assert nickname_key(" ＨＡＭＹ\u200b ") == nickname_key("hamy")


def test_fuzzy_match_and_duplicate_exact_names_require_selection():
    async def scenario():
        async with harness() as (controller, state, bot, _):
            message = FakeMessage(caption="harny")
            await controller.upload(message, state, bot)
            assert (await state.get_data())["phase"] == "choose"
            row_button = next(button for button in buttons(message) if ":row:0" in button.callback_data)
            await controller.callback(FakeQuery(message, row_button.callback_data), state)
            assert (await state.get_data())["phase"] == "confirm"
            assert "Ваш ник: harny" in message.answers[-1][0]
            assert "На скриншоте распознано: hamy" in message.answers[-1][0]
            await click(controller, state, message, "confirm")
            assert "Игрок: harny" in message.answers[-1][0]
            assert "На скриншоте распознано: hamy" in message.answers[-1][0]
        rows = [dict(nickname="same", kills=9, deaths=10, assists=0, team="ct"),
                dict(nickname="same", kills=9, deaths=10, assists=10, team="t")]
        async with harness(scoreboard(rows=rows)) as (controller, state, bot, _):
            message = FakeMessage(caption="same")
            await controller.upload(message, state, bot)
            assert (await state.get_data())["phase"] == "choose"
            assert sum(":row:" in button.callback_data for button in buttons(message)) == 2
            await click(controller, state, message, "row:1")
            assert (await state.get_data())["selected"]["assists"] == 10
    asyncio.run(scenario())


def test_cyrillic_alias_and_english_fuzzy_name_keep_the_original_ocr_row_visible():
    async def scenario():
        rows = [dict(nickname="@tynou", aliases=[" @ТУПОЙ\u200b "], kills=7, deaths=12, assists=1, team="ct")]
        async with harness(scoreboard(rows=rows)) as (controller, state, bot, _):
            message = FakeMessage(caption="@тупой")
            await controller.upload(message, state, bot)
            data = await state.get_data()
            assert data["phase"] == "confirm" and data["selected"]["nickname"] == "@tynou"
            assert data["selected"]["aliases"] == ["@ТУПОЙ\u200b"]
            assert "Ваш ник: @тупой" in message.answers[-1][0]
            assert "На скриншоте распознано: @tynou" in message.answers[-1][0]
        rows = [dict(nickname="harny", aliases=["Багпу"], kills=22, deaths=7, assists=0, team="ct")]
        async with harness(scoreboard(rows=rows)) as (controller, state, bot, _):
            message = FakeMessage(caption="hamy")
            await controller.upload(message, state, bot)
            assert (await state.get_data())["phase"] == "choose"
            await click(controller, state, message, "row:0")
            assert "Ваш ник: hamy" in message.answers[-1][0]
            assert "На скриншоте распознано: harny" in message.answers[-1][0]
            assert not any("Рейтинг за матч:" in text for text, _ in message.answers)
            await click(controller, state, message, "confirm")
            assert "Рейтинг за матч: 1.78" in message.answers[-1][0]
    asyncio.run(scenario())


def test_shared_ocr_alias_is_ambiguous_and_cannot_auto_select_a_player():
    async def scenario():
        rows = [dict(nickname="one", aliases=["same"], kills=22, deaths=7, assists=0, team="ct"),
                dict(nickname="two", aliases=["same"], kills=9, deaths=10, assists=10, team="t")]
        async with harness(scoreboard(rows=rows)) as (controller, state, bot, _):
            message = FakeMessage(caption="same")
            await controller.upload(message, state, bot)
            assert (await state.get_data())["phase"] == "choose"
            assert sum(":row:" in button.callback_data for button in buttons(message)) == 2
    asyncio.run(scenario())


def test_manual_correction_and_mode_change_are_confirmed_again():
    async def scenario():
        async with harness() as (controller, state, bot, _):
            message = FakeMessage(caption="hamy")
            await controller.upload(message, state, bot)
            old_confirm = next(button.callback_data for button in buttons(message) if button.text == "Верно, рассчитать")
            await click(controller, state, message, "manual")
            invalid = FakeMessage("9/-10/10", photo=False)
            await controller.text(invalid, state)
            assert (await state.get_data())["phase"] == "manual"
            correction = FakeMessage("9/10/10", photo=False)
            await controller.text(correction, state)
            assert "K/D/A: 9/10/10" in correction.answers[-1][0]
            await click(controller, state, correction, "mode:allies")
            assert (await state.get_data())["mode"] == "allies"
            stale = FakeQuery(correction, old_confirm)
            await controller.callback(stale, state)
            assert stale.answers[-1][1]["show_alert"]
            assert not any("Рейтинг за матч:" in text for text, _ in correction.answers)
            await click(controller, state, correction, "confirm")
            assert "Рейтинг за матч: 1.07" in correction.answers[-1][0]
            assert "Напарники 2×2" in correction.answers[-1][0]
    asyncio.run(scenario())


def test_unknown_nickname_has_explicit_manual_option():
    async def scenario():
        async with harness() as (controller, state, bot, _):
            message = FakeMessage(caption="совсем другой игрок")
            await controller.upload(message, state, bot)
            assert (await state.get_data())["phase"] == "choose"
            await click(controller, state, message, "manual")
            assert (await state.get_data())["phase"] == "choose"
            await click(controller, state, message, "row:0")
            correction = FakeMessage("0 0 0", photo=False)
            await controller.text(correction, state)
            assert (await state.get_data())["selected"]["nickname"] == "hamy"
            assert (await state.get_data())["requested_nickname"] == "совсем другой игрок"
            await click(controller, state, correction, "confirm")
            assert "Рейтинг за матч: 0.50" in correction.answers[-1][0]
    asyncio.run(scenario())


def test_manual_statistics_share_the_zero_to_200_limit_with_ocr():
    async def scenario():
        async with harness() as (controller, state, bot, _):
            message = FakeMessage(caption="hamy")
            await controller.upload(message, state, bot)
            await click(controller, state, message, "manual")
            for value in ("201/7/0", "22/201/0", "22/7/201"):
                correction = FakeMessage(value, photo=False)
                await controller.text(correction, state)
                assert "от 0 до 200" in correction.answers[-1][0]
                assert (await state.get_data())["phase"] == "manual"
            valid = FakeMessage("200/0/200", photo=False)
            await controller.text(valid, state)
            assert "K/D/A: 200/0/200" in valid.answers[-1][0]
            assert (await state.get_data())["phase"] == "confirm"
    asyncio.run(scenario())


def test_missing_assists_and_unknown_mode_do_not_become_zero_or_competitive():
    async def scenario():
        board = scoreboard(mode=None, rows=[dict(nickname="x", kills=1, deaths=0, assists=None)])
        async with harness(board) as (controller, state, bot, _):
            message = FakeMessage(caption="x")
            await controller.upload(message, state, bot)
            assert (await state.get_data())["mode"] is None
            assert "Нет данных" in message.answers[-1][0]
            assert not any(button.text == "Верно, рассчитать" for button in buttons(message))
            await click(controller, state, message, "mode:competitive")
            assert not any(button.text == "Верно, рассчитать" for button in buttons(message))
            await click(controller, state, message, "confirm")
            assert not any("Рейтинг за матч:" in text for text, _ in message.answers)
    asyncio.run(scenario())


def test_expired_flow_is_removed_without_another_user_message():
    async def scenario():
        async with harness(ttl=0.02) as (controller, state, bot, _):
            message = FakeMessage(caption="hamy")
            await controller.upload(message, state, bot)
            callback = buttons(message)[-2].callback_data
            await asyncio.sleep(0.06)
            assert await state.get_data() == {}
            query = FakeQuery(message, callback)
            await controller.callback(query, state)
            assert query.answers[-1][1]["show_alert"]
    asyncio.run(scenario())


def test_new_photo_and_cancel_invalidate_previous_buttons():
    async def scenario():
        async with harness() as (controller, state, bot, _):
            first = FakeMessage(caption="hamy")
            await controller.upload(first, state, bot)
            old = next(button.callback_data for button in buttons(first) if button.text == "Верно, рассчитать")
            second = FakeMessage(caption="lov|Nagi")
            await controller.upload(second, state, bot)
            query = FakeQuery(first, old)
            await controller.callback(query, state)
            assert query.answers[-1][1]["show_alert"]
            assert (await state.get_data())["selected"]["nickname"] == "lov|Nagi"
            await controller.cancel(second, state)
            assert await state.get_data() == {}
    asyncio.run(scenario())


def test_another_user_cannot_use_a_copied_callback_nonce():
    async def scenario():
        async with harness() as (controller, state, bot, storage):
            message = FakeMessage(caption="hamy")
            await controller.upload(message, state, bot)
            victim = await state.get_data()
            other_state = FSMContext(storage=storage, key=StorageKey(bot_id=123, chat_id=92, user_id=92))
            other_message = FakeMessage(user_id=92)
            copied = FakeQuery(other_message, f"shot:{victim['nonce']}:confirm")
            await controller.callback(copied, other_state)
            assert copied.answers[-1][1]["show_alert"] and not other_message.answers
            assert await state.get_data() == victim
    asyncio.run(scenario())


def test_malformed_callbacks_never_raise_or_change_selection():
    async def scenario():
        async with harness() as (controller, state, bot, _):
            message = FakeMessage(caption="hamy")
            await controller.upload(message, state, bot)
            data = await state.get_data()
            for payload in ("shot:не-ASCII:confirm", "shot:", f"shot:{data['nonce']}:mode:duels"):
                query = FakeQuery(message, payload)
                await controller.callback(query, state)
                assert query.answers[-1][1]["show_alert"]
            assert await state.get_data() == data
    asyncio.run(scenario())


def test_oversized_document_and_untrusted_download_size_are_bounded():
    async def scenario():
        async with harness() as (controller, state, bot, _):
            message = FakeMessage(size=MAX_IMAGE_BYTES + 1)
            await controller.upload(message, state, bot)
            assert bot.downloads == 0 and "10 МБ" in message.answers[-1][0]
            document = SimpleNamespace(file_id="test-file", file_size=10, mime_type="application/pdf")
            unsupported = FakeMessage(photo=False, document=document)
            await controller.upload(unsupported, state, bot)
            assert bot.downloads == 0 and "JPEG" in unsupported.answers[-1][0]
        async with harness() as (controller, state, _, _):
            oversized = DownloadBot(b"x" * (MAX_IMAGE_BYTES + 1))
            message = FakeMessage(size=None)
            await controller.upload(message, state, oversized)
            assert "10 МБ" in message.answers[-1][0] and await state.get_data() == {}
    asyncio.run(scenario())


def test_timeout_ocr_errors_and_unsupported_mode_have_safe_messages():
    async def scenario():
        async def timeout(_):
            raise asyncio.TimeoutError()
        async with harness(analyzer=timeout) as (controller, state, bot, _):
            message = FakeMessage()
            await controller.upload(message, state, bot)
            assert "Не успел" in message.answers[-1][0]
        async def failure(_):
            raise RuntimeError("secret-internal-url-and-token")
        async with harness(analyzer=failure) as (controller, state, bot, _):
            message = FakeMessage()
            await controller.upload(message, state, bot)
            assert "secret" not in str(message.answers) and await state.get_data() == {}
        async with harness(scoreboard(mode="duels")) as (controller, state, bot, _):
            message = FakeMessage()
            await controller.upload(message, state, bot)
            assert "не поддерживается" in message.answers[-1][0] and await state.get_data() == {}
    asyncio.run(scenario())


def test_parallel_image_work_is_bounded_before_download():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def slow(_):
            nonlocal calls
            calls += 1
            if calls == 2:
                entered.set()
            await release.wait()
            return scoreboard()
        async with harness(analyzer=slow) as (controller, _, bot, storage):
            states = [FSMContext(storage=storage, key=StorageKey(bot_id=123, chat_id=i, user_id=i)) for i in (1, 2, 3)]
            messages = [FakeMessage(caption="hamy", user_id=i) for i in (1, 2, 3)]
            tasks = [asyncio.create_task(controller.upload(messages[i], states[i], bot)) for i in (0, 1)]
            await asyncio.wait_for(entered.wait(), timeout=1)
            await controller.upload(messages[2], states[2], bot)
            assert calls == 2 and bot.downloads == 2
            assert "занят" in messages[2].answers[-1][0]
            release.set()
            await asyncio.gather(*tasks)
    asyncio.run(scenario())


def test_group_photo_does_not_run_ocr():
    async def scenario():
        async with harness() as (controller, state, bot, _):
            message = FakeMessage(kind="group")
            await controller.upload(message, state, bot)
            assert "личный чат" in message.answers[-1][0] and bot.downloads == 0
    asyncio.run(scenario())


def test_real_dispatcher_keys_callback_state_by_sender_not_callback_payload():
    class Session(BaseSession):
        def __init__(self):
            super().__init__()
            self.sent = []

        async def close(self):
            pass

        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, AnswerCallbackQuery):
                return True
            assert isinstance(method, SendMessage)
            self.sent.append(method)
            return Message(message_id=len(self.sent), date=datetime.now(timezone.utc),
                           chat=Chat(id=int(method.chat_id), type="private"), text=method.text)

        async def stream_content(self, *args, **kwargs):
            if False:
                yield b""

    async def scenario():
        session = Session()
        bot = Bot("123456:TEST_ONLY_TOKEN", session=session)
        storage = MemoryStorage()
        dispatcher = Dispatcher(storage=storage, events_isolation=SimpleEventIsolation())
        async def saver(telegram_id, payload):
            assert telegram_id == 91
            return {"match": {}, "best": None, "is_new_best": False}
        dispatcher.include_router(create_screenshot_router(saver=saver))
        victim = FSMContext(storage=storage, key=StorageKey(bot_id=bot.id, chat_id=91, user_id=91))
        payload = {"phase": "confirm", "expires": asyncio.get_running_loop().time() + 600,
                   "nonce": "0123456789abcdef", "mode": "competitive", "score": [8,6],
                   "selected": {"nickname": "hamy", "kills": 22, "deaths": 7, "assists": 0},
                   "rows": [{"nickname": "hamy", "kills": 22, "deaths": 7, "assists": 0}],
                   "selected_index": 0}
        await victim.set_state("screenshot:confirm")
        await victim.set_data(payload)
        for update_id, user_id in enumerate((92, 91, 91), start=1):
            message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=user_id, type="private"))
            callback = CallbackQuery(id=str(update_id), from_user=User(id=user_id, is_bot=False, first_name="Test"),
                                     message=message, chat_instance="test", data="shot:0123456789abcdef:confirm")
            await dispatcher.feed_update(bot, Update(update_id=update_id, callback_query=callback))
        assert len(session.sent) == 1 and int(session.sent[0].chat_id) == 91
        assert "Рейтинг за матч: 1.78" in session.sent[0].text
        assert await victim.get_data() == {}
        await bot.session.close()
        await storage.close()
    asyncio.run(scenario())


def test_only_final_confirmation_persists_corrected_row_all_players_and_metadata():
    async def scenario():
        calls = []
        async def saver(telegram_id, payload):
            calls.append((telegram_id, payload))
            return {"match": {}, "best": {}, "is_new_best": True}
        async with harness(saver=saver, public_url="https://example.test/mini/") as (controller, state, bot, _):
            message = FakeMessage(caption="hamy")
            await controller.upload(message, state, bot)
            assert not calls
            await click(controller, state, message, "manual")
            correction = FakeMessage("20/8/4", photo=False)
            await controller.text(correction, state)
            assert not calls
            # A callback's message belongs to the bot; the sender is authoritative.
            correction.from_user = SimpleNamespace(id=777777)
            await click(controller, state, correction, "confirm")
            assert len(calls) == 1 and calls[0][0] == 91
            payload = calls[0][1]
            assert payload["selected_index"] == 0 and len(payload["rows"]) == 2
            assert payload["rows"][0]["kills"] == 20 and payload["rows"][0]["assists"] == 4
            assert payload["rows"][1]["nickname"] == "lov|Nagi"
            assert payload["rows"][1]["deaths"] == 11 and payload["rows"][1]["assists"] == 3
            assert payload["rows"][0]["game_score"] == 46 and payload["rows"][0]["rank"] == 1
            assert payload["rows"][1]["game_score"] == 31 and payload["rows"][1]["rank"] == 2
            assert all(row["uid"] is None for row in payload["rows"])
            assert payload["match_id"] == "2188356974" and payload["map_name"] == "Sandstone"
            assert payload["score"] == [8, 6] and payload["played_at"] is None
            assert payload["image_fingerprint"] == hashlib.sha256(b"unit-test-image").hexdigest()
            assert "Матч сохранён." in correction.answers[-1][0]
            assert "Новый личный рекорд!" in correction.answers[-1][0]
            assert any(button.web_app.url == "https://example.test/mini/" for button in buttons(correction) if button.web_app)
    asyncio.run(scenario())


def test_saving_error_keeps_rating_and_offers_retry_without_claiming_saved_or_best():
    async def scenario():
        calls = 0
        async def saver(telegram_id, payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("secret-database-location")
            return {"match": {}, "best": {}, "is_new_best": False}
        async with harness(saver=saver) as (controller, state, bot, _):
            message = FakeMessage(caption="hamy")
            await controller.upload(message, state, bot)
            await click(controller, state, message, "confirm")
            text = message.answers[-1][0]
            assert "Рейтинг за матч: 1.78" in text and "не сохранён" in text
            assert "Матч сохранён." not in text and "Новый личный рекорд" not in text
            assert "secret" not in text
            assert any(button.text == "Повторить сохранение" for button in buttons(message))
            assert (await state.get_data())["phase"] == "confirm"
            await click(controller, state, message, "confirm")
            assert calls == 2 and "Матч сохранён." in message.answers[-1][0]
            assert await state.get_data() == {}
    asyncio.run(scenario())


def test_default_bot_saver_commits_private_archive_without_canonical_history_or_primary_uid(monkeypatch, db_factory):
    from sqlalchemy import func, select
    from app import db as database
    from app import models as m
    monkeypatch.setattr(database, "SessionLocal", db_factory)

    async def scenario():
        async with harness(saver=save_confirmed_screenshot) as (controller, state, bot, _):
            message = FakeMessage(caption="hamy")
            message.from_user = SimpleNamespace(id=777777)  # The confirmation message is sent by the bot.
            await controller.upload(message, state, bot)
            with db_factory() as db:
                assert db.scalar(select(func.count()).select_from(m.ScreenshotMatch)) == 0
            await click(controller, state, message, "confirm")
            assert "Матч сохранён." in message.answers[-1][0]
            assert "Новый личный рекорд!" in message.answers[-1][0]
        with db_factory() as db:
            user = db.get(m.User, 91)
            assert user is not None and user.primary_uid is None
            assert db.get(m.User, 777777) is None
            shots = list(db.scalars(select(m.ScreenshotMatch)))
            assert len(shots) == 1 and shots[0].telegram_id == 91
            snapshot = shots[0].snapshot
            assert snapshot["rating_display"] == "1.78" and snapshot["map"] == "Sandstone"
            assert len(snapshot["players"]) == 2
            assert snapshot["players"][0]["user_confirmed"] is True
            assert snapshot["players"][1]["user_confirmed"] is False
            assert snapshot["players"][1]["game_score"] == 31 and snapshot["players"][1]["rank"] == 2
            assert db.get(m.PersonalBest, (91, "competitive")) is not None
            for model in (m.Player, m.Match, m.Participant):
                assert db.scalar(select(func.count()).select_from(model)) == 0
    asyncio.run(scenario())
