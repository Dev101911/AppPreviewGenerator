"""Command-line interface. Every command supports --json for machine-readable output.

Exit codes: 0 success, 1 validation failure, 2 usage or input error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml

from apppreview import __version__
from apppreview.capture import capture_android, capture_ios, suggest_simulators
from apppreview.config import DEFAULT_CONFIG, STARTER_CONFIG, build, config_schema, template_schema
from apppreview.errors import AppPreviewError
from apppreview.frames import BUILTIN_FRAMES
from apppreview.generate import MODES, generate
from apppreview.graphic import generate_graphic
from apppreview.locales import is_rtl, store_locale, store_locales
from apppreview.specs import STORES, format_sizes, get_target, load_targets
from apppreview.template import builtin_templates
from apppreview.validate import ERROR, identify, validate_images

EXIT_OK, EXIT_INVALID, EXIT_USAGE = 0, 1, 2

json_option = click.option("--json", "as_json", is_flag=True, help="Write machine-readable JSON to stdout.")


def emit_json(data) -> None:
    click.echo(json.dumps(data, indent=2))


def fail(exc: AppPreviewError, as_json: bool):
    if as_json:
        emit_json({"ok": False, "error": exc.to_dict()})
    else:
        click.echo(f"Error: {exc.message}", err=True)
    sys.exit(EXIT_USAGE)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="apppreview")
def main():
    """Generate and validate App Store and Google Play screenshots.

    Exit codes: 0 success, 1 validation failure, 2 usage or input error.
    """


@main.command()
@click.argument("target_id", required=False)
@click.option("--store", type=click.Choice(STORES), help="Only list targets for one store.")
@json_option
def targets(target_id, store, as_json):
    """List screenshot targets, or show details for TARGET_ID."""
    if target_id:
        try:
            target = get_target(target_id)
        except AppPreviewError as exc:
            fail(exc, as_json)
        if as_json:
            emit_json(target.to_dict())
            return
        _print_target(target)
        return

    selected = [t for t in load_targets().values() if store is None or t.store == store]
    if as_json:
        emit_json([t.to_dict() for t in selected])
        return
    width = max(len(t.id) for t in selected)
    for t in selected:
        sizes = t.output_sizes[:2]
        detail = format_sizes(sizes) + (" ..." if len(t.output_sizes) > 2 else "")
        if t.constraints and not t.sizes:
            detail = f"rule-based (e.g. {detail})"
        click.echo(f"{t.id:<{width}}  {t.name:<38} {detail}")
    for store_targets in {t.store: t for t in selected}.values():
        click.echo(f"\n{store_targets.store} specs verified {store_targets.verified}: {store_targets.source}")


def _print_target(t):
    click.echo(f"{t.id} — {t.name} ({t.store}, {t.kind})")
    if t.sizes:
        click.echo(f"  Sizes:       {format_sizes(t.sizes)}")
    if t.constraints:
        c = t.constraints
        rules = []
        if c.min_side:
            rules.append(f"shortest side ≥ {c.min_side}px")
        if c.max_side:
            rules.append(f"longest side ≤ {c.max_side}px")
        if c.max_aspect:
            rules.append(f"aspect ≤ {c.max_aspect:g}:1")
        if c.aspects:
            rules.append(f"aspect one of {', '.join(c.aspects)}")
        click.echo(f"  Rules:       {'; '.join(rules)}")
    if t.recommended_sizes:
        click.echo(f"  Recommended: {format_sizes(t.recommended_sizes)}")
    click.echo(f"  Formats:     {', '.join(t.formats)}; transparency {'allowed' if t.alpha else 'not allowed'}")
    if t.max_bytes:
        click.echo(f"  Max size:    {t.max_bytes:,} bytes")
    click.echo(f"  Count:       {t.count_min}–{t.count_max}")
    if t.required:
        click.echo(f"  Required:    {t.required}")
    if t.fallback:
        click.echo(f"  Fallback:    {t.fallback}")
    for note in t.notes:
        click.echo(f"  Note:        {note}")
    click.echo(f"  Source:      {t.source} (verified {t.verified})")


@main.command()
@click.argument("images", nargs=-1, required=True, type=click.Path())
@click.option("-t", "--target", "target_id", required=True, help="Target ID, e.g. apple.iphone-6.9.")
@json_option
def validate(images, target_id, as_json):
    """Check IMAGES against a target's store requirements."""
    try:
        report = validate_images(images, target_id)
    except AppPreviewError as exc:
        fail(exc, as_json)

    if as_json:
        emit_json(report.to_dict())
    else:
        for issue in report.set_issues:
            click.echo(f"{_badge(issue.severity)} {issue.message}")
        for f in report.files:
            size = f"{f.width}x{f.height} {f.format}" if f.width else ""
            click.echo(f"{'PASS' if f.ok else 'FAIL'}  {f.path}  {size}")
            for issue in f.issues:
                click.echo(f"      {_badge(issue.severity)} [{issue.code}] {issue.message}")
                if issue.fix and issue.severity == ERROR:
                    click.echo(f"            fix: {issue.fix}")
        summary = report.to_dict()["summary"]
        click.echo(
            f"\n{report.target.id}: {summary['files']} file(s), "
            f"{summary['errors']} error(s), {summary['warnings']} warning(s)"
        )
    sys.exit(EXIT_OK if report.ok else EXIT_INVALID)


