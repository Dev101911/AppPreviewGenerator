from importlib import resources

import pytest
import yaml
from PIL import Image

from apppreview.compose import _draw_text, _wrap, fit_text, layout_options, load_font, missing_glyphs, text_warnings
from apppreview.config import load_config, plan
from apppreview.locales import is_rtl, store_locale, store_locales
from apppreview.template import TextStyle


@pytest.mark.parametrize(
    "locale,apple,google",
    [
        ("ja-JP", "ja", "ja-JP"),
        ("ja", "ja", "ja-JP"),
        ("zh-Hans", "zh-Hans", "zh-CN"),
        ("zh-TW", "zh-Hant", "zh-TW"),
        ("he", "he", "iw-IL"),
        ("iw-IL", "he", "iw-IL"),
        ("de", "de-DE", "de-DE"),
        ("es-MX", "es-MX", "es-419"),
        ("en-us", "en-US", "en-US"),
        ("en", None, None),  # ambiguous
        ("xx-YY", None, None),
    ],
)
def test_store_locale(locale, apple, google):
    assert store_locale("apple", locale) == apple
    assert store_locale("google", locale) == google


def test_locale_lists():
    locales = store_locales()
    assert "en-US" in locales["apple"] and "zh-Hans" in locales["apple"]
    assert "iw-IL" in locales["google"] and len(locales["google"]) > len(locales["apple"])


def test_rtl():
    assert is_rtl("ar-SA") and is_rtl("he") and is_rtl("fa-IR")
    assert not is_rtl("en-US") and not is_rtl(None)
    options = layout_options("ar")
    if options:  # needs libraqm
        assert options == {"language": "ar", "direction": "rtl"}


def test_missing_glyphs_with_default_font():
    font = load_font(None, "bold", 40)
    assert missing_glyphs(font, "Hello, Grüße €") == []
    assert missing_glyphs(font, "Hi 日本") == ["日", "本"]
    block = fit_text("日本", TextStyle(), 1000, 900, 400)
    assert any("no glyphs" in w for w in text_warnings([block]))


def test_cjk_text_breaks_between_characters():
    font = load_font(None, "bold", 40)
    text = "一" * 60  # no spaces, far wider than the line
    lines = _wrap(text, font, 300, {})
    assert len(lines) > 1 and "".join(lines) == text
    assert all(font.getlength(line) <= 300 for line in lines)


def test_rtl_mirrors_left_alignment():
    style = TextStyle(align="left", color="#000000", size=0.05)
    for locale, expect_right in ((None, False), ("ar", True)):
        canvas = Image.new("RGBA", (1000, 200), (255, 255, 255, 255))
        block = fit_text("Hi", style, 1000, 1000, 200, locale)
        _draw_text(canvas, block, 0, 0, 1000)
        bbox = Image.eval(canvas.convert("L"), lambda v: 255 - v).getbbox()
        assert (bbox[0] > 500) == expect_right


def test_locale_overrides_and_warnings(tmp_path):
    Image.new("RGB", (1179, 2556), "white").save(tmp_path / "home.png")
    regular = str(resources.files("apppreview") / "data" / "fonts" / "Inter-Regular.otf")
    config = {
        "targets": ["apple.iphone-6.9", "google.phone"],
        "locales": ["en-US", "ja-JP", "xx-YY"],
        "template": "clean",
        "locale_overrides": {"ja": {"caption": {"font": regular}}},
        "screens": [{"id": "home", "source": "home.png", "caption": "Hello"}],
    }
    path = tmp_path / "app-preview.yaml"
    path.write_text(yaml.safe_dump(config))
    jobs, warnings = plan(*load_config(path))
    fonts = {job.locale: job.template.caption.font for job in jobs}
    assert fonts["ja-JP"] == regular
    assert fonts["en-US"] is None
    assert len(warnings) == 2 and all("xx-YY" in w for w in warnings)


def test_rtl_graphic_puts_icon_on_the_right():
    from apppreview.graphic import render_graphic
    from apppreview.template import load_template

    icon = Image.new("RGB", (100, 100), (52, 120, 246))
    img, _ = render_graphic((1024, 500), load_template("clean"), "Hi", icon=icon, locale="ar")
    assert img.getpixel((600, 250)) == (52, 120, 246)
