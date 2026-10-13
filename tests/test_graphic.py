import json

import pytest
import yaml
from PIL import Image

from apppreview import generate, generate_graphic, validate_images
from apppreview.cli import main
from apppreview.config import build
from apppreview.errors import AppPreviewError
from apppreview.graphic import render_graphic
from apppreview.template import load_template


@pytest.fixture
def icon(tmp_path):
    path = tmp_path / "icon.png"
    Image.new("RGBA", (1024, 1024), (52, 120, 246, 255)).save(path)
    return path


@pytest.mark.parametrize("target,size", [("google.feature-graphic", (1024, 500)), ("google.tv-banner", (1280, 720))])
def test_generate_graphic(tmp_path, icon, target, size):
    result = generate_graphic(target, "My App", "Tagline", icon, "sunset", tmp_path)
    assert result.ok
    out = result.outputs[0]
    assert (out.width, out.height) == size
    with Image.open(out.path) as img:
        assert img.size == size and img.mode == "RGB"


def test_graphic_without_icon_and_explicit_file(tmp_path):
    dest = tmp_path / "fg.jpg"
    result = generate_graphic("google.feature-graphic", "My App", out=dest, fmt="jpeg")
    assert result.ok and dest.is_file()


def test_icon_is_drawn_left_of_text(icon):
    with Image.open(icon) as img:
        canvas, warnings = render_graphic((1024, 500), load_template("clean"), "My App", icon=img)
    assert warnings == []
    assert canvas.getpixel((440, 250)) == (52, 120, 246)  # icon sits left of center


def test_long_title_is_truncated_with_warning():
    _, warnings = render_graphic((1024, 500), load_template("clean"), "word " * 80)
    assert warnings


@pytest.mark.parametrize("target,code", [("google.phone", "not_a_graphic_target"), ("nope", "unknown_target")])
def test_graphic_target_errors(target, code):
    with pytest.raises(AppPreviewError) as exc:
        generate_graphic(target, "My App")
    assert exc.value.code == code


def test_icons_for_both_stores(tmp_path, icon):
    result = generate([icon], ["google.icon", "apple.icon"], tmp_path)
    assert result.ok
    with Image.open(tmp_path / "apple.icon" / "01_icon.png") as img:
        assert img.mode == "RGB"  # App Store icons must be opaque
    assert validate_images([tmp_path / "google.icon" / "01_icon.png"], "google.icon").ok


def test_config_graphics(tmp_path, icon):
    config = {
        "out": "out",
        "locales": ["en-US", "de-DE"],
        "graphics": [
            {
                "target": "google.feature-graphic",
                "title": "My App",
                "subtitle": {"en": "Plan your week", "de": "Plane deine Woche"},
                "icon": "icon.png",
                "template": {"extends": "dark"},
            },
            {"target": "google.tv-banner", "title": "My App"},
        ],
    }
    path = tmp_path / "app-preview.yaml"
    path.write_text(yaml.safe_dump(config))
    result = build(path)
    assert result.ok
    assert len(result.outputs) == 4
    assert (tmp_path / "out" / "de-DE" / "google.feature-graphic" / "feature-graphic.png").is_file()
    assert build(path, only_targets=["google.tv-banner"], dry_run=True).outputs[0].screen == "tv-banner"


def test_config_rejects_screenshot_target_as_graphic(tmp_path):
    path = tmp_path / "app-preview.yaml"
    path.write_text(yaml.safe_dump({"graphics": [{"target": "google.phone", "title": "x"}]}))
    with pytest.raises(AppPreviewError) as exc:
        build(path)
    assert exc.value.code == "not_a_graphic_target"


def test_config_needs_screens_or_graphics(tmp_path):
    path = tmp_path / "app-preview.yaml"
    path.write_text(yaml.safe_dump({"targets": ["google.phone"]}))
    with pytest.raises(AppPreviewError) as exc:
        build(path)
    assert exc.value.code == "invalid_config"


def test_graphic_cli(tmp_path, icon):
    from click.testing import CliRunner

    result = CliRunner().invoke(
        main,
        [
            "graphic",
            "-t",
            "google.feature-graphic",
            "--title",
            "My App",
            "--icon",
            str(icon),
            "-o",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True