def _badge(severity: str) -> str:
    return "error:" if severity == ERROR else "warn: "


@main.command(name="generate")
@click.argument("images", nargs=-1, required=True, type=click.Path())
@click.option("-t", "--target", "target_ids", multiple=True, required=True, help="Target ID; repeat for several.")
@click.option("-o", "--out", "out_dir", default="apppreview-out", show_default=True, help="Output directory.")
@click.option(
    "--mode",
    type=click.Choice(MODES),
    default="fit",
    show_default=True,
    help="fit: letterbox on background; fill: center-crop; stretch: distort.",
)
@click.option(
    "--background",
    default="#ffffff",
    show_default=True,
    help="Letterbox/flatten color: name, #RRGGBB, or 'auto' (matches the image edges).",
)
@click.option("--format", "fmt", type=click.Choice(["png", "jpeg"]), help="Output format [default: png].")
@click.option("--size", help="Exact output size WIDTHxHEIGHT; must be accepted by every target.")
@click.option("--dry-run", is_flag=True, help="Report what would be written without writing files.")
@click.option("--template", help="Template name (see `apppreview templates`) or template .yaml path.")
@click.option("--caption", "captions", multiple=True, help="Caption per image, in order. Implies a template.")
@click.option("--subtitle", "subtitles", multiple=True, help="Subtitle per image, in order.")
@click.option("--locale", help="Language of the captions (e.g. ar, ja-JP), for text direction and shaping.")
@click.option("--frame", help="Device frame: auto, phone, tablet, or a frame .png/.yaml path. Implies a template.")
@click.option(
    "--layout",
    type=click.Choice(["default", "fastlane", "gpp"]),
    help="Folder layout: default, fastlane (deliver/supply) or gpp (Gradle Play Publisher).",
)
@json_option
def generate_cmd(
    images,
    target_ids,
    out_dir,
    mode,
    background,
    fmt,
    size,
    dry_run,
    template,
    captions,
    subtitles,
    locale,
    frame,
    layout,
    as_json,
):
    """Resize IMAGES into compliant screenshots for one or more targets.

    Output goes to OUT/<target-id>/NN_<name>.<ext> and is re-validated.
    With --template or --caption, screenshots are placed on a styled canvas.
    """
    try:
        result = generate(
            images,
            target_ids,
            out_dir,
            mode,
            background,
            fmt,
            size,
            dry_run,
            template=template,
            captions=list(captions) or None,
            subtitles=list(subtitles) or None,
            locale=locale,
            frame=frame,
            layout=layout or "default",
        )
    except AppPreviewError as exc:
        fail(exc, as_json)
    _report_outputs(result, dry_run, as_json)


def _report_outputs(result, dry_run: bool, as_json: bool):
    if as_json:
        emit_json(result.to_dict())
    else:
        verb = "Would write" if dry_run else "Wrote"
        for o in result.outputs:
            click.echo(f"{verb} {o.path}  ({o.width}x{o.height} {o.format})")
            for warning in o.warnings:
                click.echo(f"      warn:  {warning}")
        for entry in result.reports:
            locale, report = entry if isinstance(entry, tuple) else (None, entry)
            label = f"{locale}/{report.target.id}" if locale else report.target.id
            for issue in report.all_issues:
                click.echo(f"{_badge(issue.severity)} {label}: {issue.message}")
        for warning in getattr(result, "warnings", []):
            click.echo(f"warn:  {warning}")
        if not dry_run:
            click.echo("\nAll outputs are compliant." if result.ok else "\nSome outputs failed validation.")
    sys.exit(EXIT_OK if result.ok else EXIT_INVALID)


