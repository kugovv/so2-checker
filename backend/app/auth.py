import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl


class InvalidInitData(ValueError):
    pass


def validate_init_data(raw: str, token: str, max_age: int = 300, now: int | None = None) -> dict:
    if not token or not raw or len(raw) > 16384:
        raise InvalidInitData("Недействительная авторизация Telegram")
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
    if len({key for key, _ in pairs}) != len(pairs):
        raise InvalidInitData("Повторяющиеся поля авторизации")
    data = dict(pairs)
    supplied_hash = data.pop("hash", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", supplied_hash):
        raise InvalidInitData("Недействительная подпись Telegram")
    check = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied_hash.lower()):
        raise InvalidInitData("Недействительная подпись Telegram")
    try:
        auth_date = int(data["auth_date"])
        current = now if now is not None else int(time.time())
        if auth_date > current + 30 or current - auth_date > max_age:
            raise InvalidInitData("Авторизация истекла. Откройте приложение снова через бота.")
        user = json.loads(data["user"])
        telegram_id = user["id"]
        if not isinstance(telegram_id, int) or isinstance(telegram_id, bool) or telegram_id <= 0:
            raise InvalidInitData("Недействительный пользователь Telegram")
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        if isinstance(exc, InvalidInitData):
            raise
        raise InvalidInitData("Неполная авторизация Telegram") from None
    return {"id": telegram_id}


def session_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()
