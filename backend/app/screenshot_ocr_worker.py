"""Local scoreboard OCR. This process never receives bot or database secrets."""
from __future__ import annotations

import csv
from dataclasses import asdict
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unicodedata
import warnings

from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError

from .screenshot_ocr import PlayerRow, Scoreboard

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 12_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class RecognitionError(ValueError):
    pass


def numeric_value(text, maximum=200):
    value = text.strip()
    return int(value) if re.fullmatch(r"[0-9]{1,3}", value) and int(value) <= maximum else None


def color_mask(image, team):
    red, green, blue = image.convert("RGB").split()
    if team == "ct":
        return ImageChops.multiply(
            ImageChops.subtract(blue, red).point(lambda n: 255 if n > 8 else 0),
            blue.point(lambda n: 255 if n > 100 else 0),
        )
    return ImageChops.multiply(
        ImageChops.subtract(red, blue).point(lambda n: 255 if n > 25 else 0),
        red.point(lambda n: 255 if n > 120 else 0),
    )


def text_image(crop, team, height=40):
    mask = color_mask(crop, team)
    bounds = mask.getbbox()
    if bounds is None:
        return None
    glyphs = ImageOps.invert(mask.crop(bounds))
    width = max(1, round(glyphs.width * height / glyphs.height))
    if width > 1600:
        return None
    return ImageOps.expand(glyphs.resize((width, height)), border=10, fill="white")


