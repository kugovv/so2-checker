"""Bounded, isolated OCR execution; image libraries run only in the worker."""
import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_STDOUT_BYTES = 64 * 1024
MAX_STDERR_BYTES = 4 * 1024
PROCESS_TIMEOUT = 45
_POSIX = os.name == "posix"
_KILL_SIGNAL = getattr(signal, "SIGKILL", 9)


class OCRException(ValueError):
    """A short user-facing message, without logs, paths or credentials."""


@dataclass(frozen=True)
class PlayerRow:
    nickname: str
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    team: str | None = None
    aliases: list[str] = field(default_factory=list)
    game_score: int | None = None
    rank: int | None = None  # Position in the team's table, not a rank badge/MMR.

    @property
    def kda(self):
        values = self.kills, self.deaths, self.assists
        return None if any(value is None for value in values) else values


@dataclass(frozen=True)
class Scoreboard:
    rows: list[PlayerRow] = field(default_factory=list)
    mode: str | None = None
    match_id: str | None = None
    score: tuple[int, int] | None = None
    warnings: list[str] = field(default_factory=list)
    map_name: str | None = None


def _invalid():
    return OCRException("Не удалось надёжно прочитать таблицу. Пришлите более чёткий скриншот.")


def _optional_stat(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 200):
        raise _invalid()
    return value


def parse_scoreboard(value):
    """Validate only the compact worker schema; absent metrics stay absent."""
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list) or len(value["rows"]) > 10:
        raise _invalid()
    mode = value.get("mode")
    if mode is not None and (not isinstance(mode, str) or mode not in {"competitive", "allies"}):
        raise OCRException("Поддерживаются только Соревновательный 5×5 и Напарники 2×2.")
    match_id = value.get("match_id")
    if match_id is not None and (not isinstance(match_id, str) or re.fullmatch(r"[0-9]{1,32}", match_id) is None):
        raise _invalid()
    map_name = value.get("map_name")
    if map_name is not None:
        if (not isinstance(map_name, str) or not map_name.strip() or len(map_name) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in map_name)):
            raise _invalid()
        map_name = map_name.strip()
    score = value.get("score")
    if score is not None:
        if (not isinstance(score, list) or len(score) != 2
                or any(type(number) is not int or not 0 <= number <= 100 for number in score)):
            raise _invalid()
        score = tuple(score)
    warnings = value.get("warnings", [])
    if (not isinstance(warnings, list) or len(warnings) > 10
            or any(not isinstance(text, str) or len(text) > 300
                   or any(ord(char) < 32 or ord(char) == 127 for char in text) for text in warnings)):
        raise _invalid()
    rows = []
    for raw in value["rows"]:
        if not isinstance(raw, dict):
            raise _invalid()
        nickname, team = raw.get("nickname"), raw.get("team")
        if (not isinstance(nickname, str) or not nickname.strip() or len(nickname) > 80
                or any(ord(char) < 32 or ord(char) == 127 for char in nickname)
                or (team is not None and (not isinstance(team, str) or team not in {"ct", "t"}))):
            raise _invalid()
        aliases = raw.get("aliases", [])
        if (not isinstance(aliases, list) or len(aliases) > 3
                or any(not isinstance(alias, str) or not alias.strip() or len(alias) > 80
                       or any(ord(char) < 32 or ord(char) == 127 for char in alias) for alias in aliases)):
            raise _invalid()
        aliases = list(dict.fromkeys(alias.strip() for alias in aliases if alias.strip() != nickname.strip()))
        game_score, rank = raw.get("game_score"), raw.get("rank")
        if game_score is not None and (type(game_score) is not int or not 0 <= game_score <= 999):
            raise _invalid()
        if rank is not None and (type(rank) is not int or not 1 <= rank <= 10):
            raise _invalid()
        rows.append(PlayerRow(nickname.strip(), *(_optional_stat(raw.get(key))
                              for key in ("kills", "deaths", "assists")), team, aliases, game_score, rank))
    return Scoreboard(rows, mode, match_id, score, list(warnings), map_name)


