"""Screenshot templates: background, captions, and screenshot styling.

All lengths are fractions of the canvas's shorter side, so one template works for
every target size and orientation.
"""

from __future__ import annotations

import copy
import json
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal

import yaml
from PIL import ImageColor
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from apppreview.errors import AppPreviewError


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_color(value: str) -> str:
    try:
        ImageColor.getrgb(value)
    except ValueError:
        raise ValueError(f"invalid color '{value}'; use a name, #RRGGBB or #RRGGBBAA") from None
    return value


Color = Annotated[str, AfterValidator(_check_color)]


class Gradient(_Model):
    start: Color = Field(alias="from", description="Start color")
    end: Color = Field(alias="to", description="End color")
    angle: float = Field(180, description="Direction in degrees: 180 = top to bottom, 90 = left to right")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Background(_Model):
    color: Color | None = Field(None, description="Solid color")
    gradient: Gradient | None = None
    image: str | None = Field(None, description="Path to a background image (cover-cropped)")

    @model_validator(mode="after")
    def _one_kind(self):
        if sum(x is not None for x in (self.color, self.gradient, self.image)) != 1:
            raise ValueError("background needs exactly one of: color, gradient, image")
        return self


class TextStyle(_Model):
    font: str | None = Field(None, description="Font file path or installed font file name; defaults to bundled Inter")
    weight: Literal["regular", "bold"] = Field("bold", description="Weight of the bundled default font")
    size: float = Field(0.075, gt=0, le=0.5, description="Font size as a fraction of the shorter canvas side")
    min_size: float = Field(0.035, gt=0, le=0.5, description="Smallest size when shrinking text to fit")
    color: Color = "#111111"
    align: Literal["left", "center", "right"] = "center"
    max_lines: int = Field(3, ge=1, le=10)
    line_spacing: float = Field(1.15, ge=0.8, le=3)


class SubtitleStyle(TextStyle):
    weight: Literal["regular", "bold"] = "regular"
    size: float = Field(0.042, gt=0, le=0.5)
    min_size: float = Field(0.028, gt=0, le=0.5)
    color: Color = "#444444"


class Shadow(_Model):
    color: Color = "#00000055"
    blur: float = Field(0.025, ge=0, le=0.2)
    offset: float = Field(0.012, ge=-0.2, le=0.2, description="Vertical offset")


class Frame(_Model):
    name: str = Field(
        "auto",
        description="Built-in frame (auto, phone, tablet) or a path to a frame .png (transparent screen) or manifest",
    )
    color: Color = Field("#1C1C1E", description="Body color of built-in frames")
    island: bool = Field(True, description="Draw a Dynamic Island-style cutout on the built-in phone frame")


class ScreenshotStyle(_Model):
    max_width: float = Field(0.84, gt=0, le=1, description="Max screenshot width as a fraction of canvas width")
    corner_radius: float = Field(0.045, ge=0, le=0.5, description="Fraction of the shorter canvas side")
    shadow: Shadow | None = Field(default_factory=Shadow)
    border: Color | None = Field(None, description="Optional 1-unit border color around the screenshot")
    border_width: float = Field(0.006, ge=0, le=0.05)
    frame: Frame | None = Field(None, description="Wrap the screenshot in a device frame; a name is shorthand")

    @field_validator("frame", mode="before")
    @classmethod
    def _frame_shorthand(cls, value):
        return {"name": value} if isinstance(value, str) else value


class Template(_Model):
    name: str = "custom"
    description: str = ""
    background: Background = Field(default_factory=lambda: Background(color="#ffffff"))
    caption: TextStyle = Field(default_factory=TextStyle)
    subtitle: SubtitleStyle = Field(default_factory=SubtitleStyle)
    caption_position: Literal["top", "bottom"] = "top"
    padding: float = Field(0.07, ge=0, le=0.4, description="Outer padding, fraction of the shorter side")
    gap: float = Field(0.045, ge=0, le=0.4, description="Space between text and screenshot")
    screenshot: ScreenshotStyle = Field(default_factory=ScreenshotStyle)


