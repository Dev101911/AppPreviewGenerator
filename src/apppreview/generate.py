"""Turn source screenshots into store-compliant images."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageColor, ImageOps, ImageStat, UnidentifiedImageError

from apppreview.compose import compose
from apppreview.errors import AppPreviewError
from apppreview.layouts import check_layout, layout_path, unsupported_reason
from apppreview.specs import Size, Target, format_sizes, get_target, size_problems
from apppreview.template import Template, load_template
from apppreview.validate import Report, validate_images

MODES = ("fit", "fill", "stretch")
UPSCALE_WARNING = 1.5
_EXT = {"png": "png", "jpeg": "jpg"}


@dataclass
class Output:
    source: str
    target: str
    path: str
    width: int
    height: int
    format: str
    scale: float | None
    warnings: list[str] = field(default_factory=list)
    locale: str | None = None
    screen: str | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "path": self.path,
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "scale": None if self.scale is None else round(self.scale, 3),
            "warnings": self.warnings,
            **({"locale": self.locale} if self.locale else {}),
            **({"screen": self.screen} if self.screen else {}),
        }


@dataclass
class GenerateResult:
    outputs: list[Output]
    reports: list[Report]
    dry_run: bool
    out_dir: str

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.reports)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "out_dir": self.out_dir,
            "outputs": [o.to_dict() for o in self.outputs],
            "validation": [r.to_dict() for r in self.reports],
        }


def parse_size(text: str) -> Size:
    try:
        w, h = text.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise AppPreviewError("invalid_size", f"Invalid size '{text}'; use WIDTHxHEIGHT, e.g. 1320x2868") from None


def choose_size(target: Target, width: int, height: int) -> Size:
    """Pick the target's output size that best matches the source.

    An exact match wins. Otherwise prefer the same orientation, then the closest aspect
    ratio, treating sizes within 1% of the closest as equal and taking the preferred one.
    """
    candidates = target.output_sizes
    if not candidates:
        raise AppPreviewError("no_output_size", f"Target {target.id} has no output size; pass --size")
    if (width, height) in candidates:
        return width, height
    portrait = height >= width
    source_aspect = width / height

    def orientation_mismatch(size: Size) -> bool:
        w, h = size
        return w != h and (h >= w) != portrait

    def aspect_diff(size: Size) -> float:
        return abs(size[0] / size[1] - source_aspect) / source_aspect

    pool = [s for s in candidates if not orientation_mismatch(s)] or list(candidates)
    best = min(aspect_diff(s) for s in pool)
    return next(s for s in pool if aspect_diff(s) <= best + 0.01)


def resolve_background(spec: str, img: Image.Image | None) -> tuple[int, int, int]:
    if spec == "auto" and img is not None:
        return _edge_color(img)
    try:
        return ImageColor.getrgb(spec)[:3]
    except ValueError:
        raise AppPreviewError("invalid_color", f"Invalid color '{spec}'; use a name, #RRGGBB, or 'auto'") from None


def _edge_color(img: Image.Image) -> tuple[int, int, int]:
    """Average color of the image border, for seamless letterboxing."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    strips = [rgb.crop(box) for box in [(0, 0, w, 1), (0, h - 1, w, h), (0, 0, 1, h), (w - 1, 0, w, h)]]
    stats = [ImageStat.Stat(strip) for strip in strips]
    count = sum(st.count[0] for st in stats)
    return tuple(round(sum(st.sum[i] for st in stats) / count) for i in range(3))


