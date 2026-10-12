"""Project config (apppreview.yaml): render every screen for every target and locale."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apppreview.errors import AppPreviewError
from apppreview.generate import (
    Output,
    check_target_options,
    load_source,
    output_name,
    produce,
    resolve_background,
)
from apppreview.specs import get_target
from apppreview.template import Template, load_template
from apppreview.validate import Report, validate_images

DEFAULT_CONFIG = "apppreview.yaml"

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


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    out: str = Field("apppreview-out", description="Output directory, relative to the config file")
    targets: list[str] = Field(min_length=1, description="Target IDs; see `apppreview targets`")
    locales: list[str] = Field(default_factory=list, description="Locale codes, e.g. en-US; empty for a single set")
    template: str | dict | None = Field(
        None, description="Built-in template name, path to a template file, or inline template. Omit for plain resize."
    )
    mode: Literal["fit", "fill", "stretch"] = Field("fit", description="Resize mode when no template is used")
    background: str = Field("#ffffff", description="Letterbox color when no template is used, or 'auto'")
    format: Literal["png", "jpeg"] | None = None
    screens: list[Screen] = Field(min_length=1)


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
            "config_not_found", f"Config file not found: {path}. Run `apppreview init` to create one."
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
    screen: Screen
    target_id: str
    locale: str | None
    source: Path
    dest: Path
    template: Template | None
    caption: str | None
    subtitle: str | None


@dataclass
class BuildResult:
    outputs: list[Output]
    reports: list[tuple[str | None, Report]]
    dry_run: bool
    out_dir: str

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
        }


def plan(config: Config, base_dir: Path, only_targets=(), only_locales=()) -> list[Job]:
    """Resolve every output to produce, failing fast on any config problem."""
    targets = [get_target(t) for t in config.targets if not only_targets or t in only_targets]
    locales = [loc for loc in config.locales if not only_locales or loc in only_locales] or (
        [None] if not config.locales else []
    )
    if not targets or not locales:
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

    ids = [s.id for s in config.screens]
    if len(set(ids)) != len(ids):
        raise AppPreviewError("invalid_config", "Screen ids must be unique")

    jobs, missing = [], []
    out_root = base_dir / config.out
    for target in targets:
        check_target_options(target, len(config.screens), config.format, None, any(screen_templates.values()))
        fmt = config.format or target.formats[0]
        for locale in locales:
            folder = out_root / locale / target.id if locale else out_root / target.id
            for index, screen in enumerate(config.screens, start=1):
                raw = screen.source if isinstance(screen.source, str) else pick_for_target(screen.source, target.id)
                if raw is None:
                    raise AppPreviewError(
                        "missing_source", f"Screen '{screen.id}' has no source for {target.id} and no 'default'"
                    )
                source = base_dir / raw.format(locale=locale or "", target=target.id, screen=screen.id)
                if not source.is_file():
                    missing.append(str(source))
                what = f"Screen '{screen.id}'"
                jobs.append(
                    Job(
                        screen=screen,
                        target_id=target.id,
                        locale=locale,
                        source=source,
                        dest=folder / output_name(index, screen.id, fmt),
                        template=screen_templates[screen.id],
                        caption=localized(screen.caption, locale, f"{what} caption"),
                        subtitle=localized(screen.subtitle, locale, f"{what} subtitle"),
                    )
                )
    if missing:
        listed = "\n".join(f"  {m}" for m in sorted(set(missing)))
        raise AppPreviewError("input_not_found", f"{len(set(missing))} source image(s) not found:\n{listed}")
    return jobs


def build(
    config_path: str | Path = DEFAULT_CONFIG,
    only_targets=(),
    only_locales=(),
    dry_run: bool = False,
) -> BuildResult:
    config, base_dir = load_config(config_path)
    jobs = plan(config, base_dir, only_targets, only_locales)

    cache: dict[Path, object] = {}
    outputs: list[Output] = []
    groups: dict[tuple[str | None, str], list[Output]] = {}
    for job in jobs:
        target = get_target(job.target_id)
        if job.source not in cache:
            cache[job.source] = load_source(job.source)
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
        )
        output = Output(
            str(job.source), target.id, str(job.dest), *out_size, fmt, scale, warnings, job.locale, job.screen.id
        )
        outputs.append(output)
        groups.setdefault((job.locale, target.id), []).append(output)

    reports = []
    if not dry_run:
        for (locale, target_id), group in groups.items():
            reports.append((locale, validate_images([o.path for o in group], target_id)))
    return BuildResult(outputs, reports, dry_run, str(base_dir / config.out))


STARTER_CONFIG = """\
# apppreview project config. Run `apppreview build` to render everything.
# Schema: `apppreview schema config`. Targets: `apppreview targets`.
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
"""
