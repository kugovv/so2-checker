"""Private, temporary screenshot-rating conversations; no game-history writes."""
import asyncio
from difflib import SequenceMatcher
import hashlib
from io import BytesIO
import re
import secrets
import time
import unicodedata

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from .rating import ALLIES_PARAMETERS, PARAMETERS, WEIGHTS, calculate, displayed, rating_color

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MODES = {"competitive": "Соревновательный 5×5", "allies": "Напарники 2×2"}
STATES = ("screenshot:nickname", "screenshot:choose", "screenshot:confirm", "screenshot:manual")
COLOR_ICONS = {"red": "🔴", "white": "⚪", "green": "🟢", "gold": "🟡"}


async def analyze_screenshot(image_bytes):
    # OCR packages/engine are unnecessary for /start and other bot functions.
    from .screenshot_ocr import analyze_image
    return await analyze_image(image_bytes)


async def save_confirmed_screenshot(telegram_id, payload):
    """The sender comes from Telegram, and this write never changes primary UID."""
    def save():
        from .db import SessionLocal
        from .personal_matches import save_screenshot_match
        with SessionLocal() as db:
            try:
                result = save_screenshot_match(db, telegram_id, payload)
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise
    return await asyncio.to_thread(save)


async def load_confirmed_nickname(telegram_id):
    def load():
        from .db import SessionLocal
        from .personal_matches import get_screenshot_nickname
        with SessionLocal() as db:
            return get_screenshot_nickname(db, telegram_id)
    return await asyncio.to_thread(load)


class ImageTooLarge(ValueError):
    pass