class Engine:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.counter = 0

    def read(self, image, *, language="eng", psm=7, numeric=False, tsv=False):
        self.counter += 1
        path = self.directory / (str(self.counter) + ".png")
        image.save(path)
        arguments = [os.environ.get("TESSERACT_CMD", "tesseract"), str(path), "stdout",
                     "-l", language, "--psm", str(psm)]
        if numeric:
            arguments += ["-c", "tessedit_char_whitelist=0123456789"]
        if tsv:
            arguments.append("tsv")
        try:
            result = subprocess.run(arguments, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise RecognitionError("Распознавание временно недоступно. Попробуйте ещё раз.") from None
        if result.returncode or len(result.stdout) > 256 * 1024:
            raise RecognitionError("Не удалось прочитать изображение. Пришлите чёткий скриншот таблицы.")
        return result.stdout.decode("utf-8", errors="replace")

    def number(self, crop, team, maximum=200):
        prepared = text_image(crop, team)
        if prepared is None:
            return None
        # A blank OCR result stays missing. Single-character zeroes can require
        # a different segmentation mode; they are never filled by default.
        for mode, whitelist in ((7, True), (13, True), (10, False)):
            value = numeric_value(self.read(prepared, psm=mode, numeric=whitelist), maximum)
            if value is not None:
                if mode == 7 and value in (0, 8):
                    alternate = numeric_value(self.read(prepared, psm=13, numeric=True), maximum)
                    if alternate is not None and alternate != value:
                        # Zero/eight are visibly ambiguous in some small fonts.
                        # Ask for correction instead of selecting a convenient digit.
                        return None
                return value
        return None


def words_from_tsv(tsv):
    words = []
    try:
        for row in csv.DictReader(StringIO(tsv), delimiter="\t"):
            text = row.get("text", "").strip()
            if text and len(words) < 1500:
                words.append({"text": text, **{key: int(row[key]) for key in ("left", "top", "width", "height")}})
    except (ValueError, TypeError, KeyError):
        raise RecognitionError("Не удалось найти заголовки таблицы. Пришлите полный скриншот результатов.") from None
    return words


def word_key(value):
    return re.sub(r"[^a-zа-я]", "", value.casefold().replace("ё", "е"))


def find_layouts(words, width, height):
    ranks = [word for word in words if word_key(word["text"]) in {"ранг", "rank"}]
    scores = [word for word in words if word_key(word["text"]) in {"счет", "score"}]
    layouts = []
    for rank in sorted(ranks, key=lambda item: item["left"]):
        rx = rank["left"] + rank["width"] / 2
        ry = rank["top"] + rank["height"] / 2
        candidates = [word for word in scores
                      if .10 * width < word["left"] + word["width"] / 2 - rx < .25 * width
                      and abs(word["top"] + word["height"] / 2 - ry) < .025 * height]
        if not candidates:
            continue
        score = min(candidates, key=lambda item: item["left"])
        step = (score["left"] + score["width"] / 2 - rx) / 4
        # This is the observed Standoff scoreboard template: У/П/С (K/A/D).
        # The anchors determine its scale and position; player rows are found
        # from actual coloured text, never from fixed row offsets.
        centers = [rx + step * index for index in (1, 2, 3)]
        headers = []
        for center in centers:
            near = [word for word in words if abs(word["left"] + word["width"] / 2 - center) < .45 * step
                    and abs(word["top"] + word["height"] / 2 - ry) < .02 * height]
            headers.append(min(near, key=lambda item: abs(item["left"] + item["width"] / 2 - center))["text"] if near else "")
        aliases = ({"у", "y", "k", "к"}, {"п", "n", "a", "а"}, {"с", "c", "d", "д", "©"})
        if sum(label.casefold() in accepted for label, accepted in zip(headers, aliases)) < 2:
            continue
        layouts.append({"centers": centers, "step": step, "header_y": ry,
                        "name_left": max(0, rx - 3.75 * step), "name_right": rx - .65 * step,
                        "team": "ct" if rx < width / 2 else "t"})
    if len(layouts) != 2 or layouts[0]["team"] == layouts[1]["team"]:
        raise RecognitionError("Нужен полный скриншот итоговой таблицы: обе команды и колонки У/П/С.")
    return layouts


def row_bands(mask, image_height):
    # Averaging each scan line is a small, bounded horizontal projection.
    projection = list(mask.resize((1, mask.height), Image.Resampling.BOX).get_flattened_data())
    active = [index for index, value in enumerate(projection) if value >= 4]
    groups = []
    for index in active:
        if not groups or index - groups[-1][-1] > max(3, image_height // 400):
            groups.append([index])
        else:
            groups[-1].append(index)
    return [(group[0], group[-1] + 1) for group in groups
            if group[0] > 1 and group[-1] < mask.height - 2
            and .008 * image_height <= group[-1] - group[0] + 1 <= .065 * image_height]


def clean_nickname(value):
    value = " ".join(value.split())
    return "".join(char for char in value if unicodedata.category(char) not in {"Cc", "Cf"})[:80].strip()


def read_scoreboard(image, engine):
    width, height = image.size
    header = image.crop((0, 0, width, round(height * .31)))
    header_words = words_from_tsv(engine.read(header, language="rus+eng", psm=6, tsv=True))
    layouts = find_layouts(header_words, width, height)
    rows = []
    problems = []
    for layout in layouts:
        centers, step, team = layout["centers"], layout["step"], layout["team"]
        top = round(layout["header_y"] + .033 * height)
        bottom = round(height * .91)
        area = image.crop((round(centers[0] - .48 * step), top, round(centers[-1] + .48 * step), bottom))
        bands = row_bands(color_mask(area, team), height)
        if len(bands) not in (2, 5):
            raise RecognitionError("Строки игроков читаются не полностью. Пришлите чёткий полный скриншот таблицы.")
        for position, (low, high) in enumerate(bands, 1):
            center_y = top + (low + high) / 2
            numeric = []
            for center_x in centers:
                radius = .48 * step
                cell = image.crop((round(center_x - radius), top + low - 3,
                                   round(center_x + radius), top + high + 3))
                numeric.append(engine.number(cell, team))
            name_crop = image.crop((round(layout["name_left"]), round(center_y - .034 * height),
                                    round(layout["name_right"]), round(center_y + .015 * height)))
            prepared = text_image(name_crop, team, height=36)
            nickname = clean_nickname(engine.read(prepared, language="eng", psm=7)) if prepared else ""
            alternate = clean_nickname(engine.read(prepared, language="rus+eng", psm=7)) if prepared else ""
            alternatives = [alternate] if alternate and alternate != nickname else []
            if not nickname and alternate:
                nickname, alternatives = alternate, []
            if not nickname:
                nickname = "Игрок " + str(len(rows) + 1)
                problems.append("Не все ники прочитаны. Сверьте свою строку по K/D/A.")
            if any(value is None for value in numeric):
                problems.append("Не все цифры прочитаны: введите отсутствующие показатели вручную.")
            kills, assists, deaths = numeric
            game_center = centers[-1] + step
            game_cell = image.crop((round(game_center - .48 * step), top + low - 3,
                                    round(game_center + .48 * step), top + high + 3))
            game_score = engine.number(game_cell, team, maximum=999)
            rows.append(PlayerRow(nickname=nickname, kills=kills, deaths=deaths, assists=assists, team=team,
                                  aliases=alternatives, game_score=game_score, rank=position))
    footer = image.crop((0, round(height * .91), width, height))
    footer_words = words_from_tsv(engine.read(footer, language="rus+eng", psm=6, tsv=True))
    footer_text = " ".join(word["text"] for word in footer_words)
    normalized = footer_text.casefold()
    if "дуэл" in normalized or "duel" in normalized:
        raise RecognitionError("Дуэли не поддерживаются. Нужен матч 5×5 или Напарники 2×2.")
    mode = ("competitive" if "соревнов" in normalized or "competitive" in normalized
            else "allies" if any(value in normalized for value in ("напарник", "allies", "wingman")) else None)
    if mode is None:
        problems.append("Режим не прочитан — выберите его перед расчётом.")
    left_footer = sorted([word for word in footer_words if word["left"] < .68 * width], key=lambda word: word["left"])
    separator = next((index for index, word in enumerate(left_footer) if word["text"] == "|"), None)
    map_name = clean_nickname(" ".join(word["text"] for word in left_footer[separator + 1:])) if separator is not None else None
    if not map_name:
        map_name = None
    header_text = " ".join(word["text"] for word in header_words)
    match = re.search(r"(?:№|#|No\.?)[ \t]*([0-9]{5,20})", header_text, re.I)
    score_words = [word for word in header_words if re.fullmatch(r"[0-9]{1,2}", word["text"])
                   and .40 * width < word["left"] < .65 * width
                   and word["top"] + word["height"] < layouts[0]["header_y"] - .02 * height]
    score_words.sort(key=lambda item: item["left"])
    score = (tuple(int(word["text"]) for word in score_words)
             if len(score_words) == 2 and abs(score_words[0]["top"] - score_words[1]["top"]) < .03 * height else None)
    return Scoreboard(rows=rows, mode=mode, match_id=match.group(1) if match else None,
                      score=score, warnings=list(dict.fromkeys(problems)), map_name=map_name)


def analyze_bytes(payload):
    if not payload or len(payload) > MAX_BYTES:
        raise RecognitionError("Пришлите изображение размером до 10 МБ.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"} or getattr(original, "n_frames", 1) != 1:
                    raise RecognitionError("Нужен обычный скриншот JPEG, PNG или WebP.")
                if original.width * original.height > MAX_PIXELS:
                    raise RecognitionError("Изображение слишком большое. Уменьшите его до 12 мегапикселей.")
                image = ImageOps.exif_transpose(original).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise RecognitionError("Не удалось открыть изображение. Пришлите скриншот JPEG, PNG или WebP.") from None
    if image.width < 700 or image.height < 300 or not 1.4 < image.width / image.height < 3.5:
        raise RecognitionError("Нужен чёткий горизонтальный скриншот полной таблицы матча.")
    if image.width < 1800:
        image = image.resize((image.width * 2, image.height * 2))
    elif image.width > 2800:
        image = image.resize((2800, round(image.height * 2800 / image.width)))
    with tempfile.TemporaryDirectory(prefix="so2-scoreboard-") as directory:
        return read_scoreboard(image, Engine(directory))


def main():
    try:
        if os.name == "posix":
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
            resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024,) * 2)
        board = analyze_bytes(sys.stdin.buffer.read(MAX_BYTES + 1))
        print(json.dumps(asdict(board), ensure_ascii=False))
        return 0
    except RecognitionError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    except Exception:
        print(json.dumps({"error": "Не удалось распознать таблицу. Пришлите более чёткий скриншот."}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
