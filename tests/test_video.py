import json
import shutil
import subprocess

import pytest
import yaml
from PIL import Image

from apppreview import generate, identify, validate_images
from apppreview.config import build
from apppreview.errors import AppPreviewError
from apppreview.generate import make_slideshow, slideshow_timing
from apppreview.layouts import layout_path
from apppreview.specs import get_target
from apppreview.video import needs_prores, probe, video_extension

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

PREVIEW = "apple.iphone-6.9-preview"


@pytest.fixture(autouse=True)
def fast_encoding(monkeypatch):
    monkeypatch.setattr("apppreview.video.X264_PRESET", "ultrafast")


def make_clip(path, size=(1179, 2556), seconds=16, fps=10, audio=False):
    """A synthetic screen recording. Low fps keeps encoding fast."""
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size[0]}x{size[1]}:rate={fps}:duration={seconds}",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True)
    return path


def codes(report):
    return {i.code for i in report.all_issues}


def test_validate_raw_recording(tmp_path):
    clip = make_clip(tmp_path / "rec.mp4", seconds=12, fps=60)
    report = validate_images([clip], PREVIEW)
    assert not report.ok
    assert {"dimensions", "duration", "frame_rate", "audio"} <= codes(report)


def test_generate_preview(tmp_path):
    clip = make_clip(tmp_path / "rec.mp4", seconds=34, fps=40)
    result = generate([clip], [PREVIEW], tmp_path / "out", start=2)
    assert result.ok, result.to_dict()
    out = result.outputs[0]
    assert out.path.endswith(".mp4") and (out.width, out.height) == (886, 1920)
    info = probe(out.path)
    # x264 signals the lowest profile the stream needs; Apple accepts anything up to High.
    assert info.codec == "h264" and info.profile in ("Constrained Baseline", "Main", "High") and info.level <= 40
    assert info.fps <= 30 and abs(info.duration - 30) < 0.2
    assert info.audio and info.audio[0]["channels"] == 2 and info.audio[0]["sample_rate"] == 48000
    assert any("Trimmed" in w for w in out.warnings)
    assert any("silent" in w for w in out.warnings)


def test_short_clip_needs_extend(tmp_path):
    clip = make_clip(tmp_path / "short.mov", seconds=10, audio=True)
    with pytest.raises(AppPreviewError) as exc:
        generate([clip], [PREVIEW], tmp_path / "out")
    assert exc.value.code == "video_too_short"
    result = generate([clip], [PREVIEW], tmp_path / "out", extend=True)
    assert result.ok
    assert abs(probe(result.outputs[0].path).duration - 15) < 0.2


def test_templated_framed_preview(tmp_path):
    clip = make_clip(tmp_path / "rec.mp4")
    result = generate([clip], [PREVIEW], tmp_path / "out", template="dark", frame="phone", captions=["Hi"])
    assert result.ok
    frame = tmp_path / "frame.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "1", "-i", result.outputs[0].path, "-frames:v", "1", str(frame)], check=True
    )
    with Image.open(frame) as img:
        corner = img.convert("RGB").getpixel((3, 3))
    assert corner[2] > corner[0]  # dark navy template background, not video content


def test_ipad_letterbox_and_dry_run(tmp_path):
    clip = make_clip(tmp_path / "rec.mp4")
    result = generate([clip], ["apple.ipad-13-preview"], tmp_path / "out", dry_run=True)
    assert (result.outputs[0].width, result.outputs[0].height) == (1200, 1600)
    assert any("letterboxed" in w for w in result.outputs[0].warnings)
    assert not (tmp_path / "out").exists()


def test_mixed_media_rejected(tmp_path):
    clip = make_clip(tmp_path / "rec.mp4")
    image = tmp_path / "shot.png"
    Image.new("RGB", (1320, 2868)).save(image)
    for inputs, targets in (([clip], ["apple.iphone-6.9"]), ([image], [PREVIEW]), ([clip, image], [PREVIEW])):
        with pytest.raises(AppPreviewError) as exc:
            generate(inputs, targets, tmp_path / "out")
        assert exc.value.code == "mixed_media"


def test_slideshow(tmp_path):
    images = []
    for i, color in enumerate(("red", "green", "blue")):
        images.append(tmp_path / f"{i}.png")
        Image.new("RGB", (1320, 2868), color).save(images[-1])
    result = make_slideshow(images, [PREVIEW], tmp_path / "out", template="clean", captions=["A", "B", "C"])
    assert result.ok, result.to_dict()
    assert 15 <= probe(result.outputs[0].path).duration <= 30


def test_slideshow_timing():
    assert slideshow_timing(3, None, 0.5)[1] == 15  # short sets are stretched to the 15s minimum
    seconds, total = slideshow_timing(10, None, 0.5)
    assert total == 30
    with pytest.raises(AppPreviewError):
        slideshow_timing(2, 3, 0.5)  # 5.5s total is too short
    with pytest.raises(AppPreviewError):
        slideshow_timing(3, 0.4, 0.5)


def test_identify_video(tmp_path):
    clip = make_clip(tmp_path / "rec.mp4")
    out = generate([clip], [PREVIEW], tmp_path / "out").outputs[0].path
    matches = identify(out)
    assert PREVIEW in matches and "apple.iphone-6.5-preview" in matches
    assert not any(not m.endswith("-preview") for m in matches)


def test_prores_for_4k():
    assert needs_prores((3840, 2160)) and video_extension((3840, 2160)) == "mov"
    assert not needs_prores((1920, 1080)) and video_extension((886, 1920)) == "mp4"


def test_fastlane_preview_path():
    from pathlib import Path

    path = layout_path("fastlane", Path("out"), get_target(PREVIEW), "ja-JP", "01_demo.mp4")
    assert path == Path("out/fastlane/app_previews/ja/IPHONE_67_01_demo.mp4")


def test_config_routes_videos_to_previews(tmp_path):
    make_clip(tmp_path / "demo.mp4")
    Image.new("RGB", (1179, 2556), "white").save(tmp_path / "home.png")
    config = {
        "targets": ["apple.iphone-6.9", PREVIEW],
        "template": "clean",
        "screens": [
            {"id": "home", "source": "home.png", "caption": "Home"},
            {"id": "demo", "source": "demo.mp4", "caption": "Demo"},
        ],
    }
    path = tmp_path / "app-preview.yaml"
    path.write_text(yaml.safe_dump(config))
    result = build(path, dry_run=True)
    by_target = {o.target: o for o in result.outputs}
    assert len(result.outputs) == 2
    assert by_target["apple.iphone-6.9"].screen == "home"
    assert by_target[PREVIEW].screen == "demo" and by_target[PREVIEW].path.endswith("01_demo.mp4")
    json.dumps(result.to_dict())