@main.command(name="graphic")
@click.option("-t", "--target", "target_id", required=True, help="google.feature-graphic or google.tv-banner.")
@click.option("--title", required=True, help="Main text, usually the app name.")
@click.option("--subtitle", help="Optional tagline.")
@click.option("--icon", type=click.Path(), help="App icon image, shown left of the text.")
@click.option("--template", default="clean", show_default=True, help="Template for background, colors and fonts.")
@click.option("-o", "--out", default="apppreview-out", show_default=True, help="Output file or directory.")
@click.option("--format", "fmt", type=click.Choice(["png", "jpeg"]), help="Output format [default: png].")
@click.option("--dry-run", is_flag=True, help="Report what would be written without writing files.")
@click.option("--locale", help="Language of the text (e.g. ar, ja-JP), for text direction and shaping.")
@click.option(
    "--layout",
    type=click.Choice(["default", "fastlane", "gpp"]),
    help="Folder layout: default, fastlane (deliver/supply) or gpp (Gradle Play Publisher).",
)
@json_option
def graphic_cmd(target_id, title, subtitle, icon, template, out, fmt, dry_run, locale, layout, as_json):
    """Render a store graphic (feature graphic, TV banner) from a title and app icon."""
    try:
        result = generate_graphic(
            target_id, title, subtitle, icon, template, out, fmt, dry_run, locale, layout or "default"
        )
    except AppPreviewError as exc:
        fail(exc, as_json)
    _report_outputs(result, dry_run, as_json)


@main.command(name="build")
@click.argument("config", default=DEFAULT_CONFIG, type=click.Path())
@click.option("-t", "--target", "only_targets", multiple=True, help="Only build these targets.")
@click.option("-l", "--locale", "only_locales", multiple=True, help="Only build these locales.")
@click.option("--dry-run", is_flag=True, help="Check the config and list outputs without rendering.")
@click.option(
    "--layout",
    type=click.Choice(["default", "fastlane", "gpp"]),
    help="Override the config's layout: default, fastlane (deliver/supply) or gpp (Gradle Play Publisher).",
)
@json_option
def build_cmd(config, only_targets, only_locales, dry_run, layout, as_json):
    """Render every screen x target x locale defined in CONFIG (default: apppreview.yaml)."""
    try:
        result = build(config, only_targets, only_locales, dry_run, layout)
    except AppPreviewError as exc:
        fail(exc, as_json)
    _report_outputs(result, dry_run, as_json)


@main.command(name="init")
@click.argument("path", default=DEFAULT_CONFIG, type=click.Path())
@click.option("--force", is_flag=True, help="Overwrite an existing file.")
@json_option
def init_cmd(path, force, as_json):
    """Write a starter apppreview.yaml."""
    target = Path(path)
    if target.exists() and not force:
        fail(AppPreviewError("file_exists", f"{target} already exists; use --force to overwrite"), as_json)
    target.write_text(STARTER_CONFIG, "utf-8")
    if as_json:
        emit_json({"ok": True, "path": str(target)})
    else:
        click.echo(f"Wrote {target}. Edit the screens, then run `apppreview build`.")


@main.command(name="templates")
@click.argument("name", required=False)
@json_option
def templates_cmd(name, as_json):
    """List built-in templates, or show one template's full settings."""
    templates = builtin_templates()
    if name:
        if name not in templates:
            fail(AppPreviewError("unknown_template", f"Unknown template '{name}'"), as_json)
        data = templates[name].model_dump(by_alias=True)
        click.echo(json.dumps(data, indent=2) if as_json else yaml.safe_dump(data, sort_keys=False))
        return
    if as_json:
        emit_json([t.model_dump(by_alias=True) for t in templates.values()])
        return
    for t in templates.values():
        click.echo(f"{t.name:<10} {t.description}")


@main.group(name="capture")
def capture_group():
    """Take screenshots from a booted iOS Simulator or Android emulator/device."""


