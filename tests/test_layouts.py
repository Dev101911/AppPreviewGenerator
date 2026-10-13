from pathlib import Path

import pytest
import yaml
from PIL import Image

from apppreview import generate, generate_graphic
from apppreview.config import build
from apppreview.errors import AppPreviewError
from apppreview.layouts import deliver_collisions, layout_path, unsupported_reason
from apppreview.specs import get_target

OUT = Path("out")


@pytest.mark.parametrize(
    "layout,target,locale,filename,expected",
    [
        ("default", "google.phone", "de-DE", "01_a.png", "de-DE/google.phone/01_a.png"),
        ("default", "google.phone", None, "01_a.png", "google.phone/01_a.png"),
        ("fastlane", "apple.iphone-6.9", "ja-JP", "01_a.png", "fastlane/screenshots/ja/iphone-6.9_01_a.png"),
        (
            "fastlane",
            "apple.ipad-12.9",
            "en-US",
            "01_a.png",
            "fastlane/screenshots/en-US/APP_IPAD_PRO_129_ipad-12.9_01_a.png",
        ),
        (
            "fastlane",
            "google.phone",
            "he",
            "01_a.png",
            "fastlane/metadata/android/iw-IL/images/phoneScreenshots/01_a.png",
        ),
        (
            "fastlane",
            "google.feature-graphic",
            "en-US",
            "feature-graphic.jpg",
            "fastlane/metadata/android/en-US/images/featureGraphic.jpg",
        ),
        (
            "fastlane",
            "google.tablet-7",
            None,
            "01_a.png",
            "fastlane/metadata/android/en-US/images/sevenInchScreenshots/01_a.png",
        ),
        ("gpp", "google.tablet-10", "ja", "01_a.png", "play/listings/ja-JP/graphics/large-tablet-screenshots/01_a.png"),
        ("gpp", "google.icon", "en-US", "icon.png", "play/listings/en-US/graphics/icon/icon.png"),
        ("gpp", "apple.ipad-13", "zh-CN", "01_a.png", "fastlane/screenshots/zh-Hans/ipad-13_01_a.png"),
    ],
)
def test_layout_paths(layout, target, locale, filename, expected):
    assert layout_path(layout, OUT, get_target(target), locale, filename) == OUT / expected


def test_unsupported_targets():
    assert unsupported_reason("fastlane", get_target("google.chromebook"))
    assert unsupported_reason("gpp", get_target("google.xr"))
    assert unsupported_reason("fastlane", get_target("apple.icon"))
    assert unsupported_reason("default", get_target("google.chromebook")) is None
    assert unsupported_reason("gpp", get_target("google.wear-os")) is None


def test_deliver_collisions():
    assert deliver_collisions([get_target("apple.tv"), get_target("apple.vision-pro")])
    assert not deliver_collisions([get_target("apple.ipad-13"), get_target("apple.ipad-12.9")])  # marker resolves it
    assert not deliver_collisions([get_target("apple.iphone-6.9"), get_target("apple.ipad-13")])


@pytest.fixture
def project(tmp_path):
    Image.new("RGB", (1179, 2556), "white").save(tmp_path / "home.png")
    config = {
        "out": "out",
        "targets": ["apple.iphone-6.9", "google.phone", "google.chromebook"],
        "locales": ["en-US", "ja-JP"],
        "screens": [{"id": "home", "source": "home.png"}],
        "graphics": [{"target": "google.feature-graphic", "title": "App"}],
    }
    path = tmp_path / "apppreview.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def test_build_fastlane_layout(project, tmp_path):
    result = build(project, layout="fastlane")
    assert result.ok
    out = tmp_path / "out"
    assert (out / "fastlane/screenshots/ja/iphone-6.9_01_home.png").is_file()
    assert (out / "fastlane/metadata/android/ja-JP/images/phoneScreenshots/01_home.png").is_file()
    assert (out / "fastlane/metadata/android/ja-JP/images/featureGraphic.png").is_file()
    assert any("google.chromebook" in w for w in result.warnings)
    assert not any("chromebook" in o.target for o in result.outputs)


def test_build_gpp_layout_from_config(project, tmp_path):
    data = yaml.safe_load(project.read_text())
    project.write_text(yaml.safe_dump({**data, "layout": "gpp", "locales": []}))
    result = build(project)
    assert result.ok
    out = tmp_path / "out"
    assert (out / "play/listings/en-US/graphics/phone-screenshots/01_home.png").is_file()
    assert (out / "play/listings/en-US/graphics/feature-graphic/feature-graphic.png").is_file()


def test_generate_and_graphic_layouts(tmp_path):
    src = tmp_path / "s.png"
    Image.new("RGB", (1179, 2556), "white").save(src)
    result = generate([src], ["google.phone"], tmp_path / "o", layout="gpp", locale="de")
    assert result.outputs[0].path.endswith("play/listings/de-DE/graphics/phone-screenshots/01_s.png")
    with pytest.raises(AppPreviewError) as exc:
        generate([src], ["google.chromebook"], tmp_path / "o", layout="fastlane")
    assert exc.value.code == "unsupported_in_layout"
    g = generate_graphic("google.tv-banner", "App", out=tmp_path / "o", layout="fastlane")
    assert g.outputs[0].path.endswith("fastlane/metadata/android/en-US/images/tvBanner.png")
