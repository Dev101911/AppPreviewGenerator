import io
import json
import subprocess

import pytest
from PIL import Image

from apppreview import capture
from apppreview.capture import capture_android, capture_ios, suggest_simulators
from apppreview.errors import AppPreviewError


def png_bytes(size):
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    return buf.getvalue()


class FakeRunner:
    """Records commands and answers them like simctl/adb would."""

    def __init__(
        self, booted=(("UDID-1", "iPhone 17 Pro Max"),), adb=("emulator-5554",), size=(1320, 2868), fail_on=None
    ):
        self.calls = []
        self.booted = booted
        self.adb = adb
        self.size = size
        self.fail_on = fail_on

    def __call__(self, args):
        self.calls.append(args)
        out = b""
        if self.fail_on and self.fail_on in args:
            return subprocess.CompletedProcess(args, 1, b"", b"boom")
        if args[:3] == ["xcrun", "simctl", "list"]:
            devices = [{"udid": u, "name": n, "state": "Booted"} for u, n in self.booted]
            out = json.dumps({"devices": {"iOS-26": devices}}).encode()
        elif args[:3] == ["xcrun", "simctl", "io"]:
            open(args[-1], "wb").write(png_bytes(self.size))
        elif args == ["adb", "devices"]:
            out = ("List of devices attached\n" + "".join(f"{s}\tdevice\n" for s in self.adb)).encode()
        elif "screencap" in args:
            out = png_bytes(self.size)
        return subprocess.CompletedProcess(args, 0, out, b"")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(capture.time, "sleep", lambda s: None)


def test_capture_ios(tmp_path):
    runner = FakeRunner()
    result = capture_ios(tmp_path / "shots" / "home.png", runner=runner)
    assert (result.width, result.height) == (1320, 2868)
    assert result.device == "iPhone 17 Pro Max"
    assert "apple.iphone-6.9" in result.matches
    override = next(c for c in runner.calls if "status_bar" in c)
    assert override[3:5] == ["UDID-1", "override"] and "9:41" in override


def test_capture_ios_without_status_bar(tmp_path):
    runner = FakeRunner()
    capture_ios(tmp_path / "a.png", clean_status_bar=False, runner=runner)
    assert not any("status_bar" in c for c in runner.calls)


def test_ios_device_selection(tmp_path):
    runner = FakeRunner(booted=(("U1", "iPhone 17"), ("U2", "iPad Pro 13-inch (M5)")))
    with pytest.raises(AppPreviewError) as exc:
        capture_ios(tmp_path / "a.png", runner=runner)
    assert exc.value.code == "ambiguous_device"
    result = capture_ios(tmp_path / "a.png", device="ipad pro 13-inch (m5)", runner=runner)
    assert result.device == "iPad Pro 13-inch (M5)"
    with pytest.raises(AppPreviewError) as exc:
        capture_ios(tmp_path / "a.png", device="nope", runner=runner)
    assert exc.value.code == "no_device"


def test_no_booted_simulator(tmp_path):
    with pytest.raises(AppPreviewError) as exc:
        capture_ios(tmp_path / "a.png", runner=FakeRunner(booted=()))
    assert exc.value.code == "no_device" and "simctl boot" in exc.value.message


def test_capture_android(tmp_path):
    runner = FakeRunner(size=(1080, 1920))
    result = capture_android(tmp_path / "a.png", runner=runner)
    assert result.device == "emulator-5554"
    assert "google.phone" in result.matches
    broadcasts = [c for c in runner.calls if "broadcast" in c]
    assert len(broadcasts) == len(capture.ANDROID_DEMO)
    assert any("0941" in c for c in broadcasts)
    assert all(c[:3] == ["adb", "-s", "emulator-5554"] for c in runner.calls[1:])


def test_android_errors(tmp_path):
    with pytest.raises(AppPreviewError) as exc:
        capture_android(tmp_path / "a.png", runner=FakeRunner(adb=()))
    assert exc.value.code == "no_device"
    with pytest.raises(AppPreviewError) as exc:
        capture_android(tmp_path / "a.png", runner=FakeRunner(fail_on="screencap"))
    assert exc.value.code == "capture_failed" and "boom" in exc.value.message


def test_missing_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(capture.shutil, "which", lambda name: None)
    with pytest.raises(AppPreviewError) as exc:
        capture_ios(tmp_path / "a.png")
    assert exc.value.code == "tool_not_found" and "Xcode" in exc.value.message


def test_suggest_simulators():
    suggestions = suggest_simulators()
    assert "iPhone 17 Pro Max" in suggestions["apple.iphone-6.9"]
    assert "iPhone 17 Pro" in suggestions["apple.iphone-6.3"]
    assert "apple.vision-pro" not in suggestions  # same size as Apple TV, different device
