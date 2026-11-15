import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo
from sqlalchemy import text

from .config import get_settings
from .db import engine
from .screenshot_bot import create_screenshot_router


async def run():
    config = get_settings()
    if not config.bot_token or not config.public_url.startswith("https://"):
        raise RuntimeError("BOT_TOKEN and HTTPS PUBLIC_URL are required")
    connection = engine.connect()
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Bot polling lock requires PostgreSQL")
    if not connection.scalar(text("SELECT pg_try_advisory_lock(72910424)")):
        raise RuntimeError("Another bot process is running")
    dispatcher = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    dispatcher.include_router(create_screenshot_router(public_url=config.public_url))
    bot = Bot(config.bot_token)

    @dispatcher.message(CommandStart())
    async def start(message):
        if message.chat.type != "private":
            await message.answer("Откройте личный чат @" + config.bot_username + ", чтобы запустить приложение.")
            return
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="Открыть приложение", web_app=WebAppInfo(url=config.public_url))]])
        await message.answer("Зона 9 — свежая форма и сравнение игроков Standoff 2.\n"
                             "Соревновательный 5×5 и Напарники 2×2. Профили, матчи и рейтинг — внутри приложения.\n"
                             "Разбор одного матча: /match или скриншот с ником в подписи.", reply_markup=keyboard)

    try:
        webhook = await bot.get_webhook_info()
        if webhook.url:
            raise RuntimeError("Bot has an existing webhook; refusing competing polling")
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Открыть приложение", web_app=WebAppInfo(url=config.public_url)))
        await bot.set_my_commands([
            BotCommand(command="start", description="Открыть приложение"),
            BotCommand(command="match", description="Разобрать матч по скриншоту"),
            BotCommand(command="nickname", description="Как сменить сохранённый ник"),
            BotCommand(command="cancel", description="Отменить разбор скриншота"),
        ])
        await dispatcher.start_polling(bot, allowed_updates=["message", "callback_query"], close_bot_session=False)
    finally:
        await bot.session.close()
        connection.close()


if __name__ == "__main__":
    # Framework logs may contain bot URLs on exceptional network failures.
    logging.basicConfig(level=logging.CRITICAL)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logging.critical("Bot stopped: %s", type(exc).__name__)
        raise SystemExit(1) from None