def render(
    img: Image.Image, size: Size, mode: str, background: tuple[int, int, int], keep_alpha: bool
) -> tuple[Image.Image, float]:
    """Return the image resized to `size` and the scale factor applied to the source."""
    img = ImageOps.exif_transpose(img).convert("RGBA")
    tw, th = size
    sw, sh = img.size
    if mode == "stretch":
        out = img.resize(size, Image.Resampling.LANCZOS)
        scale = max(tw / sw, th / sh)
    elif mode == "fill":
        out = ImageOps.fit(img, size, Image.Resampling.LANCZOS)
        scale = max(tw / sw, th / sh)
    else:
        scale = min(tw / sw, th / sh)
        resized = img.resize((max(1, round(sw * scale)), max(1, round(sh * scale))), Image.Resampling.LANCZOS)
        out = Image.new("RGBA", size, (0, 0, 0, 0) if keep_alpha else (*background, 255))
        out.alpha_composite(resized, ((tw - resized.width) // 2, (th - resized.height) // 2))

    if keep_alpha:
        return out, scale
    flat = Image.new("RGB", size, background)
    flat.paste(out, mask=out.getchannel("A"))
    return flat, scale


def check_target_options(
    target: Target, count: int, fmt: str | None, size: Size | None, templated: bool, framed: bool = False
) -> None:
    """Raise AppPreviewError if the requested options can never produce compliant output."""
    if count > target.count_max:
        raise AppPreviewError("too_many_images", f"{count} images given; {target.id} allows at most {target.count_max}")
    if fmt and fmt not in target.formats:
        raise AppPreviewError(
            "invalid_format", f"{target.id} does not accept {fmt}; allowed: {', '.join(target.formats)}"
        )
    if size and size_problems(target, *size):
        raise AppPreviewError(
            "invalid_size",
            f"{size[0]}x{size[1]} is not accepted by {target.id}: {'; '.join(size_problems(target, *size))}"
            + (f". Accepted: {format_sizes(target.sizes)}" if target.sizes else ""),
        )
    if framed and not target.frames_allowed:
        raise AppPreviewError("frame_not_allowed", f"{target.id} screenshots must not be shown inside device frames")
    if templated and not target.templates_allowed:
        raise AppPreviewError(
            "template_not_allowed",
            f"{target.id} screenshots must show only the app UI; captions and backgrounds are not allowed",
        )


def load_source(path) -> Image.Image:
    try:
        with Image.open(path) as img:
            img.load()
            return img.copy()
    except FileNotFoundError:
        raise AppPreviewError("input_not_found", f"Input not found: {path}") from None
    except (UnidentifiedImageError, OSError) as exc:
        raise AppPreviewError("input_unreadable", f"Cannot read image {path}: {exc}") from None


def _plain_scale(source: Size, out: Size, mode: str) -> float:
    ratios = (out[0] / source[0], out[1] / source[1])
    return min(ratios) if mode == "fit" else max(ratios)


def produce(
    img: Image.Image,
    target: Target,
    dest: Path,
    *,
    size: Size | None = None,
    fmt: str | None = None,
    mode: str = "fit",
    background: str = "#ffffff",
    template: Template | None = None,
    caption: str | None = None,
    subtitle: str | None = None,
    dry_run: bool = False,
    locale: str | None = None,
) -> tuple[Size, str, float | None, list[str]]:
    """Render one image for one target and save it to `dest` (with the right extension).

    Returns (output size, format, scale, warnings). Nothing is rendered or written on a dry run.
    """
    target_format = fmt or target.formats[0]
    source_size = ImageOps.exif_transpose(img).size
    out_size = size or choose_size(target, *source_size)
    warnings: list[str] = []

    if template:
        scale = None
        if not dry_run:
            rendered, scale, warnings = compose(img, out_size, template, caption, subtitle, locale)
    else:
        scale = _plain_scale(source_size, out_size, mode)
        if scale > UPSCALE_WARNING:
            warnings.append(f"Source is upscaled {scale:.2f}x; output may look blurry")
        if mode == "fit" and abs(source_size[0] / source_size[1] - out_size[0] / out_size[1]) > 0.01:
            warnings.append("Aspect ratios differ; output is letterboxed (use --mode fill to crop instead)")
        if not dry_run:
            keep_alpha = target.alpha and target_format == "png"
            rendered, _ = render(img, out_size, mode, resolve_background(background, img), keep_alpha)

    if not dry_run:
        save_image(rendered, dest, target_format)
    return out_size, target_format, scale, warnings


def save_image(img: Image.Image, dest: Path, fmt: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jpeg":
        img.save(dest, "JPEG", quality=92, optimize=True)
    else:
        img.save(dest, "PNG", optimize=True)


def output_name(index: int, stem: str, fmt: str) -> str:
    return f"{index:02d}_{stem}.{_EXT[fmt]}"


def generate(
    inputs,
    targets,
    out_dir: str | os.PathLike = "apppreview-out",
    mode: str = "fit",
    background: str = "#ffffff",
    fmt: str | None = None,
    size: str | Size | None = None,
    dry_run: bool = False,
    template: str | Template | None = None,
    captions: list[str] | None = None,
    subtitles: list[str] | None = None,
    locale: str | None = None,
    frame: str | None = None,
    layout: str = "default",
) -> GenerateResult:
    if mode not in MODES:
        raise AppPreviewError("invalid_mode", f"Invalid mode '{mode}'; use one of {', '.join(MODES)}")
    if not inputs:
        raise AppPreviewError("no_inputs", "No input images given")
    resolved = [t if isinstance(t, Target) else get_target(t) for t in targets]
    if not resolved:
        raise AppPreviewError("no_targets", "No targets given; pass --target (see `apppreview targets`)")
    if isinstance(size, str):
        size = parse_size(size)
    if template is not None and not isinstance(template, Template):
        template = load_template(template)
    for name, texts in (("captions", captions), ("subtitles", subtitles)):
        if texts and len(texts) != len(inputs):
            raise AppPreviewError(
                "text_count_mismatch", f"{len(texts)} {name} given for {len(inputs)} images; give one per image"
            )
    if (captions or subtitles or frame) and template is None:
        template = load_template("clean")
    if frame:
        template = load_template(template, {"screenshot": {"frame": frame}})
    if background != "auto":
        resolve_background(background, None)  # fail fast on an invalid color

    check_layout(layout)
    for target in resolved:
        reason = unsupported_reason(layout, target)
        if reason:
            raise AppPreviewError("unsupported_in_layout", f"{target.id}: {reason}")
        check_target_options(
            target, len(inputs), fmt, size, template is not None, bool(template and template.screenshot.frame)
        )

    sources = [(Path(path), load_source(path)) for path in inputs]
    out_dir = Path(out_dir)
    outputs: list[Output] = []
    reports: list[Report] = []
    for target in resolved:
        target_outputs = []
        for index, (path, img) in enumerate(sources, start=1):
            target_format = fmt or target.formats[0]
            dest = layout_path(layout, out_dir, target, locale, output_name(index, path.stem, target_format))
            out_size, target_format, scale, warnings = produce(
                img,
                target,
                dest,
                size=size,
                fmt=fmt,
                mode=mode,
                background=background,
                template=template,
                caption=captions[index - 1] if captions else None,
                subtitle=subtitles[index - 1] if subtitles else None,
                dry_run=dry_run,
                locale=locale,
            )
            output = Output(str(path), target.id, str(dest), *out_size, target_format, scale, warnings)
            outputs.append(output)
            target_outputs.append(output)
        if not dry_run:
            reports.append(validate_images([o.path for o in target_outputs], target))

    return GenerateResult(outputs=outputs, reports=reports, dry_run=dry_run, out_dir=str(out_dir))
