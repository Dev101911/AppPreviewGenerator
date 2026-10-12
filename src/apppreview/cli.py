"""Command-line interface. Every command supports --json for machine-readable output.

Exit codes: 0 success, 1 validation failure, 2 usage or input error.
"""

from __future__ import annotations

import json
import sys

import click

from apppreview import __version__
from apppreview.errors import AppPreviewError
from apppreview.generate import MODES, generate
from apppreview.specs import STORES, format_sizes, get_target, load_targets
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
@json_option
def generate_cmd(images, target_ids, out_dir, mode, background, fmt, size, dry_run, as_json):
    """Resize IMAGES into compliant screenshots for one or more targets.

    Output goes to OUT/<target-id>/NN_<name>.<ext> and is re-validated.
    """
    try:
        result = generate(images, target_ids, out_dir, mode, background, fmt, size, dry_run)
    except AppPreviewError as exc:
        fail(exc, as_json)

    if as_json:
        emit_json(result.to_dict())
    else:
        verb = "Would write" if dry_run else "Wrote"
        for o in result.outputs:
            click.echo(f"{verb} {o.path}  ({o.width}x{o.height} {o.format})")
            for warning in o.warnings:
                click.echo(f"      warn:  {warning}")
        for report in result.reports:
            for issue in report.all_issues:
                click.echo(f"{_badge(issue.severity)} {report.target.id}: {issue.message}")
        if not dry_run:
            click.echo("\nAll outputs are compliant." if result.ok else "\nSome outputs failed validation.")
    sys.exit(EXIT_OK if result.ok else EXIT_INVALID)


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
