import pytest
from PIL import Image

from apppreview.compose import _gradient_mask, compose, fit_text
from apppreview.errors import AppPreviewError
from apppreview.template import TextStyle, builtin_templates, load_template


def test_builtin_templates_load():
    templates = builtin_templates()
    assert {"clean", "dark", "sunset", "minimal"} <= set(templates)
    assert templates["minimal"].caption_position == "bottom"
    assert templates["minimal"].screenshot.shadow is None


def test_subtitle_keeps_its_own_defaults_when_partially_overridden():
    t = load_template("dark")
    assert t.subtitle.weight == "regular"
    assert t.subtitle.size < t.caption.size


def test_extends_and_overrides(tmp_path):
    path = tmp_path / "brand.yaml"
    path.write_text("extends: dark\ncaption: {color: '#FACC15'}\nbackground: {color: '#000000'}\n")
    t = load_template(str(path))
    assert t.caption.color == "#FACC15"
    assert t.caption.weight == "bold"  # inherited
    assert t.background.color == "#000000" and t.background.gradient is None
    t2 = load_template("clean", {"padding": 0.1})
    assert t2.padding == 0.1


@pytest.mark.parametrize(
    "spec",
    [
        {"background": {"color": "nope"}},
        {"background": {"color": "#fff", "image": "x.png"}},
        {"caption": {"size": 2}},
        {"unknown_key": 1},
    ],
)
def test_invalid_templates(spec):
    with pytest.raises(AppPreviewError) as exc:
        load_template(spec)
    assert exc.value.code == "invalid_template"


def test_unknown_template():
    with pytest.raises(AppPreviewError) as exc:
        load_template("does-not-exist")
    assert exc.value.code == "unknown_template"


def test_gradient_direction():
    mask = _gradient_mask((100, 200), 180)  # top -> bottom
    assert mask.getpixel((50, 0)) < 30 and mask.getpixel((50, 199)) > 225
    mask = _gradient_mask((200, 100), 90)  # left -> right
    assert mask.getpixel((0, 50)) < 30 and mask.getpixel((199, 50)) > 225


def test_fit_text_shrinks_and_truncates():
    style = TextStyle(size=0.1, min_size=0.03, max_lines=2)
    short = fit_text("Hello", style, 1000, 900, 400)
    assert short.lines == ["Hello"] and not short.truncated
    long = fit_text("word " * 200, style, 1000, 900, 400)
    assert len(long.lines) == 2 and long.truncated and long.lines[-1].endswith("…")
    assert long.font.size < 100


def test_compose_output():
    shot = Image.new("RGBA", (1179, 2556), (255, 0, 0, 255))
    img, scale, warnings = compose(shot, (1320, 2868), load_template("dark"), "Caption", "Subtitle")
    assert img.size == (1320, 2868) and img.mode == "RGB"
    assert scale < 1 and warnings == []
    assert img.getpixel((660, 1800)) == (255, 0, 0)  # screenshot in the middle
    assert img.getpixel((5, 5)) != (255, 0, 0)  # background in the corner


def test_compose_landscape_and_bottom_caption():
    shot = Image.new("RGB", (1000, 2000), "blue")
    img, _, _ = compose(shot, (2752, 2064), load_template("minimal"), "Caption at the bottom")
    assert img.size == (2752, 2064)


def test_asset_paths_relative_to_declaring_file(tmp_path):
    (tmp_path / "brand").mkdir()
    Image.new("RGB", (10, 10), "red").save(tmp_path / "brand" / "bg.png")
    (tmp_path / "brand" / "brand.yaml").write_text("background: {image: bg.png}\n")
    t = load_template("brand/brand.yaml", base_dir=tmp_path)
    assert t.background.image == str(tmp_path / "brand" / "bg.png")
    img, _, _ = compose(Image.new("RGB", (100, 200)), (300, 600), t)
    assert img.getpixel((2, 2)) == (255, 0, 0)