def _decode_result(status, stdout):
    if not isinstance(stdout, bytes) or len(stdout) > MAX_STDOUT_BYTES:
        raise _invalid()
    try:
        value = json.loads(stdout.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        raise _invalid() from None
    if status == 2 and isinstance(value, dict):
        message = value.get("error")
        if (isinstance(message, str) and 0 < len(message) <= 240
                and all(ord(char) >= 32 and ord(char) != 127 for char in message)):
            raise OCRException(message)
    if status != 0:
        raise OCRException("Распознавание временно недоступно. Попробуйте ещё раз чуть позже.")
    return parse_scoreboard(value)


class _OutputLimit(ValueError):
    pass


async def _read_limited(stream, limit):
    chunks, total = [], 0
    while chunk := await stream.read(min(64 * 1024, limit - total + 1)):
        total += len(chunk)
        if total > limit:
            raise _OutputLimit()
        chunks.append(chunk)
    return b"".join(chunks)


def worker_environment():
    allowed = {"PATH", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SystemRoot",
               "SYSTEMROOT", "WINDIR", "TESSERACT_CMD", "TESSDATA_PREFIX"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update(OMP_THREAD_LIMIT="1", OPENBLAS_NUM_THREADS="1",
                       PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    return environment


async def _terminate_worker(process):
    # Even an exited session leader may have a surviving Tesseract child holding
    # its pipe open; kill the owned process group rather than only the leader.
    try:
        if _POSIX:
            os.killpg(process.pid, _KILL_SIGNAL)
        elif process.returncode is None:
            process.kill()
    except (ProcessLookupError, PermissionError):
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except (asyncio.TimeoutError, ProcessLookupError):
        pass


async def _run_worker(payload, *, module="app.screenshot_ocr_worker"):
    if module not in {'app.screenshot_ocr_worker','app.collector_vision_worker'}:
        raise ValueError('Unsupported internal OCR worker')
    # The parent owns cleanup even when SIGKILL prevents worker finally blocks.
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="so2-scoreboard-", dir=str(temporary_root)) as directory:
        scratch = Path(directory).resolve(strict=True)
        if scratch.parent != temporary_root or not scratch.name.startswith("so2-scoreboard-"):
            raise OCRException("Распознавание временно недоступно. Попробуйте ещё раз чуть позже.")
        return await _run_worker_in_scratch(payload, scratch, module=module)


async def _run_worker_in_scratch(payload, scratch, *, module="app.screenshot_ocr_worker"):
    process = None
    tasks = []
    try:
        environment = worker_environment()
        environment.update({name: str(scratch) for name in ("TMPDIR", "TMP", "TEMP")})
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", module,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(Path(__file__).resolve().parents[1]), env=environment,
            limit=128 * 1024, start_new_session=_POSIX, close_fds=True,
        )

        async def feed_input():
            try:
                process.stdin.write(payload)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        tasks = [asyncio.create_task(feed_input()),
                 asyncio.create_task(_read_limited(process.stdout, MAX_STDOUT_BYTES)),
                 asyncio.create_task(_read_limited(process.stderr, MAX_STDERR_BYTES)),
                 asyncio.create_task(process.wait())]
        _, stdout, _, status = await asyncio.gather(*tasks)
        return status, stdout
    except BaseException:
        if process is not None:
            await _terminate_worker(process)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


class OCRService:
    def __init__(self, *, runner=None, timeout=PROCESS_TIMEOUT):
        self.runner = runner or _run_worker
        self.timeout = timeout
        self.loop = asyncio.get_running_loop()
        self._semaphore = asyncio.Semaphore(1)
        self._admitted = 0

    async def analyze(self, payload):
        if not isinstance(payload, bytes) or not payload:
            raise OCRException("Пришлите непустой файл изображения JPEG, PNG или WebP.")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise OCRException("Изображение слишком большое. Максимум — 10 МБ.")
        if self._admitted >= 2:
            raise OCRException("Сейчас распознавание занято. Попробуйте ещё раз чуть позже.")
        self._admitted += 1
        try:
            async with asyncio.timeout(self.timeout):
                async with self._semaphore:
                    status, stdout = await self.runner(payload)
                    return _decode_result(status, stdout)
        except asyncio.TimeoutError:
            raise OCRException("Не успел прочитать скриншот. Попробуйте более чёткое изображение.") from None
        except (_OutputLimit, OSError):
            raise OCRException("Распознавание временно недоступно. Попробуйте ещё раз чуть позже.") from None
        finally:
            self._admitted -= 1


_default_service = None


async def analyze_image(image_bytes):
    global _default_service
    loop = asyncio.get_running_loop()
    if _default_service is None or _default_service.loop is not loop:
        if _default_service is not None and _default_service._admitted:
            raise OCRException("Сейчас распознавание занято. Попробуйте ещё раз чуть позже.")
        _default_service = OCRService()
    return await _default_service.analyze(image_bytes)
