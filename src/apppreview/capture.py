"""Capture screenshots from a booted iOS Simulator or Android emulator/device.

iOS uses `xcrun simctl` (macOS with Xcode); Android uses `adb`. Both put the status bar
into a clean, consistent state first (9:41, full battery and signal, no notifications).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path

from PIL import Image

from apppreview.errors import AppPreviewError
from apppreview.specs import load_targets
from apppreview.validate import identify


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, check=False)


def _call(runner, args: list[str]) -> bytes:
    if shutil.which(args[0]) is None and runner is run:
        hint = {
            "xcrun": "Install Xcode and its command line tools (macOS only)",
            "adb": "Install the Android SDK platform-tools and put adb on PATH",
        }.get(args[0], "")
        raise AppPreviewError("tool_not_found", f"'{args[0]}' not found. {hint}".strip())
    result = runner(args)
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode(errors="replace").strip()
        raise AppPreviewError(
            "capture_failed", f"`{' '.join(args)}` failed: {stderr or 'exit ' + str(result.returncode)}"
        )
    return result.stdout or b""


@dataclass
class CaptureResult:
    platform: str
    device: str
    path: str
    width: int
    height: int
    matches: list[str]

    def to_dict(self) -> dict:
        return {"ok": True, **self.__dict__}


def _finish(platform: str, device: str, path: Path) -> CaptureResult:
    with Image.open(path) as img:
        width, height = img.size
    return CaptureResult(
        platform, device, str(path), width, height, identify(path, "apple" if platform == "ios" else "google")
    )


def _pick(devices: list[tuple[str, str]], selector: str | None, kind: str, boot_hint: str) -> tuple[str, str]:
    """devices: (id, name) pairs. Match `selector` against id or name; default to the only one."""
    if not devices:
        raise AppPreviewError("no_device", f"No booted {kind} found. {boot_hint}")
    if selector:
        matches = [d for d in devices if selector in (d[0], d[1]) or d[1].lower() == selector.lower()]
        if not matches:
            names = ", ".join(f"{name} ({ident})" for ident, name in devices)
            raise AppPreviewError("no_device", f"No booted {kind} matches '{selector}'. Booted: {names}")
        return matches[0]
    if len(devices) > 1:
        names = ", ".join(f"{name} ({ident})" for ident, name in devices)
        raise AppPreviewError("ambiguous_device", f"Several {kind}s are booted; pass --device. Booted: {names}")
    return devices[0]


IOS_STATUS_BAR = [
    "--time", "9:41",
    "--dataNetwork", "wifi",
    "--wifiMode", "active",
    "--wifiBars", "3",
    "--cellularMode", "active",
    "--cellularBars", "4",
    "--batteryState", "charged",
    "--batteryLevel", "100",
]  # fmt: skip


def capture_ios(out: str | Path, device: str | None = None, clean_status_bar: bool = True, runner=run) -> CaptureResult:
    listing = json.loads(_call(runner, ["xcrun", "simctl", "list", "devices", "booted", "--json"]) or b"{}")
    booted = [
        (d["udid"], d["name"])
        for devices in listing.get("devices", {}).values()
        for d in devices
        if d.get("state") == "Booted"
    ]
    udid, name = _pick(booted, device, "simulator", 'Boot one with `xcrun simctl boot "iPhone 17 Pro Max"`.')
    if clean_status_bar:
        _call(runner, ["xcrun", "simctl", "status_bar", udid, "override", *IOS_STATUS_BAR])
        time.sleep(0.5)  # let the status bar redraw before the screenshot
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _call(runner, ["xcrun", "simctl", "io", udid, "screenshot", "--type=png", str(out)])
    return _finish("ios", name, out)


ANDROID_DEMO = [
    ["command", "enter"],
    ["command", "clock", "hhmm", "0941"],
    ["command", "battery", "level", "100", "plugged", "false"],
    ["command", "network", "wifi", "show", "level", "4"],
    ["command", "network", "mobile", "show", "datatype", "none", "level", "4"],
    ["command", "notifications", "visible", "false"],
]


def capture_android(out: str | Path, device: str | None = None, demo_mode: bool = True, runner=run) -> CaptureResult:
    lines = _call(runner, ["adb", "devices"]).decode().splitlines()[1:]
    devices = [(line.split("\t")[0], line.split("\t")[0]) for line in lines if line.endswith("\tdevice")]
    serial, _ = _pick(devices, device, "Android device or emulator", "Start an emulator or connect a device.")
    adb = ["adb", "-s", serial]
    if demo_mode:
        _call(runner, [*adb, "shell", "settings", "put", "global", "sysui_demo_allowed", "1"])
        for extras in ANDROID_DEMO:
            args = [*adb, "shell", "am", "broadcast", "-a", "com.android.systemui.demo"]
            for key, value in zip(extras[::2], extras[1::2], strict=True):
                args += ["-e", key, value]
            _call(runner, args)
        time.sleep(0.5)
    png = _call(runner, [*adb, "exec-out", "screencap", "-p"])
    if not png.startswith(b"\x89PNG"):
        raise AppPreviewError("capture_failed", "adb screencap did not return a PNG image")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    return _finish("android", serial, out)


@cache
def _simulators() -> list[dict]:
    return json.loads((resources.files("apppreview") / "data" / "simulators.json").read_text("utf-8"))["devices"]


def _family(name: str) -> str:
    lowered = name.lower()
    for key, family in (
        ("iphone", "iphone"),
        ("ipad", "ipad"),
        ("watch", "watch"),
        ("apple tv", "tv"),
        ("vision", "vision"),
    ):
        if key in lowered:
            return family
    return "other"


def suggest_simulators() -> dict[str, list[str]]:
    """Apple target id -> simulator names whose screenshots fit it without resizing."""
    suggestions: dict[str, list[str]] = {}
    for target in load_targets().values():
        if target.store != "apple" or target.kind != "screenshot":
            continue
        family = target.id.split(".", 1)[1].split("-")[0]  # iphone, ipad, watch, tv, vision, mac
        names = [d["name"] for d in _simulators() if tuple(d["size"]) in target.sizes and _family(d["name"]) == family]
        if names:
            suggestions[target.id] = names
    return suggestions