def _report_capture(result, as_json):
    if as_json:
        emit_json(result.to_dict())
        return
    click.echo(f"Captured {result.path} from {result.device} ({result.width}x{result.height})")
    click.echo(
        f"Fits as-is: {', '.join(result.matches) if result.matches else 'no targets; run `apppreview generate`'}"
    )


@capture_group.command(name="ios")
@click.option("-o", "--out", required=True, type=click.Path(), help="PNG file to write.")
@click.option("--device", help="Simulator name or UDID, if more than one is booted.")
@click.option("--no-status-bar", is_flag=True, help="Leave the status bar as is (default: 9:41, full bars).")
@json_option
def capture_ios_cmd(out, device, no_status_bar, as_json):
    """Screenshot a booted iOS Simulator (needs Xcode). The status bar override stays until
    `xcrun simctl status_bar booted clear`."""
    try:
        result = capture_ios(out, device, clean_status_bar=not no_status_bar)
    except AppPreviewError as exc:
        fail(exc, as_json)
    _report_capture(result, as_json)


@capture_group.command(name="android")
@click.option("-o", "--out", required=True, type=click.Path(), help="PNG file to write.")
@click.option("--device", help="adb serial, if more than one device is connected.")
@click.option("--no-demo-mode", is_flag=True, help="Leave the status bar as is (default: demo mode, 09:41).")
@json_option
def capture_android_cmd(out, device, no_demo_mode, as_json):
    """Screenshot an Android emulator or device over adb. Demo mode stays on until
    `adb shell am broadcast -a com.android.systemui.demo -e command exit`."""
    try:
        result = capture_android(out, device, demo_mode=not no_demo_mode)
    except AppPreviewError as exc:
        fail(exc, as_json)
    _report_capture(result, as_json)


@capture_group.command(name="simulators")
@json_option
def capture_simulators_cmd(as_json):
    """Which iOS simulators produce screenshots that fit each Apple target without resizing."""
    suggestions = suggest_simulators()
    if as_json:
        emit_json(suggestions)
        return
    width = max(len(t) for t in suggestions)
    for target_id, names in suggestions.items():
        click.echo(f"{target_id:<{width}}  {', '.join(names)}")


@main.command(name="frames")
@json_option
def frames_cmd(as_json):
    """List built-in device frames. Custom frames are PNGs with a transparent screen."""
    if as_json:
        emit_json([{"name": name, "description": text} for name, text in BUILTIN_FRAMES.items()])
        return
    for name, text in BUILTIN_FRAMES.items():
        click.echo(f"{name:<8} {text}")
    click.echo(
        "\nCustom: any .png with a transparent screen area, or a manifest {image: frame.png, screen: [x, y, w, h]}"
    )


@main.command(name="locales")
@click.argument("locale", required=False)
@click.option("--store", type=click.Choice(STORES), help="Only list one store's locale codes.")
@json_option
def locales_cmd(locale, store, as_json):
    """List store locale codes, or show how LOCALE maps to each store's code."""
    stores = [store] if store else list(STORES)
    if locale:
        mapping = {s: store_locale(s, locale) for s in stores}
        if as_json:
            emit_json({"locale": locale, "stores": mapping, "rtl": is_rtl(locale)})
            return
        for s, code in mapping.items():
            click.echo(f"{s:<7} {code or 'not supported'}")
        return
    data = {s: store_locales()[s] for s in stores}
    if as_json:
        emit_json(data)
        return
    for s, codes in data.items():
        click.echo(f"{s} ({len(codes)} locales)")
        for code, name in codes.items():
            click.echo(f"  {code:<8} {name}")


@main.command(name="schema")
@click.argument("kind", type=click.Choice(["config", "template"]))
def schema_cmd(kind):
    """Print the JSON Schema for the config file or for templates."""
    emit_json(config_schema() if kind == "config" else template_schema())


@main.command(name="identify")
@click.argument("images", nargs=-1, required=True, type=click.Path())
@click.option("--store", type=click.Choice(STORES), help="Only consider one store.")
@json_option
def identify_cmd(images, store, as_json):
    """Show which targets each image already satisfies as-is."""
    results = {str(path): identify(path, store) for path in images}
    if as_json:
        emit_json({"ok": True, "matches": results})
        return
    for path, matches in results.items():
        click.echo(f"{path}: {', '.join(matches) if matches else 'no matching targets'}")


if __name__ == "__main__":
    main()
