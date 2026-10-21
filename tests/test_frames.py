import pytest
import yaml
from PIL import Image, ImageDraw

from apppreview import generate
from apppreview.compose import compose
from apppreview.config import build
from apppreview.errors import AppPreviewError
from apppreview.frames import FrameStyle, apply_frame, find_screen
from apppreview.template import load_template

RED = (255, 0, 0, 255)


def shot(size=(1179, 2556)):
    return Image.new("RGBA", size, RED)


@pytest.fixture
def frame_png(tmp_path):
    """A 700x1400 green bezel with a transparent rounded screen hole at (50, 60, 600x1280)."""
    frame = Image.new("RGBA", (700, 1400), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rounded_rectangle((0, 0, 699, 1399), 90, fill=(0, 160, 0, 255))
    hole = Image.new("L", frame.size, 0)
    ImageDraw.Draw(hole).rounded_rectangle((50, 60, 649, 1339), 60, fill=255)
    frame.putalpha(Image.composite(Image.new("L", frame.size, 0), frame.getchannel("A"), hole))
    path = tmp_path / "frame.png"
    frame.save(path)
    return path


@pytest.mark.parametrize("name,size", [("phone", (1179, 2556)), ("tablet", (2064, 2752)), ("phone", (2556, 1179))])
def test_builtin_frames_wrap_the_screenshot(name, size):
    device = apply_frame(shot(size), FrameStyle(name), [])
    assert device.width > size[0] and device.height > size[1]
    assert device.getpixel((device.width // 2, device.height // 2)) == RED  # screen visible
    assert device.getpixel((0, 0))[3] == 0  # rounded outer corner is transparent


def test_auto_picks_by_aspect():
    phone = apply_frame(shot((1179, 2556)), FrameStyle("auto"), [])
    tablet = apply_frame(shot((2064, 2752)), FrameStyle("auto"), [])
    assert phone.width - 1179 < tablet.width - 2064  # phone bezels are thinner


def test_find_screen(frame_png):
    with Image.open(frame_png) as img:
        (x, y, w, h), mask = find_screen(img.convert("RGBA"))
    assert (x, y, w, h) == (50, 60, 600, 1280)
    assert mask.size == (w, h)


def test_custom_png_frame(frame_png):
    warnings = []
    device = apply_frame(shot((600, 1280)), FrameStyle(str(frame_png)), warnings)
    assert device.size == (700, 1400)
    assert device.getpixel((350, 700)) == RED
    assert device.getpixel((20, 700)) == (0, 160, 0, 255)  # bezel drawn on top
    assert warnings == []
    apply_frame(shot((1000, 1000)), FrameStyle(str(frame_png)), warnings)
    assert warnings and "cropped" in warnings[0]


def test_manifest_frame(tmp_path, frame_png):
    manifest = tmp_path / "frame.yaml"
    manifest.write_text("image: frame.png\nscreen: [50, 60, 600, 1280]\n")
    device = apply_frame(shot((600, 1280)), FrameStyle(str(manifest)), [])
    assert device.getpixel((350, 700)) == RED


def test_frame_without_transparent_screen(tmp_path):
    path = tmp_path / "solid.png"
    Image.new("RGBA", (100, 200), (0, 0, 0, 255)).save(path)
    with pytest.raises(AppPreviewError) as exc:
        apply_frame(shot(), FrameStyle(str(path)), [])
    assert exc.value.code == "invalid_frame"


def test_unknown_frame():
    with pytest.raises(AppPreviewError) as exc:
        apply_frame(shot(), FrameStyle("watch"), [])
    assert exc.value.code == "unknown_frame"


def test_template_frame_shorthand_and_compose():
    template = load_template("dark", {"screenshot": {"frame": "phone"}})
    assert template.screenshot.frame.name == "phone"
    img, _, warnings = compose(shot(), (1320, 2868), template, "Hi")
    assert img.size == (1320, 2868) and warnings == []
    assert img.getpixel((660, 1700)) == RED[:3]


def test_generate_with_frame(tmp_path):
    src = tmp_path / "s.png"
    shot().save(src)
    result = generate([src], ["apple.iphone-6.9", "google.phone"], tmp_path / "out", frame="auto")
    assert result.ok


def test_frames_rejected_where_store_forbids(tmp_path):
    src = tmp_path / "s.png"
    shot((454, 454)).save(src)
    with pytest.raises(AppPreviewError) as exc:
        generate([src], ["google.wear-os"], tmp_path, frame="phone")
    assert exc.value.code in ("frame_not_allowed", "template_not_allowed")


def test_frame_path_in_config_is_relative_to_config(tmp_path, frame_png):
    shot((600, 1280)).save(tmp_path / "home.png")
    config = {
        "targets": ["google.phone"],
        "template": {"extends": "clean", "screenshot": {"frame": "frame.png"}},
        "screens": [{"id": "home", "source": "home.png", "caption": "Hi"}],
    }
    path = tmp_path / "app-preview.yaml"
    path.write_text(yaml.safe_dump(config))
    assert build(path).ok


def test_manifest_frame_keeps_artwork_over_a_transparent_screen(tmp_path, frame_png):
    # Apple's product bezels draw the Dynamic Island inside the transparent screen.
    with Image.open(frame_png) as img:
        frame = img.convert("RGBA")
    ImageDraw.Draw(frame).rounded_rectangle((250, 80, 450, 130), 25, fill=(0, 0, 0, 255))
    frame.save(tmp_path / "island.png")
    manifest = tmp_path / "island.yaml"
    manifest.write_text("image: island.png\nscreen: [50, 60, 600, 1280]\n")
    device = apply_frame(shot((600, 1280)), FrameStyle(str(manifest)), [])
    assert device.getpixel((350, 700)) == RED
    assert device.getpixel((350, 105)) == (0, 0, 0, 255)  # island kept
    assert device.getpixel((52, 62)) == (0, 160, 0, 255)  # rounded screen corner keeps the bezel


def test_manifest_frame_with_opaque_screen(tmp_path):
    frame = Image.new("RGBA", (700, 1400), (0, 160, 0, 255))
    frame.paste((255, 255, 255, 255), (50, 60, 650, 1340))  # placeholder screen
    frame.save(tmp_path / "solid.png")
    manifest = tmp_path / "solid.yaml"
    manifest.write_text("image: solid.png\nscreen: [50, 60, 600, 1280]\n")
    device = apply_frame(shot((600, 1280)), FrameStyle(str(manifest)), [])
    assert device.getpixel((350, 700)) == RED
    assert device.getpixel((20, 700)) == (0, 160, 0, 255)
