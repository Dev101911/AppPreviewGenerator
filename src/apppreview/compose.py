"""Render a screenshot onto a template canvas: background, captions, styled screenshot."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

from apppreview.errors import AppPreviewError
from apppreview.specs import Size
from apppreview.template import Background, Template, TextStyle

# Text may use at most this share of the canvas height.
TEXT_BUDGET = 0.32


def _rgba(color: str) -> tuple[int, int, int, int]:
    rgb = ImageColor.getrgb(color)
    return rgb if len(rgb) == 4 else (*rgb, 255)


def render_background(bg: Background, size: Size) -> Image.Image:
    if bg.color:
        return Image.new("RGBA", size, _rgba(bg.color))
    if bg.image:
        try:
            with Image.open(bg.image) as img:
                return ImageOps.fit(ImageOps.exif_transpose(img).convert("RGBA"), size, Image.Resampling.LANCZOS)
        except (FileNotFoundError, OSError) as exc:
            raise AppPreviewError("background_unreadable", f"Cannot read background image {bg.image}: {exc}") from None
    g = bg.gradient
    return Image.composite(
        Image.new("RGBA", size, _rgba(g.end)), Image.new("RGBA", size, _rgba(g.start)), _gradient_mask(size, g.angle)
    )


def _gradient_mask(size: Size, angle: float) -> Image.Image:
    """Linear gradient mask following CSS `linear-gradient(<angle>deg, ...)` geometry."""
    w, h = size
    scale = 256 / max(w, h)
    sw, sh = max(2, round(w * scale)), max(2, round(h * scale))
    dx, dy = math.sin(math.radians(angle)), -math.cos(math.radians(angle))
    length = abs(sw * dx) + abs(sh * dy) or 1
    cx, cy = (sw - 1) / 2, (sh - 1) / 2
    data = [
        max(0, min(255, round((0.5 + ((x - cx) * dx + (y - cy) * dy) / length) * 255)))
        for y in range(sh)
        for x in range(sw)
    ]
    mask = Image.new("L", (sw, sh))
    mask.putdata(data)
    return mask.resize(size, Image.Resampling.BILINEAR)


@lru_cache(maxsize=64)
def load_font(font: str | None, weight: str, size: int) -> ImageFont.FreeTypeFont:
    if font is None:
        name = "Inter-Bold.otf" if weight == "bold" else "Inter-Regular.otf"
        path = resources.files("apppreview") / "data" / "fonts" / name
        with resources.as_file(path) as real:
            return ImageFont.truetype(str(real), size)
    try:
        return ImageFont.truetype(font, size)
    except OSError:
        raise AppPreviewError(
            "font_not_found", f"Cannot load font '{font}'; give a .ttf/.otf path or an installed font file name"
        ) from None


@dataclass
class TextBlock:
    lines: list[str]
    font: ImageFont.FreeTypeFont
    style: TextStyle
    line_height: int
    truncated: bool

    @property
    def height(self) -> int:
        return self.line_height * len(self.lines) if self.lines else 0


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    lines = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if not line or font.getlength(candidate) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def fit_text(text: str, style: TextStyle, unit: int, width: float, max_height: float) -> TextBlock:
    """Wrap `text`, shrinking from style.size towards style.min_size until it fits."""
    size = style.size * unit
    floor = min(style.min_size, style.size) * unit
    while True:
        font = load_font(style.font, style.weight, max(1, round(size)))
        line_height = round(size * style.line_spacing)
        lines = _wrap(text, font, width)
        too_wide = any(font.getlength(line) > width for line in lines)
        fits = len(lines) <= style.max_lines and line_height * len(lines) <= max_height and not too_wide
        if fits or size <= floor:
            break
        size = max(floor, size * 0.94)

    truncated = not fits
    if len(lines) > style.max_lines:
        lines = lines[: style.max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    return TextBlock(lines, font, style, line_height, truncated)


def _draw_text(canvas: Image.Image, block: TextBlock, left: float, top: float, width: float) -> None:
    draw = ImageDraw.Draw(canvas)
    color = _rgba(block.style.color)
    # Center each line's glyphs vertically within its line box.
    ascent, descent = block.font.getmetrics()
    offset = (block.line_height - (ascent + descent)) / 2
    for i, line in enumerate(block.lines):
        line_width = block.font.getlength(line)
        x = {"left": left, "center": left + (width - line_width) / 2, "right": left + width - line_width}[
            block.style.align
        ]
        draw.text((x, top + i * block.line_height + offset), line, font=block.font, fill=color)


def _rounded_mask(size: Size, radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return mask


def compose(
    screenshot: Image.Image,
    size: Size,
    template: Template,
    caption: str | None = None,
    subtitle: str | None = None,
) -> tuple[Image.Image, float, list[str]]:
    """Return an RGB image of `size`, the scale applied to the screenshot, and warnings."""
    warnings: list[str] = []
    w, h = size
    unit = min(w, h)
    pad = template.padding * unit
    gap = template.gap * unit
    text_width = w - 2 * pad
    canvas = render_background(template.background, size)

    budget = TEXT_BUDGET * h
    blocks = []
    if caption:
        blocks.append(fit_text(caption, template.caption, unit, text_width, budget))
        budget -= blocks[-1].height
    if subtitle:
        blocks.append(fit_text(subtitle, template.subtitle, unit, text_width, max(budget, 0)))
    for block in blocks:
        if block.truncated:
            warnings.append("Text did not fit at the minimum size and was truncated; shorten it")
    line_gap = round(0.3 * blocks[0].line_height) if len(blocks) == 2 else 0
    text_height = sum(b.height for b in blocks) + line_gap

    if template.caption_position == "top":
        text_top = pad
        region_top, region_bottom = pad + (text_height + gap if blocks else 0), h - pad
    else:
        text_top = h - pad - text_height
        region_top, region_bottom = pad, h - pad - (text_height + gap if blocks else 0)

    y = text_top
    for block in blocks:
        _draw_text(canvas, block, pad, y, text_width)
        y += block.height + line_gap

    style = template.screenshot
    shot = ImageOps.exif_transpose(screenshot).convert("RGBA")
    max_w, max_h = style.max_width * w, max(1, region_bottom - region_top)
    scale = min(max_w / shot.width, max_h / shot.height)
    if scale > 1.5:
        warnings.append(f"Screenshot is upscaled {scale:.2f}x; output may look blurry")
    shot_size = (max(1, round(shot.width * scale)), max(1, round(shot.height * scale)))
    shot = shot.resize(shot_size, Image.Resampling.LANCZOS)
    x = round((w - shot_size[0]) / 2)
    y = round(region_top + (max_h - shot_size[1]) / 2)
    radius = min(round(style.corner_radius * unit), min(shot_size) // 2)
    mask = _rounded_mask(shot_size, radius)

    if style.shadow:
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        offset = round(style.shadow.offset * unit)
        layer.paste(Image.new("RGBA", shot_size, _rgba(style.shadow.color)), (x, y + offset), mask)
        blur = style.shadow.blur * unit
        canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)) if blur else layer)

    # Transparent pixels in the screenshot show the background, as they would on screen.
    shot_alpha = Image.composite(shot.getchannel("A"), Image.new("L", shot_size, 0), mask)
    shot.putalpha(shot_alpha)
    canvas.alpha_composite(shot, (x, y))

    if style.border:
        width = max(1, round(style.border_width * unit))
        ImageDraw.Draw(canvas).rounded_rectangle(
            (x, y, x + shot_size[0] - 1, y + shot_size[1] - 1), radius, outline=_rgba(style.border), width=width
        )

    flat = Image.new("RGB", size, (255, 255, 255))
    flat.paste(canvas, mask=canvas.getchannel("A"))
    return flat, scale, warnings
