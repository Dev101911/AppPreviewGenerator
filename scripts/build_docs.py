"""Build docs/index.html from docs/_template.html plus reference sections generated from the code.

    python scripts/build_docs.py          # write docs/index.html
    python scripts/build_docs.py --check  # exit 1 if docs/index.html is out of date

Generated sections (command options, targets, config and template fields, error codes, MCP
tools) come straight from the CLI, the spec data and the pydantic models, so they can't drift.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

import click

from apppreview import __version__
from apppreview.cli import main as cli
from apppreview.config import Config
from apppreview.frames import BUILTIN_FRAMES
from apppreview.mcp_server import READ_ONLY, WRITES
from apppreview.specs import load_targets
from apppreview.template import Template, builtin_templates

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "docs" / "_template.html"
OUTPUT = ROOT / "docs" / "index.html"

# Every code the tool can report. tests/test_docs.py checks this matches the source.
USAGE_CODES = {
    "ambiguous_device": "Several simulators or devices are running; pass --device.",
    "background_unreadable": "A template's background image can't be read.",
    "capture_failed": "xcrun simctl or adb returned an error; the message includes its output.",
    "config_not_found": "No app-preview.yaml at the given path. Run `app-preview init`.",
    "ffmpeg_failed": "ffmpeg returned an error while converting a video.",
    "file_exists": "init would overwrite an existing file; pass --force.",
    "font_not_found": "A template font path or name can't be loaded.",
    "frame_not_allowed": "The target forbids device frames (Wear OS, Automotive, app icons).",
    "frame_not_found": "A custom frame image can't be read.",
    "input_not_found": "Source files are missing. `build` lists every missing file at once.",
    "input_unreadable": "A source file exists but isn't a readable image.",
    "invalid_color": "A color isn't a name, #RRGGBB, #RRGGBBAA or `auto`.",
    "invalid_config": "app-preview.yaml doesn't match the schema; the message lists each field.",
    "invalid_format": "The target doesn't accept the requested --format.",
    "invalid_frame": "A frame image has no enclosed transparent screen, or its manifest is malformed.",
    "invalid_layout": "Layout isn't default, fastlane or gpp.",
    "invalid_mode": "Mode isn't fit, fill or stretch.",
    "invalid_option": "Slideshow timing can't fit 15–30 s, or another option is out of range.",
    "invalid_size": "--size isn't WIDTHxHEIGHT or isn't accepted by every target.",
    "invalid_start": "--start is past the end of the video.",
    "invalid_template": "A template doesn't match the schema; the message lists each field.",
    "missing_source": "No screen in the config has a source of the right kind for a target.",
    "missing_text": "A caption or title has no text for a locale and no `default`.",
    "mixed_media": "Videos go to *-preview targets and images to the others; run them separately.",
    "no_device": "No booted simulator or connected device (or none matches --device).",
    "no_inputs": "No input files were given.",
    "no_output_size": "The target has no size to generate; pass --size.",
    "no_targets": "No --target was given.",
    "not_a_graphic_target": "`graphic` only works with graphic targets (feature graphic, TV banner).",
    "not_a_video_target": "`slideshow` only works with *-preview targets.",
    "nothing_to_build": "The --target/--locale filters match nothing in the config.",
    "template_not_allowed": "The target forbids added text and backgrounds (Wear OS, Automotive, icons).",
    "template_not_found": "A template file path doesn't exist.",
    "text_count_mismatch": "Give exactly one --caption (and --subtitle) per input image.",
    "too_many_images": "More inputs than the target allows (e.g. 10 for Apple, 8 for Google).",
    "tool_not_found": "A required tool is missing: ffmpeg, xcrun, adb, or the MCP extra.",
    "unknown_frame": "Not a built-in frame name or a .png/.yaml path.",
    "unknown_target": "Not a target ID; see `app-preview targets`.",
    "unknown_template": "Not a built-in template name or a .yaml/.json path.",
    "unreadable": "ffprobe can't read the video.",
    "unsupported_in_layout": "The upload tool for this layout has no folder for the target.",
    "video_too_short": "The clip is under 15 s; record longer or pass --extend.",
}

ISSUE_CODES = {
    "alpha": ("error", "Has an alpha channel where transparency isn't allowed."),
    "audio": ("error", "Video audio is missing, not stereo, or uses the wrong codec or sample rate."),
    "bitrate": ("warning", "Video bit rate is well above Apple's 10–12 Mbps target."),
    "color_mode": ("error / warning", "Not RGB (error), or an 8-bit palette image (warning)."),
    "count": ("error / warning", "More images than allowed (error) or fewer than required (warning)."),
    "dimensions": ("error", "Pixel size, aspect ratio or min/max side isn't accepted."),
    "duration": ("error", "Video is shorter than 15 s or longer than 30 s."),
    "extension_mismatch": ("warning", "File content and extension disagree (e.g. a JPEG named .png)."),
    "file_size": ("error", "Over the store's byte limit."),
    "format": ("error", "Not an accepted format or container for the target."),
    "frame_rate": ("error", "Video is over 30 fps."),
    "mixed_orientation": ("warning", "The set mixes portrait and landscape."),
    "mixed_sizes": ("error", "Apple Watch screenshots must all be one size."),
    "not_found": ("error", "File doesn't exist."),
    "recommended_aspect": ("warning", "Not 9:16 or 16:9, which Google Play needs for promotion."),
    "recommended_count": ("warning", "Fewer than 4 screenshots, which Google Play recommends for promotion."),
    "recommended_size": ("warning", "Shortest side under 1080 px, which Google Play needs for promotion."),
    "unreadable": ("error", "Not a readable image or video."),
    "video_codec": ("error", "Video codec isn't H.264 or ProRes 422 HQ."),
    "video_profile": ("error", "H.264 above High@4.0, or a ProRes profile other than 422 HQ."),
}

EXAMPLES = {
    "targets": ["app-preview targets --store apple", "app-preview targets apple.iphone-6.9 --json"],
    "validate": [
        "app-preview validate shots/*.png -t apple.iphone-6.9",
        "app-preview validate demo.mov -t apple.iphone-6.9-preview --json",
    ],
    "generate": [
        "app-preview generate raw/*.png -t apple.iphone-6.9 -t apple.ipad-13 -t google.phone -o out",
        'app-preview generate shot.png -t apple.iphone-6.9 --template dark --frame phone --caption "Plan your week"',
        "app-preview generate demo.mov -t apple.iphone-6.9-preview --start 3",
    ],
    "identify": ["app-preview identify shot.png --store google"],
    "build": [
        "app-preview build",
        "app-preview build -t apple.iphone-6.9 -l de-DE --dry-run --json",
        "app-preview build --layout fastlane",
    ],
    "init": ["app-preview init", "app-preview init marketing/app-preview.yaml"],
    "graphic": [
        'app-preview graphic -t google.feature-graphic --title "Weekly Planner" '
        '--subtitle "Plan in seconds" --icon icon.png'
    ],
    "slideshow": ["app-preview slideshow shots/*.png -t apple.iphone-6.9-preview --template clean --frame phone"],
    "templates": ["app-preview templates", "app-preview templates sunset"],
    "frames": ["app-preview frames --json"],
    "locales": ["app-preview locales ja-JP", "app-preview locales --store google"],
    "schema": ["app-preview schema config > app-preview.schema.json"],
    "capture ios": ['app-preview capture ios -o shots/en-US/home.png --device "iPhone 17 Pro Max"'],
    "capture android": ["app-preview capture android -o shots/en-US/home.png"],
    "capture simulators": ["app-preview capture simulators"],
    "mcp": ["claude mcp add app-preview -- app-preview mcp"],
}

e = html.escape


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def code(text: str) -> str:
    return f"<code>{e(text)}</code>"


def inline_md(text: str) -> str:
    """Escape text and turn `backticks` into <code>."""
    return re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", e(text))


def table(headers: list[str], rows: list[list[str]], cls: str = "") -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return (
        f'<div class="table-wrap"><table class="{cls}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def pre(lines: list[str], lang: str = "bash") -> str:
    return f'<pre><code class="lang-{lang}">{e(chr(10).join(lines))}</code></pre>'


# --- Commands -------------------------------------------------------------------------------


def _commands():
    def walk(cmd, path):
        if isinstance(cmd, click.Group):
            for name in sorted(cmd.commands):
                yield from walk(cmd.commands[name], [*path, name])
        else:
            yield " ".join(path), cmd

    yield from walk(cli, [])


def _type(param) -> str:
    if isinstance(param.type, click.Choice):
        return " | ".join(code(c) for c in param.type.choices)
    if getattr(param, "is_flag", False):
        return "flag"
    name = param.type.name.upper()
    return {"TEXT": "text", "FLOAT": "number", "PATH": "path", "INTEGER": "integer"}.get(name, name.lower())


def _default(param) -> str:
    default = param.default
    if getattr(param, "is_flag", False) or default in (None, (), [], False) or str(default).startswith("Sentinel"):
        return ""
    return code(str(default))


def commands_html() -> str:
    parts = []
    for name, cmd in _commands():
        ctx = click.Context(cmd, info_name=f"app-preview {name}")
        usage = cmd.get_usage(ctx).replace("Usage: ", "")
        help_text = (cmd.help or "").strip()
        paragraphs = "".join(f"<p>{inline_md(' '.join(p.split()))}</p>" for p in help_text.split("\n\n") if p.strip())
        args = [p for p in cmd.params if isinstance(p, click.Argument)]
        opts = [p for p in cmd.params if isinstance(p, click.Option)]
        rows = []
        for p in args:
            rows.append(
                [code(p.human_readable_name), "argument" + (" (repeatable)" if p.nargs == -1 else ""), _default(p), ""]
            )
        for p in opts:
            names = ", ".join(code(o) for o in p.opts)
            if p.multiple:
                names += ' <span class="tag">repeatable</span>'
            rows.append([names, _type(p), _default(p), inline_md(p.help or "")])
        examples = EXAMPLES.get(name, [])
        parts.append(
            f'<section class="command" id="cmd-{slug(name)}"><h3>{code("app-preview " + name)}</h3>'
            f"{paragraphs}{pre([usage])}"
            + (table(["Option", "Type", "Default", "Description"], rows, "options") if rows else "")
            + (f'<p class="label">Examples</p>{pre(examples)}' if examples else "")
            + "</section>"
        )
    return "\n".join(parts)


def commands_nav() -> str:
    return "".join(f'<li><a href="#cmd-{slug(n)}">{e(n)}</a></li>' for n, _ in _commands())


# --- Targets --------------------------------------------------------------------------------


def _sizes(t) -> str:
    if t.constraints and not t.sizes:
        c = t.constraints
        rules = []
        if c.min_side:
            rules.append(f"short side ≥ {c.min_side}")
        if c.max_side:
            rules.append(f"long side ≤ {c.max_side}")
        if c.max_aspect:
            rules.append(f"≤ {c.max_aspect:g}:1")
        if c.aspects:
            rules.append(" or ".join(c.aspects))
        rec = ", ".join(f"{w}×{h}" for w, h in t.recommended_sizes)
        return e("; ".join(rules)) + (f'<br><span class="muted">generates {e(rec)}</span>' if rec else "")
    return ", ".join(f"{w}×{h}" for w, h in t.sizes)


def targets_html() -> str:
    groups = [
        ("Apple screenshots", lambda t: t.store == "apple" and t.kind == "screenshot"),
        ("Apple app previews (video)", lambda t: t.kind == "video"),
        ("Google Play screenshots", lambda t: t.store == "google" and t.kind == "screenshot"),
        ("Store graphics and icons", lambda t: t.kind in ("graphic", "icon")),
    ]
    targets = list(load_targets().values())
    out = []
    for title, pick in groups:
        rows = []
        for t in filter(pick, targets):
            notes = []
            if t.required:
                notes.append(f"<strong>{e(t.required)}</strong>")
            if t.fallback:
                notes.append(f"Falls back to {code(t.fallback)}.")
            if t.preview_type:
                notes.append(f"Preview type {code(t.preview_type)}.")
            if not t.templates_allowed:
                notes.append("No templates or frames.")
            fmt = "/".join(t.formats) + ("" if not t.alpha else ", alpha OK")
            count = f"{t.count_min}–{t.count_max}" if t.count_min != t.count_max else str(t.count_max)
            rows.append([code(t.id), e(t.name), _sizes(t), e(fmt), count, " ".join(notes)])
        out.append(f'<h3 id="targets-{slug(title)}">{e(title)}</h3>')
        out.append(table(["ID", "Name", "Accepted sizes (px)", "Formats", "Count", "Notes"], rows, "targets"))
    sources = {t.store: (t.source, t.verified) for t in targets if t.kind != "video"}
    preview = next(t for t in targets if t.kind == "video")
    links = [
        f'<a href="{e(src)}">{"App Store" if s == "apple" else "Google Play"} specs</a> (verified {e(v)})'
        for s, (src, v) in sources.items()
    ]
    links.append(f'<a href="{e(preview.source)}">App preview specs</a> (verified {e(preview.verified)})')
    out.append(f'<p class="muted">Sources: {", ".join(links)}.</p>')
    return "\n".join(out)


def target_count() -> str:
    return str(len(load_targets()))


# --- Schemas --------------------------------------------------------------------------------


def _schema_type(prop: dict, defs: dict) -> str:
    if "$ref" in prop:
        return e(prop["$ref"].split("/")[-1])
    if "enum" in prop:
        return " | ".join(code(json.dumps(v)) for v in prop["enum"])
    if "const" in prop:
        return code(json.dumps(prop["const"]))
    if "anyOf" in prop:
        return " or ".join(_schema_type(p, defs) for p in prop["anyOf"] if p.get("type") != "null") + (
            " or null" if any(p.get("type") == "null" for p in prop["anyOf"]) else ""
        )
    kind = prop.get("type", "any")
    if kind == "array":
        return f"list of {_schema_type(prop.get('items', {}), defs)}"
    if kind == "object" and isinstance(prop.get("additionalProperties"), dict):
        return f"map of {_schema_type(prop['additionalProperties'], defs)}"
    return {
        "string": "text",
        "number": "number",
        "integer": "integer",
        "boolean": "true/false",
        "object": "mapping",
    }.get(kind, kind)


def _schema_rows(schema: dict, defs: dict, prefix: str, refs_to_expand: set[str]) -> list[list[str]]:
    rows = []
    required = set(schema.get("required", []))
    for name, prop in schema.get("properties", {}).items():
        path = f"{prefix}{name}"
        default = prop.get("default")
        default_html = code(json.dumps(default)) if "default" in prop and default not in (None, [], {}) else ""
        if name in required:
            default_html = '<span class="tag">required</span>'
        rows.append([code(path), _schema_type(prop, defs), default_html, inline_md(prop.get("description", ""))])
        ref = prop.get("$ref") or next((p["$ref"] for p in prop.get("anyOf", []) if "$ref" in p), None)
        if ref is None and prop.get("type") == "array":
            ref = prop.get("items", {}).get("$ref")
            path += "[]"
        if ref and ref.split("/")[-1] in refs_to_expand:
            rows += _schema_rows(defs[ref.split("/")[-1]], defs, path + ".", refs_to_expand)
    return rows


def config_fields_html() -> str:
    schema = Config.model_json_schema()
    defs = schema.get("$defs", {})
    rows = _schema_rows(schema, defs, "", {"Screen", "Graphic"})
    return table(["Field", "Type", "Default", "Description"], rows, "fields")


def template_fields_html() -> str:
    schema = Template.model_json_schema(by_alias=True)
    defs = schema.get("$defs", {})
    rows = _schema_rows(schema, defs, "", set(defs))
    return table(["Setting", "Type", "Default", "Description"], rows, "fields")


def templates_html() -> str:
    rows = [[code(t.name), e(t.description)] for t in builtin_templates().values()]
    return table(["Template", "Look"], rows)


def frames_html() -> str:
    return table(["Frame", "Description"], [[code(n), e(d)] for n, d in BUILTIN_FRAMES.items()])


# --- Codes and tools ------------------------------------------------------------------------


def issue_codes_html() -> str:
    rows = [[code(c), e(sev), inline_md(desc)] for c, (sev, desc) in sorted(ISSUE_CODES.items())]
    return table(["Code", "Severity", "Meaning"], rows, "codes")


def usage_codes_html() -> str:
    rows = [[code(c), inline_md(desc)] for c, desc in sorted(USAGE_CODES.items())]
    return table(["Code", "Meaning"], rows, "codes")


def mcp_tools_html() -> str:
    rows = []
    for fn in READ_ONLY + WRITES:
        doc = " ".join((fn.__doc__ or "").split())
        rows.append([code(fn.__name__), "read-only" if fn in READ_ONLY else "writes files", inline_md(doc)])
    return table(["Tool", "Kind", "What it does"], rows, "codes")


SECTIONS = {
    "version": lambda: e(__version__),
    "target-count": target_count,
    "commands": commands_html,
    "commands-nav": commands_nav,
    "targets": targets_html,
    "config-fields": config_fields_html,
    "template-fields": template_fields_html,
    "templates": templates_html,
    "frames": frames_html,
    "issue-codes": issue_codes_html,
    "usage-codes": usage_codes_html,
    "mcp-tools": mcp_tools_html,
}


def render() -> str:
    text = TEMPLATE.read_text("utf-8")
    missing = [name for name in SECTIONS if f"<!-- @{name} -->" not in text]
    if missing:
        raise SystemExit(f"Template is missing markers: {', '.join(missing)}")
    for name, build in SECTIONS.items():
        text = text.replace(f"<!-- @{name} -->", build())
    leftover = re.findall(r"<!-- @([a-z-]+) -->", text)
    if leftover:
        raise SystemExit(f"Unknown markers in template: {', '.join(leftover)}")
    return text


def main_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="Fail if docs/index.html is out of date")
    args = parser.parse_args(argv)
    page = render()
    if args.check:
        current = OUTPUT.read_text("utf-8") if OUTPUT.exists() else ""
        if current != page:
            print("docs/index.html is out of date; run: python scripts/build_docs.py", file=sys.stderr)
            return 1
        print("docs/index.html is up to date")
        return 0
    OUTPUT.write_text(page, "utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(page):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
