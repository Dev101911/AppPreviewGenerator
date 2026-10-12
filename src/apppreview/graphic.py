"""Store graphics that are not screenshots: Play feature graphic and TV banner."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from apppreview.compose import _draw_text, _rgba, _rounded_mask, fit_text, render_background, text_warnings
from apppreview.errors import AppPreviewError
from apppreview.generate import GenerateResult, Output, check_target_options, load_source, save_image
from apppreview.locales import is_rtl
from apppreview.specs import Size, Target, get_target
from apppreview.template import Template, load_template
from apppreview.validate import validate_images

# Proportions of the canvas height.
TITLE_SIZE, TITLE_MIN = 0.15, 0.07
SUBTITLE_SIZE, SUBTITLE_MIN = 0.075, 0.045
ICON_SIZE = 0.44
# Google crops feature graphics in some placements; keep content inside this width.
SAFE_WIDTH = 0.8


def check_graphic_target(target: Target) -> None:
    if target.kind != "graphic":
        raise AppPreviewError(
            "not_a_graphic_target",
            f"{target.id} is not a graphic target; use one of the targets with kind 'graphic' "
            "(e.g. google.feature-graphic, google.tv-banner)",
        )


def graphic_name(target: Target, fmt: str) -> str:
    """Default file name, e.g. feature-graphic.png for google.feature-graphic."""
    return f"{target.id.split('.', 1)[1]}.{'jpg' if fmt == 'jpeg' else 'png'}"


def generate_graphic(
    target: str | Target,
    title: str,
    subtitle: str | None = None,
    icon: str | Path | None = None,
    template: str | Template = "clean",
    out: str | Path = "apppreview-out",
    fmt: str | None = None,
    dry_run: bool = False,
    locale: str | None = None,
) -> GenerateResult:
    """Render a graphic target. `out` is a file path, or a directory for the default name."""
    target = target if isinstance(target, Target) else get_target(target)
    check_graphic_target(target)
    check_target_options(target, 1, fmt, None, True)
    if not isinstance(template, Template):
        template = load_template(template)
    fmt = fmt or target.formats[0]
    out = Path(out)
    dest = out if out.suffix.lower() in (".png", ".jpg", ".jpeg") else out / target.id / graphic_name(target, fmt)
    if not title:
        raise AppPreviewError("missing_text", "A graphic needs a title")
    icon_img = load_source(icon) if icon else None

    warnings: list[str] = []
    if not dry_run:
        img, warnings = render_graphic(target.sizes[0], template, title, subtitle, icon_img, locale)
        save_image(img, dest, fmt)
    output = Output(str(icon or ""), target.id, str(dest), *target.sizes[0], fmt, None, warnings)
    reports = [] if dry_run else [validate_images([dest], target)]
    return GenerateResult(outputs=[output], reports=reports, dry_run=dry_run, out_dir=str(dest.parent))


def render_graphic(
    size: Size,
    template: Template,
    title: str,
    subtitle: str | None = None,
    icon: Image.Image | None = None,
    locale: str | None = None,
) -> tuple[Image.Image, list[str]]:
    """Icon and text, centered as a group on the template background. Returns (RGB image, warnings)."""
    if not title:
        raise AppPreviewError("missing_text", "A graphic needs a title")
    w, h = size
    warnings: list[str] = []
    canvas = render_background(template.background, size)

    icon_size = round(ICON_SIZE * h) if icon is not None else 0
    gap = round(0.07 * h) if icon is not None else 0
    text_width = SAFE_WIDTH * w - icon_size - gap
    align = "left" if icon is not None else "center"
    title_style = template.caption.model_copy(
        update={"size": TITLE_SIZE, "min_size": TITLE_MIN, "max_lines": 2, "align": align}
    )
    subtitle_style = template.subtitle.model_copy(
        update={"size": SUBTITLE_SIZE, "min_size": SUBTITLE_MIN, "max_lines": 2, "align": align}
    )
    blocks = [_fit_prefer_one_line(title, title_style, h, text_width, 0.5 * h, locale)]
    if subtitle:
        blocks.append(_fit_prefer_one_line(subtitle, subtitle_style, h, text_width, 0.3 * h, locale))
    warnings.extend(text_warnings(blocks))
    line_gap = round(0.25 * blocks[0].line_height) if len(blocks) == 2 else 0
    text_height = sum(b.height for b in blocks) + line_gap
    actual_text_width = max(b.font.getlength(line, **b.layout) for b in blocks for line in b.lines)

    if icon is None:
        left, block_width = (w - text_width) / 2, text_width
    else:
        group_x = round((w - (icon_size + gap + actual_text_width)) / 2)
        icon_y = round((h - icon_size) / 2)
        if is_rtl(locale):  # icon on the right, text reading towards it
            left, icon_x = group_x, round(group_x + actual_text_width + gap)
        else:
            icon_x, left = group_x, group_x + icon_size + gap
        _paste_icon(canvas, icon, (icon_x, icon_y), icon_size, template)
        block_width = actual_text_width

    y = (h - text_height) / 2
    for block in blocks:
        _draw_text(canvas, block, left, y, block_width)
        y += block.height + line_gap

    flat = Image.new("RGB", size, (255, 255, 255))
    flat.paste(canvas, mask=canvas.getchannel("A"))
    return flat, warnings


def _fit_prefer_one_line(text, style, unit, width, max_height, locale):
    """Short lines read better in a banner: shrink to fit one line before wrapping."""
    single = fit_text(text, style.model_copy(update={"max_lines": 1}), unit, width, max_height, locale)
    return single if not single.truncated else fit_text(text, style, unit, width, max_height, locale)


def _paste_icon(canvas: Image.Image, icon: Image.Image, pos: tuple[int, int], size: int, template: Template) -> None:
    img = ImageOps.fit(ImageOps.exif_transpose(icon).convert("RGBA"), (size, size), Image.Resampling.LANCZOS)
    mask = _rounded_mask((size, size), round(0.225 * size))  # iOS-like squircle approximation
    shadow = template.screenshot.shadow
    if shadow:
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        offset = round(shadow.offset * canvas.height)
        layer.paste(Image.new("RGBA", (size, size), _rgba(shadow.color)), (pos[0], pos[1] + offset), mask)
        canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(shadow.blur * canvas.height)))
    alpha = Image.composite(img.getchannel("A"), Image.new("L", (size, size), 0), mask)
    img.putalpha(alpha)
    canvas.alpha_composite(img, pos)
