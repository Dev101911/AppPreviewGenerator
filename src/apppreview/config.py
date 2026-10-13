"""Project config (app-preview.yaml): render every screen for every target and locale."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from apppreview.errors import AppPreviewError
from apppreview.generate import (
    Output,
    check_target_options,
    load_source,
    output_name,
    produce,
    resolve_background,
    save_image,
)
from apppreview.graphic import check_graphic_target, graphic_name, render_graphic
from apppreview.layouts import check_layout, deliver_collisions, layout_path, unsupported_reason
from apppreview.locales import language, store_locale
from apppreview.specs import get_target
from apppreview.template import Template, load_template
from apppreview.validate import Report, validate_images
from apppreview.video import is_video, produce_video

DEFAULT_CONFIG = "app-preview.yaml"
STORE_NAMES = {"apple": "App Store", "google": "Google Play"}

LocalizedText = str | dict[str, str]


class Screen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", description="Used in output file names")
    source: str | dict[str, str] = Field(
        description=(
            "Source screenshot path, relative to the config file. May use {locale}, {target} and {screen}. "
            "A mapping picks a path per target: keys are target IDs or globs like 'apple.ipad-*', plus 'default'."
        )
    )
    caption: LocalizedText | None = Field(
        None, description="Caption text, or a mapping of locale -> text (falls back to language, then 'default')"
    )
    subtitle: LocalizedText | None = None
    template: str | dict | None = Field(None, description="Template overrides for this screen only")
    start: float = Field(0.0, ge=0, description="Video sources: start time in seconds (previews are at most 30s)")
    extend: bool = Field(False, description="Video sources: hold the last frame to reach the 15s minimum")


class Graphic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(description="A graphic target, e.g. google.feature-graphic or google.tv-banner")
    title: LocalizedText = Field(description="Usually the app name; text or a locale -> text mapping")
    subtitle: LocalizedText | None = None
    icon: str | None = Field(None, description="App icon path, relative to the config file; may use {locale}")
    template: str | dict | None = Field(None, description="Template overrides for this graphic")


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    out: str = Field("app-preview-out", description="Output directory, relative to the config file")
    targets: list[str] = Field(default_factory=list, description="Screenshot target IDs; see `app-preview targets`")
    locales: list[str] = Field(default_factory=list, description="Locale codes, e.g. en-US; empty for a single set")
    template: str | dict | None = Field(
        None, description="Built-in template name, path to a template file, or inline template. Omit for plain resize."
    )
    mode: Literal["fit", "fill", "stretch"] = Field("fit", description="Resize mode when no template is used")
    background: str = Field("#ffffff", description="Letterbox color when no template is used, or 'auto'")
    format: Literal["png", "jpeg"] | None = None
    layout: Literal["default", "fastlane", "gpp"] = Field(
        "default",
        description=(
            "Output folder layout: default (<locale>/<target>/), fastlane (deliver + supply), "
            "or gpp (Gradle Play Publisher for Google; deliver for Apple)"
        ),
    )
    default_locale: str = Field("en-US", description="Locale folder for fastlane/gpp layouts when locales is empty")
    locale_overrides: dict[str, str | dict] = Field(
        default_factory=dict,
        description=(
            "Template overrides per locale or language, e.g. {ja: {caption: {font: fonts/NotoSansJP-Bold.otf}}}. "
            "Use it to pick fonts that cover CJK, Arabic, Hebrew, Thai, Devanagari, etc."
        ),
    )
    screens: list[Screen] = Field(default_factory=list)
    graphics: list[Graphic] = Field(default_factory=list, description="Feature graphic, TV banner, ...")

    @model_validator(mode="after")
    def _has_work(self):
        if not self.screens and not self.graphics:
            raise ValueError("add at least one entry to screens or graphics")
        if self.screens and not self.targets:
            raise ValueError("screens need at least one entry in targets")
        return self


def config_schema() -> dict:
    return Config.model_json_schema()


def template_schema() -> dict:
    return Template.model_json_schema(by_alias=True)


def load_config(path: str | Path) -> tuple[Config, Path]:
    path = Path(path)
    if path.is_dir():
        path = path / DEFAULT_CONFIG
    if not path.is_file():
        raise AppPreviewError(
            "config_not_found", f"Config file not found: {path}. Run `app-preview init` to create one."
        )
    text = path.read_text("utf-8")
    try:
        data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise AppPreviewError("invalid_config", f"Cannot parse {path}: {exc}") from None
    try:
        return Config.model_validate(data), path.parent
    except ValidationError as exc:
        details = "\n".join(f"  {'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors())
        raise AppPreviewError("invalid_config", f"Invalid config {path}:\n{details}") from None


def pick_for_target(mapping: dict[str, str], target_id: str) -> str | None:
    if target_id in mapping:
        return mapping[target_id]
    for pattern, value in mapping.items():
        if pattern != "default" and fnmatch.fnmatchcase(target_id, pattern):
            return value
    return mapping.get("default")


def localized(text: LocalizedText | None, locale: str | None, what: str) -> str | None:
    if text is None or isinstance(text, str):
        return text
    candidates = [locale, locale.split("-")[0]] if locale else []
    for key in [*candidates, "default"]:
        if key in text:
            return text[key]
    raise AppPreviewError(
        "missing_text", f"{what} has no text for locale '{locale or '(none)'}' and no 'default' entry"
    )


@dataclass
class Job:
    kind: Literal["screen", "graphic"]
    name: str  # screen id or graphic name
    target_id: str
    locale: str | None
    source: Path | None  # screenshot, or optional icon for graphics
    dest: Path
    template: Template | None
    caption: str | None  # title for graphics
    subtitle: str | None
    start: float = 0.0  # video sources only
    extend: bool = False


@dataclass
class BuildResult:
    outputs: list[Output]
    reports: list[tuple[str | None, Report]]
    dry_run: bool
    out_dir: str
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return all(r.ok for _, r in self.reports)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "out_dir": self.out_dir,
            "outputs": [o.to_dict() for o in self.outputs],
            "validation": [{**r.to_dict(), "locale": locale} for locale, r in self.reports],
            "warnings": self.warnings,
        }


def locale_warnings(config: Config, stores: set[str]) -> list[str]:
    warnings = []
    for locale in config.locales:
        for store in sorted(stores):
            if store_locale(store, locale) is None:
                warnings.append(
                    f"Locale '{locale}' is not a {STORE_NAMES[store]} locale; see `app-preview locales --store {store}`"
                )
    return warnings


def plan(
    config: Config, base_dir: Path, only_targets=(), only_locales=(), layout_override: str | None = None
) -> tuple[list[Job], list[str]]:
    """Resolve every output to produce, failing fast on any config problem.

    Returns the jobs and any warnings about the config."""
    targets = [get_target(t) for t in config.targets if not only_targets or t in only_targets]
    graphics = [g for g in config.graphics if not only_targets or g.target in only_targets]
    locales = [loc for loc in config.locales if not only_locales or loc in only_locales] or (
        [None] if not config.locales else []
    )
    if not (targets and config.screens or graphics) or not locales:
        raise AppPreviewError("nothing_to_build", "The --target/--locale filters match nothing in the config")
    if config.background != "auto":
        resolve_background(config.background, None)

    base_template = load_template(config.template, base_dir=base_dir) if config.template else None
    has_text = any(s.caption or s.subtitle for s in config.screens)
    if base_template is None and has_text:
        base_template = load_template("clean")
    screen_templates = {
        s.id: load_template(base_template or "clean", s.template, base_dir) if s.template else base_template
        for s in config.screens
    }

    localized_templates: dict[tuple[int, str | None], Template | None] = {}

    def for_locale(template: Template | None, locale: str | None) -> Template | None:
        """Apply locale_overrides (exact locale first, then language) on top of `template`."""
        if template is None or not locale:
            return template
        override = config.locale_overrides.get(locale) or config.locale_overrides.get(language(locale))
        if not override:
            return template
        key = (id(template), locale)
        if key not in localized_templates:
            localized_templates[key] = load_template(template, override, base_dir)
        return localized_templates[key]

    ids = [s.id for s in config.screens]
    if len(set(ids)) != len(ids):
        raise AppPreviewError("invalid_config", "Screen ids must be unique")

    jobs, missing, warnings = [], [], []
    out_root = base_dir / config.out
    layout = layout_override or config.layout
    check_layout(layout)

    def skip(target) -> bool:
        reason = unsupported_reason(layout, target)
        if reason:
            warnings.append(f"Skipped {target.id} in the {layout} layout: {reason}")
        return bool(reason)

    for target in targets:
        if skip(target):
            continue
        wants_video = target.kind == "video"
        # A screen applies to a target when it has a source for it of the right kind:
        # recordings (.mp4/.mov/.m4v) for preview targets, images for everything else.
        applicable = []
        for screen in config.screens:
            raw = screen.source if isinstance(screen.source, str) else pick_for_target(screen.source, target.id)
            if raw is not None and is_video(raw) == wants_video:
                applicable.append((screen, raw))
        if not applicable:
            kind = "video (.mp4/.mov)" if wants_video else "image"
            raise AppPreviewError(
                "missing_source", f"No screen has a {kind} source for {target.id}; add one or remove the target"
            )
        templates = [screen_templates[screen.id] for screen, _ in applicable]
        fmt = None if wants_video else config.format
        check_target_options(
            target, len(applicable), fmt, None, any(templates), any(t and t.screenshot.frame for t in templates)
        )
        fmt = fmt or target.formats[0]
        for locale in locales:
            for index, (screen, raw) in enumerate(applicable, start=1):
                source = base_dir / raw.format(locale=locale or "", target=target.id, screen=screen.id)
                if not source.is_file():
                    missing.append(str(source))
                what = f"Screen '{screen.id}'"
                filename = f"{index:02d}_{screen.id}.mp4" if wants_video else output_name(index, screen.id, fmt)
                dest = layout_path(layout, out_root, target, locale, filename, config.default_locale)
                jobs.append(
                    Job(
                        kind="screen",
                        name=screen.id,
                        target_id=target.id,
                        locale=locale,
                        source=source,
                        dest=dest.with_suffix("") if wants_video else dest,
                        template=for_locale(screen_templates[screen.id], locale),
                        caption=localized(screen.caption, locale, f"{what} caption"),
                        subtitle=localized(screen.subtitle, locale, f"{what} subtitle"),
                        start=screen.start,
                        extend=screen.extend,
                    )
                )
    for g in graphics:
        target = get_target(g.target)
        check_graphic_target(target)
        if skip(target):
            continue
        check_target_options(target, 1, config.format, None, True)
        template = load_template(base_template or "clean", g.template, base_dir)
        fmt = config.format or target.formats[0]
        for locale in locales:
            icon = base_dir / g.icon.format(locale=locale or "") if g.icon else None
            if icon and not icon.is_file():
                missing.append(str(icon))
            name = graphic_name(target, fmt)
            what = f"Graphic '{target.id}'"
            jobs.append(
                Job(
                    kind="graphic",
                    name=name.rsplit(".", 1)[0],
                    target_id=target.id,
                    locale=locale,
                    source=icon,
                    dest=layout_path(layout, out_root, target, locale, name, config.default_locale),
                    template=for_locale(template, locale),
                    caption=localized(g.title, locale, f"{what} title"),
                    subtitle=localized(g.subtitle, locale, f"{what} subtitle"),
                )
            )
    if missing:
        listed = "\n".join(f"  {m}" for m in sorted(set(missing)))
        raise AppPreviewError("input_not_found", f"{len(set(missing))} source image(s) not found:\n{listed}")
    stores = {t.store for t in targets} | {get_target(g.target).store for g in graphics}
    if layout != "default":
        warnings += deliver_collisions(targets)
    return jobs, locale_warnings(config, stores) + warnings


def build(
    config_path: str | Path = DEFAULT_CONFIG,
    only_targets=(),
    only_locales=(),
    dry_run: bool = False,
    layout: str | None = None,
) -> BuildResult:
    config, base_dir = load_config(config_path)
    jobs, config_warnings = plan(config, base_dir, only_targets, only_locales, layout)

    cache: dict[Path, object] = {}
    outputs: list[Output] = []
    groups: dict[tuple[str | None, str], list[Output]] = {}
    for job in jobs:
        target = get_target(job.target_id)
        if job.source and job.source not in cache and not is_video(job.source):
            cache[job.source] = load_source(job.source)
        if job.kind == "screen" and target.kind == "video":
            dest, out_size, fmt, scale, warnings = produce_video(
                job.source,
                target,
                job.dest,
                mode=config.mode,
                background=config.background,
                template=job.template,
                caption=job.caption,
                subtitle=job.subtitle,
                locale=job.locale,
                start=job.start,
                extend=job.extend,
                dry_run=dry_run,
            )
            job.dest = dest
        elif job.kind == "graphic":
            fmt = config.format or target.formats[0]
            out_size, scale, warnings = target.sizes[0], None, []
            if not dry_run:
                img, warnings = render_graphic(
                    out_size, job.template, job.caption, job.subtitle, cache.get(job.source), job.locale
                )
                save_image(img, job.dest, fmt)
        else:
            out_size, fmt, scale, warnings = produce(
                cache[job.source],
                target,
                job.dest,
                fmt=config.format,
                mode=config.mode,
                background=config.background,
                template=job.template,
                caption=job.caption,
                subtitle=job.subtitle,
                dry_run=dry_run,
                locale=job.locale,
            )
        output = Output(
            str(job.source or ""), target.id, str(job.dest), *out_size, fmt, scale, warnings, job.locale, job.name
        )
        outputs.append(output)
        groups.setdefault((job.locale, target.id), []).append(output)

    reports = []
    if not dry_run:
        for (locale, target_id), group in groups.items():
            reports.append((locale, validate_images([o.path for o in group], target_id)))
    return BuildResult(outputs, reports, dry_run, str(base_dir / config.out), config_warnings)


STARTER_CONFIG = """\
# app-preview project config. Run `app-preview build` to render everything.
# Schema: `app-preview schema config`. Targets: `app-preview targets`.
version: 1
out: store-screenshots

targets:
  - apple.iphone-6.9
  - apple.ipad-13
  - google.phone

locales: [en-US]

# Built-in: clean, dark, sunset, minimal. Or a path to your own template .yaml,
# or an inline mapping such as {extends: dark, caption: {color: "#FACC15"}}.
template: clean

screens:
  - id: home
    # Paths are relative to this file. {locale}, {target} and {screen} are substituted.
    source: screenshots/{locale}/home.png
    caption:
      en-US: Plan your whole week in seconds
    subtitle:
      en-US: Smart lists that sort themselves
  - id: detail
    source:
      apple.ipad-*: screenshots/{locale}/ipad/detail.png
      default: screenshots/{locale}/detail.png
    caption:
      en-US: Every detail, one tap away

# Store graphics rendered from your app name and icon (optional).
graphics:
  - target: google.feature-graphic
    title: My App
    subtitle:
      en-US: Plan your whole week in seconds
    icon: assets/icon.png
"""