def _templates_dir():
    return resources.files("apppreview") / "data" / "templates"


def builtin_templates() -> dict[str, Template]:
    found = {}
    for entry in sorted(_templates_dir().iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".yaml"):
            template = _parse(yaml.safe_load(entry.read_text("utf-8")), entry.name)
            found[template.name] = template
    return found


def _deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            # Switching background kind replaces the whole background block.
            if key == "background":
                merged[key] = copy.deepcopy(value)
            else:
                merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _parse(data, origin: str) -> Template:
    try:
        return Template.model_validate(data)
    except ValidationError as exc:
        raise AppPreviewError("invalid_template", f"Invalid template {origin}:\n{_format_errors(exc)}") from None


def _format_errors(exc: ValidationError) -> str:
    return "\n".join(f"  {'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors())


def _read_file(path: Path) -> dict:
    if not path.is_file():
        raise AppPreviewError("template_not_found", f"Template file not found: {path}")
    text = path.read_text("utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise AppPreviewError("invalid_template", f"Template {path} must be a mapping")
    return data


def _absolutize(data: dict, base_dir: Path) -> dict:
    """Make asset paths relative to the file (or config) that declared them."""
    background = data.get("background")
    if isinstance(background, dict) and isinstance(background.get("image"), str):
        if not Path(background["image"]).is_absolute():
            background["image"] = str(base_dir / background["image"])
    screenshot = data.get("screenshot")
    if isinstance(screenshot, dict):
        frame = screenshot.get("frame")
        name = frame.get("name") if isinstance(frame, dict) else frame
        if isinstance(name, str) and (base_dir / name).is_file():
            if isinstance(frame, dict):
                frame["name"] = str(base_dir / name)
            else:
                screenshot["frame"] = str(base_dir / name)
    for key in ("caption", "subtitle"):
        style = data.get(key)
        # A bare font name may be an installed font, so only rewrite paths that exist here.
        if isinstance(style, dict) and isinstance(style.get("font"), str) and (base_dir / style["font"]).is_file():
            style["font"] = str(base_dir / style["font"])
    return data


def resolve_template_data(spec, base_dir: Path | None = None) -> dict:
    """Turn a template reference into a raw dict, following `extends`.

    `spec` is a built-in name, a path to a YAML/JSON file, or an inline mapping that may
    contain `extends: <name or path>` plus overrides.
    """
    base_dir = base_dir or Path.cwd()
    if isinstance(spec, Template):
        return spec.model_dump(by_alias=True)
    if isinstance(spec, str):
        builtins = builtin_templates()
        if spec in builtins:
            return builtins[spec].model_dump(by_alias=True)
        path = Path(spec)
        if path.suffix in (".yaml", ".yml", ".json"):
            path = path if path.is_absolute() else base_dir / path
            data = _read_file(path)
            return resolve_template_data(data, path.parent)
        raise AppPreviewError(
            "unknown_template",
            f"Unknown template '{spec}'. Built-in templates: {', '.join(builtins)}; or pass a .yaml path.",
        )
    if isinstance(spec, dict):
        spec = _absolutize(copy.deepcopy(spec), base_dir)
        parent = spec.pop("extends", None)
        if parent is None:
            return spec
        return _deep_merge(resolve_template_data(parent, base_dir), spec)
    raise AppPreviewError("invalid_template", f"Template must be a name, path, or mapping, not {type(spec).__name__}")


def load_template(spec, overrides: dict | None = None, base_dir: Path | None = None) -> Template:
    data = resolve_template_data(spec, base_dir)
    if overrides:
        data = _deep_merge(data, resolve_template_data(overrides, base_dir))
    template = _parse(data, spec if isinstance(spec, str) else "(inline)")
    return template
