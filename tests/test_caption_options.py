"""Caption width, font weight and badges."""

import json

import pytest
import yaml
from click.testing import CliRunner
from PIL import Image

from apppreview.cli import main
from apppreview.compose import compose, load_font, text_column
from apppreview.config import build
from apppreview.errors import AppPreviewError
from apppreview.generate import generate
from apppreview.template import TextStyle, load_template

SIZE = (1320, 2868)
BADGE = "#00FF00"


def _ink_box(img, bottom):
    """Bounding box of everything that isn't white background in rows 0..bottom."""
    return Image.eval(img.crop((0, 0, img.width, bottom)).convert("L"), lambda v: 255 if v < 215 else 0).getbbox()


def _first_row(img, color, x=SIZE[0] // 2):
    return next((y for y in range(img.height) if img.getpixel((x, y)) == color), None)


def _template(**overrides):
    return load_template({"background": {"color": "#FFFFFF"}, "screenshot": {"shadow": None}, **overrides})


def test_weights_load_and_differ():
    widths = [load_font(None, w, 60).getlength("Plan your week") for w in ("thin", "regular", 600, "bold", "black")]
    assert widths == sorted(widths) and len(set(widths)) == len(widths)


@pytest.mark.parametrize("weight", ["heavy", 50, 1000, "450x"])
def test_invalid_weight(weight):
    with pytest.raises(AppPreviewError) as exc:
        load_template("clean", {"caption": {"weight": weight}})
    assert exc.value.code == "invalid_template"


def test_numeric_weight_accepted():
    assert load_template("clean", {"caption": {"weight": 650}}).caption.weight == 650


@pytest.mark.parametrize(
    "align, expected", [("center", (330, 660)), ("left", (92.4, 660)), ("right", (1227.6 - 660, 660))]
)
def test_text_column(align, expected):
    left, width = text_column(TextStyle(max_width=0.5, align=align), 1320, 92.4)
    assert (round(left, 1), round(width)) == (round(expected[0], 1), expected[1])
    # Never wider than the padded width
    assert text_column(TextStyle(max_width=1.0), 1320, 92.4)[1] == pytest.approx(1320 - 2 * 92.4)


def test_text_column_mirrors_for_rtl():
    left, _ = text_column(TextStyle(max_width=0.5, align="left"), 1320, 100, rtl=True)
    assert left == 1320 - 100 - 660


def test_caption_width_narrows_the_text():
    shot = Image.new("RGB", (1179, 2556), "blue")
    text = "A long caption that wraps over several lines of text"
    wide, _, _ = compose(shot, SIZE, _template(), text)
    narrow, _, _ = compose(shot, SIZE, _template(caption={"max_width": 0.5}), text)
    wide_box = _ink_box(wide, _first_row(wide, (0, 0, 255)))
    narrow_box = _ink_box(narrow, _first_row(narrow, (0, 0, 255)))
    assert narrow_box[2] - narrow_box[0] <= 0.5 * SIZE[0]
    assert narrow_box[2] - narrow_box[0] < wide_box[2] - wide_box[0]


@pytest.mark.parametrize("position", ["above", "below"])
def test_badge_position(position):
    shot = Image.new("RGB", (1179, 2556), "blue")
    template = _template(badge={"background": BADGE, "position": position})
    img, _, warnings = compose(shot, SIZE, template, "Caption", "Subtitle", badge="Pro")
    assert warnings == []
    badge_top = _first_row(img, (0, 255, 0))
    text_top = _ink_box(img, _first_row(img, (0, 0, 255)))[1]
    assert badge_top is not None, "badge not drawn on the center line"
    if position == "above":
        assert badge_top - text_top < 5  # the badge is the first thing drawn
    else:
        assert text_top < badge_top - 100


def test_badge_alone_moves_the_screenshot_down():
    shot = Image.new("RGB", (1179, 2556), "blue")
    plain, _, _ = compose(shot, SIZE, _template())
    badged, _, _ = compose(shot, SIZE, _template(badge={"background": BADGE}), badge="Pro")
    assert _first_row(badged, (0, 0, 255)) > _first_row(plain, (0, 0, 255))


def test_badge_follows_left_alignment():
    shot = Image.new("RGB", (1179, 2556), "blue")
    template = _template(caption={"align": "left"}, badge={"background": BADGE})
    img, _, _ = compose(shot, SIZE, template, "Caption", badge="Pro")
    pad = round(template.padding * SIZE[0])
    row = _first_row(img, (0, 255, 0), x=pad + 40)
    assert row is not None and img.getpixel((SIZE[0] // 2 + 200, row)) != (0, 255, 0)


def test_generate_with_badges_width_and_weight(make_image, tmp_path):
    result = generate(
        [make_image("a.png"), make_image("b.png")],
        ["apple.iphone-6.9"],
        tmp_path / "out",
        captions=["One", "Two"],
        badges=["Pro", "Plus"],
        caption_width=0.6,
        caption_weight="semibold",
    )
    assert result.ok and all(o.warnings == [] for o in result.outputs)


def test_badge_count_mismatch(make_image, tmp_path):
    with pytest.raises(AppPreviewError) as exc:
        generate([make_image()], ["apple.iphone-6.9"], tmp_path, badges=["a", "b"])
    assert exc.value.code == "text_count_mismatch"


def test_badges_imply_a_template(make_image, tmp_path):
    result = generate([make_image()], ["apple.iphone-6.9"], tmp_path / "out", badges=["Pro"], dry_run=True)
    assert result.outputs[0].scale is None  # templated outputs report no plain-resize scale


def test_cli_options(make_image, tmp_path):
    args = ["generate", str(make_image()), "-t", "apple.iphone-6.9", "-o", str(tmp_path / "out"), "--caption", "Hi"]
    args += ["--badge", "Pro", "--caption-width", "0.7", "--caption-weight", "600", "--json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True
    bad = CliRunner().invoke(main, [*args[:-1], "--caption-weight", "heavy"])
    assert bad.exit_code == 2 and "not a weight" in bad.output


def test_config_badges(tmp_path):
    Image.new("RGB", (1179, 2556), "white").save(tmp_path / "home.png")
    config = {
        "targets": ["apple.iphone-6.9"],
        "locales": ["en-US", "de-DE"],
        "template": {"extends": "clean", "caption": {"max_width": 0.7, "weight": "medium"}},
        "screens": [
            {"id": "home", "source": "home.png", "caption": "Hello", "badge": {"default": "Pro", "de": "Profi"}}
        ],
    }
    (tmp_path / "app-preview.yaml").write_text(yaml.safe_dump(config))
    result = build(tmp_path / "app-preview.yaml")
    assert result.ok and len(result.outputs) == 2


def test_config_badge_missing_locale(tmp_path):
    Image.new("RGB", (1179, 2556), "white").save(tmp_path / "home.png")
    config = {
        "targets": ["apple.iphone-6.9"],
        "locales": ["en-US", "de-DE"],
        "screens": [{"id": "home", "source": "home.png", "badge": {"en": "Pro"}}],
    }
    (tmp_path / "app-preview.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises(AppPreviewError) as exc:
        build(tmp_path / "app-preview.yaml", dry_run=True)
    assert exc.value.code == "missing_text"