class LimitedImageBuffer(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_IMAGE_BYTES:
            raise ImageTooLarge()
        return super().write(data)


def nickname_key(value):
    value = unicodedata.normalize("NFKC", value)
    return " ".join("".join(c for c in value if unicodedata.category(c) != "Cf").casefold().split())


def caption_nickname(caption):
    if not caption:
        return None
    value = caption.strip().splitlines()[0].strip()
    value = re.sub(r"^/(?:match|rate)(?:@\w+)?\s+", "", value, flags=re.I)
    value = re.sub(r"^(?:ник(?:нейм)?|nickname)\s*:\s*", "", value, flags=re.I).strip()
    return value if 0 < len(value) <= 80 and not value.startswith("/") else None


def matching_rows(rows, nickname):
    key = nickname_key(nickname)
    alternatives = [[nickname_key(value) for value in [row["nickname"], *row.get("aliases", [])]] for row in rows]
    exact = [index for index, keys in enumerate(alternatives) if key in keys]
    if exact:
        return exact, True
    similarities = [(max(SequenceMatcher(None, key, alternative).ratio() for alternative in keys), index)
                    for index, keys in enumerate(alternatives)]
    return [index for score, index in sorted(similarities, reverse=True) if score >= 0.45][:5], False


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def parsed_scoreboard(board):
    rows = []
    for item in list(_field(board, "rows", []))[:12]:
        nickname = _field(item, "nickname")
        if not isinstance(nickname, str) or not nickname.strip() or len(nickname) > 80:
            continue
        row = {"nickname": nickname.strip(), "team": None, "aliases": [], "game_score": None, "rank": None}
        aliases = _field(item, "aliases", [])
        if isinstance(aliases, list):
            row["aliases"] = list(dict.fromkeys(alias.strip() for alias in aliases[:3]
                                  if isinstance(alias, str) and 0 < len(alias.strip()) <= 80
                                  and alias.strip() != nickname.strip()))
        for name in ("kills", "deaths", "assists"):
            value = _field(item, name)
            row[name] = value if type(value) is int and 0 <= value <= 200 else None
        for name, minimum, maximum in (("game_score", 0, 999), ("rank", 1, 10)):
            value = _field(item, name)
            row[name] = value if type(value) is int and minimum <= value <= maximum else None
        team = _field(item, "team")
        if isinstance(team, str):
            row["team"] = team[:24]
        rows.append(row)
    mode = _field(board, "mode")
    if mode is not None and mode not in MODES:
        raise ValueError("unsupported_mode")
    warnings = _field(board, "warnings", []) or []
    match_id, score, map_name = (_field(board, key) for key in ("match_id", "score", "map_name"))
    return {"rows": rows, "mode": mode,
            "warnings": [str(value)[:180] for value in warnings[:5]],
            "match_id": match_id if isinstance(match_id, str) and re.fullmatch(r"[0-9]{1,32}", match_id) else None,
            "score": list(score) if isinstance(score, (list, tuple)) and len(score) == 2
                     and all(type(value) is int and 0 <= value <= 100 for value in score) else None,
            "map_name": map_name.strip() if isinstance(map_name, str) and 0 < len(map_name.strip()) <= 128 else None}


def statistics_text(row):
    return "/".join("Нет данных" if row.get(field) is None else str(row[field])
                    for field in ("kills", "deaths", "assists"))


def formula_text(mode):
    if mode == "allies":
        p = ALLIES_PARAMETERS
        return ("Предварительная методика v2 · Напарники 2×2\n\n"
                f"R = ((У + {p['assist_credit']} × А + {p['kill_prior']}) / (С + {p['death_prior']})) ^ {p['exponent']}.\n"
                "У — убийства, С — смерти, А — ассисты.\n\n"
                "Главный фактор — соотношение убийств и смертей. Ассисты дают осторожный положительный вклад. "
                "Добавки +1 и +2 — консервативное сглаживание малой статистики, не реальные фраги. "
                "Степень 0.60 ограничивает рост крайних результатов, верхнего потолка нет. "
                "Коэффициенты предварительные. Раунды, звание, MVP и MMR не участвуют. "
                "Это оценка дуэльной эффективности по K/D/A: темп игры и пассивность по ней определить нельзя.")
    norms = PARAMETERS[mode]["match"]
    return (f"Предварительная методика v1 · {MODES[mode]}\n\n"
            f"R = max(0; 1 + {WEIGHTS['kills']} × (У/{norms['kills']} − 1)"
            f" − {WEIGHTS['deaths']} × (С/{norms['deaths']} − 1)"
            f" + {WEIGHTS['assists']} × (А/{norms['assists']} − 1)).\n"
            "У — убийства, С — смерти, А — ассисты.\n\n"
            "Ассисты имеют отдельный положительный вес после нормирования. "
            "Нормы предварительные, без калибровки на большой выборке. "
            "Личные раунды и MMR на скриншоте не подтверждены, поэтому "
            "оценка упрощённая. Это собственный рейтинг приложения.")


class ScreenshotController:
    def __init__(self, analyzer=None, *, saver=None, nickname_loader=None, public_url=None, ttl=600, cooldown=10, clock=time.monotonic):
        self.analyzer = analyzer or analyze_screenshot
        self.saver = saver or save_confirmed_screenshot
        self.nickname_loader = nickname_loader or load_confirmed_nickname
        self.public_url = public_url if isinstance(public_url, str) and public_url.startswith("https://") else None
        self.ttl = ttl
        self.cooldown = cooldown
        self.clock = clock
        self._expiry = {}
        self._cooldowns = {}
        self._uploads = 0

    @staticmethod
    async def answer(message, text, **kwargs):
        return await message.answer(text, parse_mode=None, **kwargs)

    async def _clear(self, state):
        task = self._expiry.pop(state.key, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        await state.clear()

    def _expire(self, state, expires):
        previous = self._expiry.pop(state.key, None)
        if previous:
            previous.cancel()

        async def cleanup():
            try:
                await asyncio.sleep(max(0, expires - self.clock()))
                if (await state.get_data()).get("expires") == expires:
                    await state.clear()
            finally:
                if self._expiry.get(state.key) is asyncio.current_task():
                    self._expiry.pop(state.key, None)

        self._expiry[state.key] = asyncio.create_task(cleanup())

    async def close(self):
        tasks = list(self._expiry.values())
        self._expiry.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _save(self, state, data, phase):
        data = {**data, "phase": phase, "nonce": secrets.token_hex(8)}
        await state.set_data(data)
        await state.set_state("screenshot:" + phase)
        return data

    async def _active(self, state):
        data = await state.get_data()
        if not data or data.get("expires", 0) <= self.clock():
            await self._clear(state)
            return None
        return data

    async def start(self, message, state):
        if message.chat.type != "private":
            await self.answer(message, "Разбор скриншота доступен в личном чате с ботом.")
            return
        await self._clear(state)
        await self.answer(message, "Пришлите скриншот итоговой таблицы матча и укажите свой ник в подписи. "
                          "Можно отправить ник следующим сообщением.\n"
                          "Поддерживаются 5×5 и 2×2. Перед расчётом вы проверите K/D/A.\n"
                          "/cancel — отменить разбор.")

    async def cancel(self, message, state):
        if message.chat.type == "private":
            await self._clear(state)
            await self.answer(message, "Разбор отменён. Новый скриншот можно прислать в любое время.")

    async def nickname_help(self, message, state):
        if message.chat.type == 'private':
            await self._clear(state)
            await self.answer(message, "Чтобы сменить сохранённый ник, пришлите следующий скриншот с новым ником в подписи. Я заменю ник только после подтверждения вашей строки и сохранения матча.")

    async def upload(self, message, state, bot):
        if message.chat.type != "private":
            await self.answer(message, "Пришлите скриншот в личный чат с ботом.")
            return
        photo = getattr(message, "photo", None)
        document = getattr(message, "document", None)
        attachment = photo[-1] if photo else document
        if document and document.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            await self.answer(message, "Нужен скриншот JPEG, PNG или WebP, отправленный как фото или файл.")
            return
        if attachment is None:
            return
        if (getattr(attachment, "file_size", 0) or 0) > MAX_IMAGE_BYTES:
            await self.answer(message, "Изображение слишком большое. Максимум — 10 МБ.")
            return
        now = self.clock()
        self._cooldowns = {key: value for key, value in self._cooldowns.items() if now - value < self.cooldown}
        previous = self._cooldowns.get(state.key)
        if previous is not None and now - previous < self.cooldown:
            await self.answer(message, "Подождите несколько секунд перед следующим скриншотом.")
            return
        if (self._uploads >= 2 or (state.key not in self._expiry and len(self._expiry) >= 512)
                or len(self._cooldowns) >= 1024):
            await self.answer(message, "Сейчас разбор занят. Попробуйте прислать скриншот чуть позже.")
            return
        self._cooldowns[state.key] = now
        await self._clear(state)
        self._uploads += 1
        try:
            await self.answer(message, "Читаю таблицу матча…")
            with LimitedImageBuffer() as destination:
                await bot.download(attachment.file_id, destination=destination, timeout=20)
                payload = destination.getvalue()
                if not payload:
                    raise ValueError("empty_image")
                board = await asyncio.wait_for(self.analyzer(payload), timeout=40)
            data = parsed_scoreboard(board)
            if not data["rows"]:
                await self.answer(message, "Не удалось прочитать игроков. Пришлите чёткую итоговую таблицу целиком, "
                                  "без обрезанных столбцов с убийствами, смертями и ассистами.")
                return
            data["image_fingerprint"] = hashlib.sha256(payload).hexdigest()
            data["expires"] = self.clock() + self.ttl
            self._expire(state, data["expires"])
            nickname = caption_nickname(getattr(message, "caption", None))
            if not nickname:
                try:
                    remembered = await self.nickname_loader(message.from_user.id)
                except Exception:
                    remembered = None
                if remembered:
                    indices, exact = matching_rows(data['rows'], remembered)
                    if exact and len(indices) == 1:
                        nickname = remembered
                    else:
                        await self._save(state, data, 'nickname')
                        await self.answer(message, f"Сохранённый ник «{remembered}» не найден однозначно на снимке. Напишите ник для этого матча; после подтверждения я запомню его.")
                        return
            if nickname:
                await self._select_nickname(message, state, data, nickname)
            else:
                await self._save(state, data, "nickname")
                await self.answer(message, "Таблица прочитана. Напишите свой никнейм точно как в игре.")
        except ImageTooLarge:
            await self.answer(message, "Изображение слишком большое. Максимум — 10 МБ.")
        except asyncio.TimeoutError:
            await self.answer(message, "Не успел прочитать скриншот. Попробуйте ещё раз или отправьте более чёткое изображение.")
        except ValueError as exc:
            if str(exc) == "unsupported_mode":
                await self.answer(message, "Этот режим не поддерживается. Нужна итоговая таблица 5×5 или 2×2.")
            else:
                await self.answer(message, "Не удалось прочитать таблицу. Пришлите чёткий скриншот 5×5 или 2×2.")
        except Exception:
            # Network and OCR exception strings can include URLs or internal details.
            await self.answer(message, "Не удалось обработать скриншот. Попробуйте ещё раз чуть позже.")
        finally:
            self._uploads -= 1

    async def _select_nickname(self, message, state, data, nickname):
        data.pop("manual_after_selection", None)
        data["requested_nickname"] = nickname
        indices, exact = matching_rows(data["rows"], nickname)
        if exact and len(indices) == 1:
            data["selected_index"] = indices[0]
            data["selected"] = dict(data["rows"][indices[0]])
            await self._confirmation(message, state, data)
            return
        data = await self._save(state, data, "choose")
        buttons = []
        for index in indices:
            row = data["rows"][index]
            text = f"{row['nickname'][:30]} · {statistics_text(row)}"
            buttons.append([InlineKeyboardButton(text=text[:60],
                           callback_data=f"shot:{data['nonce']}:row:{index}")])
        buttons.append([InlineKeyboardButton(text="Ввести K/D/A вручную",
                        callback_data=f"shot:{data['nonce']}:manual")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await self.answer(message, ("Найдено несколько строк с этим ником. Выберите свою:"
                                   if exact else "Точного совпадения нет. Выберите свою строку или введите K/D/A вручную:"),
                          reply_markup=keyboard)

    async def _manual(self, message, state, data):
        data.pop('personal_rounds', None)
        index = data.get("selected_index")
        if type(index) is not int or not 0 <= index < len(data["rows"]):
            data["manual_after_selection"] = True
            data = await self._save(state, data, "choose")
            buttons = [[InlineKeyboardButton(
                text=f"{row['nickname'][:30]} · {statistics_text(row)}"[:60],
                callback_data=f"shot:{data['nonce']}:row:{index}",
            )] for index, row in enumerate(data["rows"])]
            await self.answer(message, "Выберите свою строку на скриншоте, чтобы исправление сохранилось у нужного игрока:",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
            return
        await self._save(state, data, "manual")
        await self.answer(message, "Отправьте убийства/смерти/ассисты, например 12/11/3. Затем выберите режим и подтвердите данные.")

    async def _confirmation(self, message, state, data):
        data = await self._save(state, data, "confirm")
        row = data["selected"]
        mode = data.get("mode")
        buttons = [[InlineKeyboardButton(text=("✓ " if mode == key else "") + label,
                     callback_data=f"shot:{data['nonce']}:mode:{key}")] for key, label in MODES.items()]
        if mode == "competitive":
            score = data.get('score')
            total = sum(score) if score and len(score) == 2 else 0
            if 0 < total <= 200:
                buttons.append([InlineKeyboardButton(text=f"Сыграл все {total} раундов",
                                callback_data=f"shot:{data['nonce']}:rounds:all")])
            buttons.append([InlineKeyboardButton(text="Личные раунды неизвестны",
                            callback_data=f"shot:{data['nonce']}:rounds:unknown")])
        if mode in MODES and all(type(row.get(key)) is int for key in ("kills", "deaths", "assists")):
            buttons.append([InlineKeyboardButton(text="Верно, рассчитать",
                            callback_data=f"shot:{data['nonce']}:confirm")])
        buttons.append([InlineKeyboardButton(text="Исправить K/D/A",
                        callback_data=f"shot:{data['nonce']}:manual")])
        mode_text = MODES.get(mode, "выберите ниже")
        requested = data.get("requested_nickname") or row["nickname"]
        identity = (f"Ваш ник: {requested}\nНа скриншоте распознано: {row['nickname']}"
                    if requested != row["nickname"] else f"Игрок: {row['nickname']}")
        context = []
        if data.get("map_name"):
            context.append(data["map_name"])
        if data.get("score"):
            context.append("счёт " + ":".join(str(value) for value in data["score"]))
        context_text = "\n" + " · ".join(context) if context else ""
        await self.answer(message, f"Проверьте данные\n{identity}\n"
                          f"K/D/A: {statistics_text(row)}\n"
                          f"Порядок: убийства / смерти / ассисты.\nРежим: {mode_text}{context_text}\n\n"
                          + (f"Личных раундов: {data['personal_rounds']}. Вы подтвердили полное участие."
                             if mode == "competitive" and data.get('personal_rounds') else
                             "Личные раунды неизвестны: будет упрощённая оценка. Подтвердите полное участие кнопкой, только если играли весь матч."
                             if mode == "competitive" else "Отдельная методика Напарников v2: основной фактор — соотношение K/D, ассисты повышают оценку."),
                          reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    async def text(self, message, state):
        data = await self._active(state)
        if not data:
            await self.answer(message, "Время разбора истекло. Пришлите скриншот заново.")
            return
        value = (message.text or "").strip()
        if data["phase"] == "manual":
            match = re.fullmatch(r"(\d{1,3})(?:\s*[/,;]\s*|\s+)(\d{1,3})(?:\s*[/,;]\s*|\s+)(\d{1,3})", value)
            if not match:
                await self.answer(message, "Введите три числа: убийства/смерти/ассисты. Например: 12/11/3.")
                return
            kills, deaths, assists = map(int, match.groups())
            if any(value > 200 for value in (kills, deaths, assists)):
                await self.answer(message, "Каждый показатель должен быть от 0 до 200. Порядок: убийства/смерти/ассисты.")
                return
            nickname = data.get("selected", {}).get("nickname") or data["requested_nickname"]
            data["selected"] = {"nickname": nickname, "kills": kills, "deaths": deaths, "assists": assists, "team": None}
            await self._confirmation(message, state, data)
        elif data["phase"] in {"nickname", "choose"}:
            if not value or len(value) > 80 or value.startswith("/"):
                await self.answer(message, "Напишите только свой никнейм, до 80 символов.")
                return
            await self._select_nickname(message, state, data, value)
        else:
            await self.answer(message, "Проверьте данные кнопками выше или нажмите «Исправить K/D/A».")

    async def callback(self, query, state):
        message = getattr(query, "message", None)
        if message is None or message.chat.type != "private" or not hasattr(message, "answer"):
            await query.answer("Откройте личный чат с ботом.", show_alert=True)
            return
        data = await self._active(state)
        parts = (query.data or "").split(":")
        if (not data or len(parts) < 3 or parts[0] != "shot"
                or re.fullmatch(r"[0-9a-f]{16}", parts[1]) is None
                or not secrets.compare_digest(parts[1], data.get("nonce", ""))):
            await query.answer("Эта кнопка устарела. Пришлите скриншот заново.", show_alert=True)
            return
        action = parts[2]
        valid = ((action == "row" and len(parts) == 4 and data["phase"] == "choose"
                  and re.fullmatch(r"[0-9]{1,2}", parts[3]) is not None)
                 or (action == "mode" and len(parts) == 4 and data["phase"] == "confirm" and parts[3] in MODES)
                 or (action == "rounds" and len(parts) == 4 and data["phase"] == "confirm" and parts[3] in {"all", "unknown"} and data.get('mode') == 'competitive')
                 or (action == "manual" and len(parts) == 3 and data["phase"] in {"choose", "confirm"})
                 or (action == "confirm" and len(parts) == 3 and data["phase"] == "confirm"))
        if not valid:
            await query.answer("Эта кнопка больше не актуальна.", show_alert=True)
            return
        await query.answer()
        if action == "row":
            index = int(parts[3])
            if index >= len(data["rows"]):
                return
            data["selected_index"] = index
            data["selected"] = dict(data["rows"][index])
            if data.pop("manual_after_selection", False):
                await self._manual(message, state, data)
            else:
                await self._confirmation(message, state, data)
        elif action == "mode":
            data["mode"] = parts[3]
            data.pop('personal_rounds', None)
            await self._confirmation(message, state, data)
        elif action == "rounds":
            score = data.get('score')
            total = sum(score) if score and len(score) == 2 else 0
            if parts[3] == 'all' and 0 < total <= 200:
                if data['selected'].get('deaths') is not None and data['selected']['deaths'] > total:
                    await self.answer(message, "Смертей больше числа раундов. Исправьте K/D/A или счёт не распознан верно; полное участие не подтверждено.")
                    return
                data['personal_rounds'] = total
            else:
                data.pop('personal_rounds', None)
            await self._confirmation(message, state, data)
        elif action == "manual":
            await self._manual(message, state, data)
        else:
            row, mode = data.get("selected", {}), data.get("mode")
            if mode not in MODES or any(type(row.get(key)) is not int for key in ("kills", "deaths", "assists")):
                await self._confirmation(message, state, data)
                return
            # A scoreboard's final score is never substituted for personal rounds.
            personal_rounds = data.get('personal_rounds') if mode == 'competitive' else None
            result = calculate(mode, row["kills"], row["deaths"], row["assists"], rounds=personal_rounds)
            icon = COLOR_ICONS[rating_color(result["value"])]
            requested = data.get("requested_nickname") or row["nickname"]
            identity = f"Игрок: {requested}"
            if requested != row["nickname"]:
                identity += f"\nНа скриншоте распознано: {row['nickname']}"
            text = (f"{icon} Рейтинг за матч: {displayed(result['value'])}\n"
                    f"{identity}\n{MODES[mode]} · K/D/A {statistics_text(row)}\n\n"
                    "Источник: подтверждённые данные скриншота.\n"
                    + ("Предварительная дуэльная оценка 2×2 · v2 по K/D/A, без MMR."
                       if mode == "allies" else f"Оценка по личным раундам: {personal_rounds}. Полное участие подтверждено вами. Без MMR."
                       if personal_rounds else "Предварительная оценка v1 по K/D/A, без личных раундов и MMR."))
            selected_index = data.get("selected_index")
            if type(selected_index) is not int or not 0 <= selected_index < len(data.get("rows", [])):
                await self._manual(message, state, data)
                return
            rows = [dict(player, uid=None) for player in data["rows"]]
            rows[selected_index].update({key: row[key] for key in ("kills", "deaths", "assists")})
            payload = {
                "mode": mode, "rows": rows, "selected_index": selected_index,
                "requested_nickname": requested, "match_id": data.get("match_id"),
                "map_name": data.get("map_name"), "score": data.get("score"),
                "played_at": None, "date_label": None, "image_fingerprint": data.get("image_fingerprint"),
                "personal_rounds": personal_rounds,
            }
            sender = getattr(query, "from_user", None)
            try:
                if sender is None or type(sender.id) is not int or sender.id <= 0:
                    raise ValueError("untrusted_sender")
                saved = await self.saver(sender.id, payload)
            except Exception:
                data = await self._save(state, data, "confirm")
                text += "\n\nСохранение сейчас недоступно. Рейтинг рассчитан, матч пока не сохранён."
                keyboard = [[InlineKeyboardButton(text="Повторить сохранение",
                             callback_data=f"shot:{data['nonce']}:confirm")]]
            else:
                text += "\n\nМатч сохранён."
                text += f"\nНик «{requested}» запомнен. Следующие скриншоты можно присылать без подписи. Для смены укажите новый ник в подписи."
                if saved.get("is_new_best") is True:
                    text += "\nНовый личный рекорд!"
                await self._clear(state)
                keyboard = []
            keyboard.append([InlineKeyboardButton(text="Как рассчитано", callback_data=f"shot-formula:{mode}")])
            if self.public_url:
                keyboard.append([InlineKeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=self.public_url))])
            await self.answer(message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

    async def formula(self, query):
        message = getattr(query, "message", None)
        mode = (query.data or "").removeprefix("shot-formula:")
        if mode not in MODES or message is None or message.chat.type != "private" or not hasattr(message, "answer"):
            await query.answer("Откройте личный чат с ботом.", show_alert=True)
            return
        await query.answer()
        await self.answer(message, formula_text(mode))


def create_screenshot_router(analyzer=None, *, saver=None, public_url=None):
    controller = ScreenshotController(analyzer, saver=saver, public_url=public_url)
    router = Router(name="screenshot-ratings")
    router.message.register(controller.start, Command("match", "rate"))
    router.message.register(controller.cancel, Command("cancel"))
    router.message.register(controller.nickname_help, Command("nickname"))
    router.message.register(controller.upload, F.photo | F.document)
    router.message.register(controller.text, StateFilter(*STATES), F.text)
    router.callback_query.register(controller.formula, F.data.startswith("shot-formula:"))
    router.callback_query.register(controller.callback, F.data.startswith("shot:"))

    async def shutdown(**_):
        await controller.close()

    router.shutdown.register(shutdown)
    return router
