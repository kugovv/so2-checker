from io import BytesIO
import os
from pathlib import Path
import shutil

from PIL import Image, ImageDraw, ImageFont
import pytest

from app.rating import calculate, displayed
from app.screenshot_ocr_worker import (
    Engine, RecognitionError, analyze_bytes, color_mask, find_layouts,
    numeric_value, row_bands,
)


@pytest.mark.parametrize("text,value", [("0", 0), (" 12\n", 12), ("200", 200),
                                       ("", None), ("O", None), ("-1", None),
                                       ("201", None), ("12 3", None), ("1.5", None)])
def test_numeric_cells_never_fill_missing_or_invalid_values(text, value):
    assert numeric_value(text) == value


def test_row_projection_ignores_header_noise_at_boundary():
    mask = Image.new("L", (200, 600))
    draw = ImageDraw.Draw(mask)
    draw.rectangle((10, 0, 190, 15), fill=255)
    for top in (50, 150, 250, 350, 450):
        draw.rectangle((60, top, 140, top + 22), fill=255)
    assert row_bands(mask, 1080) == [(50, 73), (150, 173), (250, 273), (350, 373), (450, 473)]


def test_blank_numeric_crop_is_missing_and_explicit_zero_requires_ocr(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    monkeypatch.setattr(engine, "read", lambda *args, **kwargs: pytest.fail("Blank pixels must not invoke OCR"))
    assert engine.number(Image.new("RGB", (50, 30), "white"), "ct") is None
    blue_text = Image.new("RGB", (50, 30), (125, 150, 200))
    answers = iter(("", "", "0"))
    monkeypatch.setattr(engine, "read", lambda *args, **kwargs: next(answers))
    assert engine.number(blue_text, "ct") == 0


def test_disagreeing_zero_eight_is_reported_as_missing(tmp_path, monkeypatch):
    engine = Engine(tmp_path)
    answers = iter(("8", "0"))
    monkeypatch.setattr(engine, "read", lambda *args, **kwargs: next(answers))
    assert engine.number(Image.new("RGB", (50, 30), (230, 175, 64)), "t") is None


def test_team_color_masks_do_not_turn_grey_background_into_statistics():
    grey = Image.new("RGB", (20, 20), (80, 80, 80))
    assert color_mask(grey, "ct").getbbox() is None
    assert color_mask(grey, "t").getbbox() is None


def test_unknown_headers_and_corrupt_or_portrait_images_are_rejected_before_ocr(monkeypatch):
    with pytest.raises(RecognitionError):
        find_layouts([], 2400, 1080)
    monkeypatch.setattr(Engine, "read", lambda *args, **kwargs: pytest.fail("Invalid images must not invoke OCR"))
    with pytest.raises(RecognitionError):
        analyze_bytes(b"not an image")
    stream = BytesIO()
    Image.new("RGB", (700, 1000)).save(stream, format="PNG")
    with pytest.raises(RecognitionError):
        analyze_bytes(stream.getvalue())


def test_real_private_example_with_local_tesseract():
    # User screenshots are kept private and never committed as test fixtures.
    sample = os.environ.get("SO2_SCOREBOARD_EXAMPLE")
    if not sample or not shutil.which(os.environ.get("TESSERACT_CMD", "tesseract")):
        pytest.skip("Private example and local Tesseract required")
    board = analyze_bytes(Path(sample).read_bytes())
    assert board.mode == "competitive" and board.match_id == "2188356974"
    assert board.map_name == "Sandstone"
    assert board.score == (8, 6)
    assert [(row.kills, row.deaths, row.assists) for row in board.rows] == [
        (22, 7, 0), (12, 11, 3), (12, 12, 6), (7, 12, 1), (4, 9, 2),
        (17, 8, 2), (11, 12, 5), (10, 13, 3), (7, 11, 3), (5, 13, 2),
    ]
    assert [row.game_score for row in board.rows] == [46, 31, 30, 15, 14, 43, 27, 23, 17, 12]
    assert [row.rank for row in board.rows] == [1, 2, 3, 4, 5] * 2
    assert displayed(calculate(board.mode, 22, 7, 0, rounds=None)["value"]) == "1.34"


def test_real_private_allies_example_with_tinted_background():
    sample = os.environ.get("SO2_ALLIES_EXAMPLE")
    if not sample or not shutil.which(os.environ.get("TESSERACT_CMD", "tesseract")):
        pytest.skip("Private allies example and local Tesseract required")
    board = analyze_bytes(Path(sample).read_bytes())
    assert board.mode == "allies" and board.match_id == "2187421850"
    assert board.map_name == "Sandstone" and board.score == (3, 8)
    assert [(r.kills, r.deaths, r.assists) for r in board.rows] == [(7, 8, 0), (1, 10, 2), (13, 3, 0), (4, 5, 2)]
    assert board.rows[2].nickname == "harny"


@pytest.mark.parametrize("mode,count,map_name", [("competitive", 5, "Sandstone"), ("allies", 2, "Rust")])
def test_ocr_on_generated_scoreboard_pixels(mode, count, map_name):
    if not os.environ.get("RUN_OCR_INTEGRATION") or not shutil.which("tesseract"):
        pytest.skip("Set RUN_OCR_INTEGRATION with local Tesseract to exercise pixels")
    image = Image.new("RGB", (2436, 1080), (25, 30, 35))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=28)
    grey = (180, 180, 180)
    draw.text((220, 35), "MATCH #987654321", font=font, fill=grey)
    draw.text((1200, 150), "8", font=font, fill=grey)
    draw.text((1290, 150), "6", font=font, fill=grey)
    expected = []
    for origin, colour in ((0, (135, 160, 215)), (1170, (230, 175, 64))):
        for x, label in ((265, "Name"), (620, "Rank"), (745, "K"), (852, "A"), (960, "D"), (1045, "Score")):
            draw.text((origin + x, 230), label, font=font, fill=grey)
        for index in range(count):
            kills, assists, deaths = 12 + index, index, 7 + index
            expected.append((kills, deaths, assists))
            y = 306 + index * 96
            draw.text((origin + 265, y - 10), "Player" + chr(65 + index), font=font, fill=colour)
            for x, value in ((745, kills), (852, assists), (960, deaths)):
                draw.text((origin + x, y), str(value), font=font, fill=colour)
    label = "Competitive" if mode == "competitive" else "Allies"
    draw.text((100, 1025), label + " | " + map_name, font=font, fill=grey)
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    board = analyze_bytes(encoded.getvalue())
    assert board.mode == mode and board.map_name == map_name
    for row, truth in zip(board.rows, expected, strict=True):
        assert row.kills == truth[0] and row.deaths == truth[1]
        assert row.assists == truth[2] or (truth[2] == 0 and row.assists is None)
    if any(row.assists is None for row in board.rows):
        assert any("вручную" in warning for warning in board.warnings)
