"""MCP server: the app-preview CLI as tools for AI agents.

Run `app-preview mcp` (stdio). Needs the optional extra: `pip install "app-preview[mcp]"`.
Every tool returns the same JSON documents as the CLI's --json output. Errors come back as
{"ok": false, "error": {"code", "message"}} instead of raising, so agents can act on the code.
Relative paths are resolved against the server's working directory.
"""

from __future__ import annotations

from functools import wraps
from typing import Literal

from apppreview import __version__
from apppreview.errors import AppPreviewError

INSTRUCTIONS = """\
app-preview makes App Store and Google Play screenshots, graphics and app preview videos that
pass store validation. Typical flow:
1. list_targets to pick target IDs (e.g. apple.iphone-6.9, apple.ipad-13, google.phone).
2. validate existing files, or generate compliant copies (optionally with a template, captions
   and a device frame). For a full listing across locales, write app-preview.yaml (see
   config_schema) and call build with dry_run=true first.
3. Check `ok`, `outputs[].warnings` and `validation[]` in every result.
Errors return {"ok": false, "error": {"code", "message"}}; most validation errors are fixed by
calling generate for the same target. Relative paths resolve against the server's working directory.
"""


def _safe(fn):
    """Return AppPreviewErrors as data so the agent sees the stable error code."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AppPreviewError as exc:
            return {"ok": False, "error": exc.to_dict()}

    return wrapper


# --- Tool implementations (plain functions, also used by tests) -----------------------------


@_safe
def list_targets(store: Literal["apple", "google"] | None = None, kind: str | None = None) -> dict:
    """List store targets. kind is screenshot, graphic, icon or video. Returns compact entries;
    call get_target for the full spec."""
    from apppreview.specs import load_targets

    targets = [
        {
            "id": t.id,
            "name": t.name,
            "store": t.store,
            "kind": t.kind,
            "sizes": [list(s) for s in (t.sizes or t.recommended_sizes)],
            "rule_based": bool(t.constraints and not t.sizes),
            "count": {"min": t.count_min, "max": t.count_max},
            "required": t.required,
        }
        for t in load_targets().values()
        if (store is None or t.store == store) and (kind is None or t.kind == kind)
    ]
    return {"ok": True, "targets": targets}


@_safe
def get_target(target_id: str) -> dict:
    """Full spec for one target: sizes or size rules, formats, transparency, limits, notes, source."""
    from apppreview.specs import get_target as _get

    return {"ok": True, "target": _get(target_id).to_dict()}


@_safe
def validate(paths: list[str], target: str) -> dict:
    """Check images or videos against a target. Every problem is reported with a stable code."""
    from apppreview.validate import validate_images

    return validate_images(paths, target).to_dict()


@_safe
def identify(paths: list[str], store: Literal["apple", "google"] | None = None) -> dict:
    """Which targets each file already satisfies without changes."""
    from apppreview.validate import identify as _identify

    return {"ok": True, "matches": {p: _identify(p, store) for p in paths}}


@_safe
def generate(
    inputs: list[str],
    targets: list[str],
    out_dir: str = "app-preview-out",
    mode: Literal["fit", "fill", "stretch"] = "fit",
    background: str = "#ffffff",
    format: Literal["png", "jpeg"] | None = None,
    size: str | None = None,
    template: str | None = None,
    captions: list[str] | None = None,
    subtitles: list[str] | None = None,
    frame: str | None = None,
    locale: str | None = None,
    layout: Literal["default", "fastlane", "gpp"] = "default",
    start: float = 0.0,
    extend: bool = False,
    dry_run: bool = False,
) -> dict:
    """Make compliant copies of screenshots (or, for *-preview targets, recordings) for each target.
    template/captions/frame give styled marketing screenshots; captions/subtitles are one per input."""
    from apppreview.generate import generate as _generate

    return _generate(
        inputs, targets, out_dir, mode, background, format, size, dry_run,
        template=template, captions=captions, subtitles=subtitles, locale=locale,
        frame=frame, layout=layout, start=start, extend=extend,
    ).to_dict()  # fmt: skip


@_safe
def build(
    config: str = "app-preview.yaml",
    targets: list[str] | None = None,
    locales: list[str] | None = None,
    layout: Literal["default", "fastlane", "gpp"] | None = None,
    dry_run: bool = False,
) -> dict:
    """Render everything in an app-preview.yaml (screens x targets x locales, plus graphics), then
    validate. Use dry_run=true first: it checks the config and lists all outputs."""
    from apppreview.config import build as _build

    return _build(config, targets or (), locales or (), dry_run, layout).to_dict()


@_safe
def graphic(
    target: str,
    title: str,
    subtitle: str | None = None,
    icon: str | None = None,
    template: str = "clean",
    out: str = "app-preview-out",
    locale: str | None = None,
    layout: Literal["default", "fastlane", "gpp"] = "default",
    dry_run: bool = False,
) -> dict:
    """Render a Google Play feature graphic or TV banner from a title, tagline and app icon."""
    from apppreview.graphic import generate_graphic

    return generate_graphic(target, title, subtitle, icon, template, out, None, dry_run, locale, layout).to_dict()


@_safe
def slideshow(
    images: list[str],
    targets: list[str],
    out_dir: str = "app-preview-out",
    seconds: float | None = None,
    transition: float = 0.5,
    template: str | None = None,
    captions: list[str] | None = None,
    frame: str | None = None,
    locale: str | None = None,
    layout: Literal["default", "fastlane", "gpp"] = "default",
    dry_run: bool = False,
) -> dict:
    """Turn screenshots into an App Store preview video (*-preview targets). Needs ffmpeg."""
    from apppreview.generate import make_slideshow

    return make_slideshow(
        images, targets, out_dir, seconds=seconds, transition=transition, template=template,
        captions=captions, frame=frame, locale=locale, layout=layout, dry_run=dry_run,
    ).to_dict()  # fmt: skip


@_safe
def list_templates() -> dict:
    """Built-in templates with every setting; pass a name as `template`, or extend one."""
    from apppreview.frames import BUILTIN_FRAMES
    from apppreview.template import builtin_templates

    return {
        "ok": True,
        "templates": [t.model_dump(by_alias=True) for t in builtin_templates().values()],
        "frames": BUILTIN_FRAMES,
    }


@_safe
def schema(kind: Literal["config", "template"]) -> dict:
    """JSON Schema for app-preview.yaml (config) or for template files (template)."""
    from apppreview.config import config_schema, template_schema

    return {"ok": True, "schema": config_schema() if kind == "config" else template_schema()}


@_safe
def init_config(path: str = "app-preview.yaml", force: bool = False) -> dict:
    """Write a commented starter app-preview.yaml. Refuses to overwrite unless force=true."""
    from pathlib import Path

    from apppreview.config import STARTER_CONFIG

    target = Path(path)
    if target.exists() and not force:
        raise AppPreviewError("file_exists", f"{target} already exists; pass force=true to overwrite")
    target.write_text(STARTER_CONFIG, "utf-8")
    return {"ok": True, "path": str(target)}


@_safe
def locales(code: str | None = None, store: Literal["apple", "google"] | None = None) -> dict:
    """Map a locale (e.g. ja-JP, he) to each store's code, or list store locale codes."""
    from apppreview.locales import is_rtl, store_locale, store_locales

    stores = [store] if store else ["apple", "google"]
    if code:
        return {"ok": True, "locale": code, "stores": {s: store_locale(s, code) for s in stores}, "rtl": is_rtl(code)}
    return {"ok": True, "locales": {s: store_locales()[s] for s in stores}}


