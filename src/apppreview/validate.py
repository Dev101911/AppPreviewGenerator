"""Check images against a store target's screenshot requirements."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from apppreview.specs import Target, aspect_matches, get_target, load_targets, size_problems

ERROR = "error"
WARNING = "warning"

# Pillow format names -> spec format names
_FORMATS = {"PNG": "png", "JPEG": "jpeg", "MPO": "jpeg"}
_EXTENSIONS = {"png": {".png"}, "jpeg": {".jpg", ".jpeg"}}


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    fix: str | None = None

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message, "fix": self.fix}


@dataclass
class FileReport:
    path: str
    width: int | None = None
    height: int | None = None
    format: str | None = None
    mode: str | None = None
    bytes: int | None = None
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == ERROR for i in self.issues)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "ok": self.ok,
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "mode": self.mode,
            "bytes": self.bytes,
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass
class Report:
    target: Target
    files: list[FileReport]
    set_issues: list[Issue]

    @property
    def all_issues(self) -> list[Issue]:
        return self.set_issues + [i for f in self.files for i in f.issues]

    @property
    def ok(self) -> bool:
        return not any(i.severity == ERROR for i in self.all_issues)

    def to_dict(self) -> dict:
        issues = self.all_issues
        return {
            "ok": self.ok,
            "target": self.target.id,
            "files": [f.to_dict() for f in self.files],
            "set_issues": [i.to_dict() for i in self.set_issues],
            "summary": {
                "files": len(self.files),
                "errors": sum(i.severity == ERROR for i in issues),
                "warnings": sum(i.severity == WARNING for i in issues),
            },
        }


def has_alpha_channel(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (img.mode == "P" and "transparency" in img.info)


def _generate_hint(path: Path, target: Target) -> str:
    return f"Run `apppreview generate {path} --target {target.id}` to produce a compliant copy."


def validate_file(path: str | os.PathLike, target: Target) -> FileReport:
    path = Path(path)
    report = FileReport(path=str(path))
    if not path.is_file():
        report.issues.append(Issue(ERROR, "not_found", f"File not found: {path}"))
        return report
    report.bytes = path.stat().st_size

    try:
        with Image.open(path) as img:
            report.width, report.height = img.size
            report.format = _FORMATS.get(img.format or "", (img.format or "unknown").lower())
            report.mode = img.mode
            alpha = has_alpha_channel(img)
    except (UnidentifiedImageError, OSError) as exc:
        report.issues.append(Issue(ERROR, "unreadable", f"Cannot read image: {exc}"))
        return report

    hint = _generate_hint(path, target)
    if report.format not in target.formats:
        report.issues.append(
            Issue(ERROR, "format", f"Format is {report.format}; allowed: {', '.join(target.formats)}", hint)
        )
    elif path.suffix.lower() not in _EXTENSIONS[report.format]:
        report.issues.append(
            Issue(
                WARNING,
                "extension_mismatch",
                f"File content is {report.format} but the extension is '{path.suffix}'",
                f"Rename the file to use {' or '.join(sorted(_EXTENSIONS[report.format]))}.",
            )
        )

    if alpha and not target.alpha:
        report.issues.append(
            Issue(ERROR, "alpha", "Image has an alpha channel; this target does not allow transparency", hint)
        )

    base_mode = {"RGBA": "RGB", "RGBa": "RGB", "LA": "L", "La": "L", "PA": "P"}.get(report.mode, report.mode)
    if base_mode == "P":
        report.issues.append(
            Issue(WARNING, "color_mode", "Image uses an 8-bit palette; stores expect 24-bit RGB", hint)
        )
    elif base_mode != "RGB":
        report.issues.append(Issue(ERROR, "color_mode", f"Color mode is {report.mode}; expected RGB", hint))

    for problem in size_problems(target, report.width, report.height):
        report.issues.append(Issue(ERROR, "dimensions", problem, hint))

    if target.max_bytes and report.bytes > target.max_bytes:
        report.issues.append(
            Issue(
                ERROR,
                "file_size",
                f"File is {report.bytes:,} bytes; maximum is {target.max_bytes:,} bytes",
                "Re-encode as JPEG (`--format jpeg`) or reduce the image size.",
            )
        )

    rec = target.recommendations
    if rec.get("min_side") and min(report.width, report.height) < rec["min_side"]:
        report.issues.append(
            Issue(
                WARNING,
                "recommended_size",
                f"Shortest side is below the recommended {rec['min_side']}px (needed for store promotion)",
            )
        )
    if rec.get("aspects") and not any(aspect_matches(report.width, report.height, a) for a in rec["aspects"]):
        report.issues.append(
            Issue(
                WARNING,
                "recommended_aspect",
                f"Aspect ratio is not one of the recommended {', '.join(rec['aspects'])} (needed for store promotion)",
            )
        )
    return report


def validate_set(reports: list[FileReport], target: Target) -> list[Issue]:
    issues: list[Issue] = []
    n = len(reports)
    if n > target.count_max:
        issues.append(Issue(ERROR, "count", f"{n} images given; {target.name} allows at most {target.count_max}"))
    if n < target.count_min:
        issues.append(Issue(WARNING, "count", f"{n} image(s) given; {target.name} needs at least {target.count_min}"))
    min_count = target.recommendations.get("min_count")
    if min_count and target.count_min <= n < min_count:
        issues.append(
            Issue(WARNING, "recommended_count", f"At least {min_count} screenshots are recommended for store promotion")
        )

    sized = [r for r in reports if r.width and r.height]
    if target.uniform_size and len({(r.width, r.height) for r in sized}) > 1:
        issues.append(Issue(ERROR, "mixed_sizes", f"{target.name} screenshots must all use the same size"))
    if len({r.width > r.height for r in sized if r.width != r.height}) > 1:
        issues.append(Issue(WARNING, "mixed_orientation", "The set mixes portrait and landscape screenshots"))
    return issues


def validate_images(paths, target: Target | str) -> Report:
    if isinstance(target, str):
        target = get_target(target)
    files = [validate_file(p, target) for p in paths]
    return Report(target=target, files=files, set_issues=validate_set(files, target))


def identify(path: str | os.PathLike, store: str | None = None) -> list[str]:
    """Return the IDs of all targets that `path` already satisfies on its own."""
    return [t.id for t in load_targets().values() if (store is None or t.store == store) and validate_file(path, t).ok]