@_safe
def capture(platform: Literal["ios", "android"], out: str, device: str | None = None) -> dict:
    """Screenshot a booted iOS Simulator (macOS + Xcode) or Android emulator/device (adb), with a
    clean status bar. Returns the targets the capture already fits."""
    from apppreview.capture import capture_android, capture_ios

    result = capture_ios(out, device) if platform == "ios" else capture_android(out, device)
    return result.to_dict()


READ_ONLY = [list_targets, get_target, validate, identify, list_templates, schema, locales]
WRITES = [generate, build, graphic, slideshow, init_config, capture]


def create_server():
    try:
        from mcp.server.mcpserver import MCPServer  # mcp >= 2
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as MCPServer  # mcp 1.x
        except ImportError:
            raise AppPreviewError(
                "tool_not_found", 'The MCP server needs the "mcp" extra: pip install "app-preview[mcp]"'
            ) from None
    try:
        from mcp.types import ToolAnnotations
    except ImportError:  # very old SDKs
        ToolAnnotations = None

    try:
        server = MCPServer("app-preview", instructions=INSTRUCTIONS, version=__version__)
    except TypeError:  # mcp 1.x has no version parameter
        server = MCPServer("app-preview", instructions=INSTRUCTIONS)
    for fn in READ_ONLY + WRITES:
        annotations = None
        if ToolAnnotations is not None:
            annotations = ToolAnnotations(readOnlyHint=fn in READ_ONLY, destructiveHint=False, openWorldHint=False)
        server.tool(name=fn.__name__, annotations=annotations)(fn)
    return server


def main() -> None:
    create_server().run("stdio")


__all__ = ["create_server", "main"]
