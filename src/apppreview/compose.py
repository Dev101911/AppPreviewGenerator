"""Render a screenshot onto a template canvas: background, captions, styled screenshot."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps, features

from apppreview.errors import AppPreviewError
from apppreview.frames import FrameStyle, aspect_warning, frame_layer
from apppreview.locales import is_rtl, language
from apppreview.specs import Size
from apppreview.template import Background, BadgeStyle, Template, TextStyle, weight_value

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
def load_font(font: str | None, weight: str | int, size: int) -> ImageFont.FreeTypeFont:
    """Load `font` (default: the bundled Inter variable font) at `weight` if it has a weight axis."""
    if font is None:
        path = resources.files("apppreview") / "data" / "fonts" / "InterVariable.ttf"
        with resources.as_file(path) as real:
            loaded = ImageFont.truetype(str(real), size)
    else:
        try:
            loaded = ImageFont.truetype(font, size)
        except OSError:
            raise AppPreviewError(
                "font_not_found", f"Cannot load font '{font}'; give a .ttf/.otf path or an installed font file name"
            ) from None
    _set_weight(loaded, weight_value(weight))
    return loaded


def _set_weight(font: ImageFont.FreeTypeFont, weight: int) -> None:
    """Set the weight axis of a variable font; static fonts keep their own weight."""
    try:
        axes = font.get_variation_axes()
    except OSError:  # not a variable font (or FreeType without variation support)
        return
    names = [axis["name"] for axis in axes]
    if b"Weight" not in names and "Weight" not in names:
        return
    values = [
        max(axis["minimum"], min(axis["maximum"], weight)) if axis["name"] in (b"Weight", "Weight") else axis["default"]
        for axis in axes
    ]
    font.set_variation_by_axes(values)


HAS_RAQM = features.check("raqm")


def layout_options(locale: str | None) -> dict:
    """Keyword arguments for Pillow text calls: shaping language and paragraph direction.

    Needs libraqm (bundled with Pillow wheels); without it, text is laid out left to right.
    """
    if not locale or not HAS_RAQM:
        return {}
    options = {"language": language(locale)}
    if is_rtl(locale):
        options["direction"] = "rtl"
    return options


@dataclass
class TextBlock:
    lines: list[str]
    font: ImageFont.FreeTypeFont
    style: TextStyle
    line_height: int
    truncated: bool
    layout: dict = field(default_factory=dict)
    rtl: bool = False

    @property
    def height(self) -> int:
        return self.line_height * len(self.lines) if self.lines else 0


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: float, layout: dict) -> list[str]:
    """Greedy word wrap. Words wider than a line (e.g. CJK text, which has no spaces)
    are broken between characters."""

    def length(s: str) -> float:
        return font.getlength(s, **layout)

    lines = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if not line or length(candidate) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
            while length(line) > width and len(line) > 1:
                cut = max(1, next((i for i in range(len(line), 0, -1) if length(line[:i]) <= width), 1))
                lines.append(line[:cut])
                line = line[cut:].lstrip()
        lines.append(line)
    return lines


@lru_cache(maxsize=64)
def _notdef(font: ImageFont.FreeTypeFont) -> tuple:
    mask = font.getmask("\U0010fffd")  # private-use code point no font defines
    return mask.size, bytes(mask)


def missing_glyphs(font: ImageFont.FreeTypeFont, text: str) -> list[str]:
    """Characters in `text` that `font` would draw as the 'missing glyph' box."""
    notdef = _notdef(font)
    missing = []
    for ch in dict.fromkeys(text):
        if ch.isspace() or ch == "…":
            continue
        mask = font.getmask(ch)
        if (mask.size, bytes(mask)) == notdef:
            missing.append(ch)
    return missing


def text_warnings(blocks: list[TextBlock]) -> list[str]:
    warnings = []
    if any(b.truncated for b in blocks):
        warnings.append("Text did not fit at the minimum size and was truncated; shorten it")
    missing = list(dict.fromkeys(ch for b in blocks for ch in missing_glyphs(b.font, "".join(b.lines))))
    if missing:
        sample = "".join(missing[:8])
        warnings.append(
            f"The font has no glyphs for {len(missing)} character(s) ({sample}); they render as boxes. "
            "Set a font that covers this language, e.g. with locale_overrides in app-preview.yaml"
        )
    return warnings


def fit_text(
    text: str, style: TextStyle, unit: int, width: float, max_height: float, locale: str | None = None
) -> TextBlock:
    """Wrap `text`, shrinking from style.size towards style.min_size until it fits."""
    layout = layout_options(locale)
    size = style.size * unit
    floor = min(style.min_size, style.size) * unit
    while True:
        font = load_font(style.font, style.weight, max(1, round(size)))
        line_height = round(size * style.line_spacing)
        lines = _wrap(text, font, width, layout)
        too_wide = any(font.getlength(line, **layout) > width for line in lines)
        fits = len(lines) <= style.max_lines and line_height * len(lines) <= max_height and not too_wide
        if fits or size <= floor:
            break
        size = max(floor, size * 0.94)

    truncated = not fits
    if len(lines) > style.max_lines:
        lines = lines[: style.max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    return TextBlock(lines, font, style, line_height, truncated, layout, is_rtl(locale))


def _draw_text(canvas: Image.Image, block: TextBlock, left: float, top: float, width: float) -> None:
    draw = ImageDraw.Draw(canvas)
    color = _rgba(block.style.color)
    # Center each line's glyphs vertically within its line box.
    ascent, descent = block.font.getmetrics()
    offset = (block.line_height - (ascent + descent)) / 2
    align = _align(block.style.align, block.rtl)
    for i, line in enumerate(block.lines):
        line_width = block.font.getlength(line, **block.layout)
        x = {"left": left, "center": left + (width - line_width) / 2, "right": left + width - line_width}[align]
        draw.text((x, top + i * block.line_height + offset), line, font=block.font, fill=color, **block.layout)


def _rounded_mask(size: Size, radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return mask


@dataclass
class Layers:
    """A template laid out for content of a given size.

    Content (a screenshot, or each frame of a video) goes inside `screen`, clipped by
    `mask`, between `under` (background, text, shadow) and `over` (device frame or border).
    """

    under: Image.Image  # RGBA, canvas size
    over: Image.Image | None  # RGBA, canvas size
    screen: tuple[int, int, int, int]  # x, y, width, height on the canvas
    mask: Image.Image  # L, screen size
    scale: float  # content pixels -> canvas pixels
    warnings: list[str]


def _align(align: str, rtl: bool) -> str:
    """Mirror left/right alignment for right-to-left scripts."""
    return {"left": "right", "right": "left"}.get(align, align) if rtl else align


def text_column(style: TextStyle, w: int, pad: float, rtl: bool = False) -> tuple[float, float]:
    """(left, width) of the text column: the padded width, narrowed by style.max_width."""
    full = w - 2 * pad
    width = min(full, style.max_width * w) if style.max_width else full
    align = _align(style.align, rtl)
    left = {"left": pad, "center": (w - width) / 2, "right": w - pad - width}[align]
    return left, width


@dataclass
class Badge:
    text: str
    font: ImageFont.FreeTypeFont
    style: BadgeStyle
    width: int
    height: int
    layout: dict = field(default_factory=dict)


def fit_badge(text: str, style: BadgeStyle, unit: int, max_width: float, locale: str | None = None) -> Badge:
    """Size a pill around `text`, shrinking the font if the pill is wider than `max_width`."""
    layout = layout_options(locale)
    if style.uppercase:
        text = text.upper()
    size = style.size * unit
    for _ in range(2):
        font = load_font(style.font, style.weight, max(1, round(size)))
        pad = style.padding * size
        width = font.getlength(text, **layout) + 2 * pad
        if width <= max_width:
            break
        size *= max_width / width
    return Badge(text, font, style, round(min(width, max_width)), round(size * 1.8), layout)


def _draw_badge(canvas: Image.Image, badge: Badge, left: float, top: float) -> None:
    box = (round(left), round(top), round(left) + badge.width - 1, round(top) + badge.height - 1)
    draw = ImageDraw.Draw(canvas)
    radius = round(badge.style.radius * badge.height)
    draw.rounded_rectangle(box, radius, fill=_rgba(badge.style.background))
    center = (left + badge.width / 2, top + badge.height / 2)
    draw.text(center, badge.text, font=badge.font, fill=_rgba(badge.style.color), anchor="mm", **badge.layout)


def layout(
    template: Template,
    size: Size,
    content_size: Size,
    caption: str | None = None,
    subtitle: str | None = None,
    locale: str | None = None,
    badge: str | None = None,
) -> Layers:
    warnings: list[str] = []
    w, h = size
    unit = min(w, h)
    pad = template.padding * unit
    gap = template.gap * unit
    rtl = is_rtl(locale)
    canvas = render_background(template.background, size)

    budget = TEXT_BUDGET * h
    pill = None
    badge_space = 0
    if badge:
        _, badge_width = text_column(template.caption, w, pad, rtl)
        pill = fit_badge(badge, template.badge, unit, badge_width, locale)
        badge_space = pill.height + round(template.badge.gap * unit)
        budget -= badge_space
        missing = missing_glyphs(pill.font, pill.text)
        if missing:
            warnings.append(f"The badge font has no glyphs for {''.join(missing[:8])}; they render as boxes")
    blocks = []
    columns = []
    for text, style in ((caption, template.caption), (subtitle, template.subtitle)):
        if text:
            columns.append(text_column(style, w, pad, rtl))
            blocks.append(fit_text(text, style, unit, columns[-1][1], max(budget, 0), locale))
            budget -= blocks[-1].height
    warnings.extend(text_warnings(blocks))
    line_gap = round(0.3 * blocks[0].line_height) if len(blocks) == 2 else 0
    text_height = sum(b.height for b in blocks) + line_gap
    if pill:
        text_height = text_height + badge_space if blocks else pill.height
    has_text = bool(blocks or pill)

    if template.caption_position == "top":
        text_top = pad
        region_top, region_bottom = pad + (text_height + gap if has_text else 0), h - pad
    else:
        text_top = h - pad - text_height
        region_top, region_bottom = pad, h - pad - (text_height + gap if has_text else 0)

    y = text_top
    if pill and template.badge.position == "above":
        _place_badge(canvas, pill, template.caption, w, pad, rtl, y)
        y += badge_space
    for block, (left, width) in zip(blocks, columns, strict=True):
        _draw_text(canvas, block, left, y, width)
        y += block.height + line_gap
    if pill and template.badge.position == "below":
        _place_badge(canvas, pill, template.caption, w, pad, rtl, text_top + text_height - pill.height)

    style = template.screenshot
    frame = None
    if style.frame:
        frame = frame_layer(content_size, FrameStyle(style.frame.name, _rgba(style.frame.color), style.frame.island))
        warning = aspect_warning(content_size, frame.screen)
        if warning:
            warnings.append(warning)
        body_size = frame.device.size
    else:
        body_size = content_size

    # Scale the device (or bare screenshot) to fit the region below/above the text.
    max_w, max_h = style.max_width * w, max(1, region_bottom - region_top)
    body_scale = min(max_w / body_size[0], max_h / body_size[1])
    scaled = (max(1, round(body_size[0] * body_scale)), max(1, round(body_size[1] * body_scale)))
    x = round((w - scaled[0]) / 2)
    y = round(region_top + (max_h - scaled[1]) / 2)

    over = None
    if frame:
        device = frame.device.resize(scaled, Image.Resampling.LANCZOS)
        fx, fy, fw, fh = frame.screen
        screen = (
            x + round(fx * body_scale),
            y + round(fy * body_scale),
            max(1, round(fw * body_scale)),
            max(1, round(fh * body_scale)),
        )
        mask = frame.mask.resize(screen[2:], Image.Resampling.LANCZOS)
        # Silhouette for the shadow: the device body plus its (transparent) screen.
        silhouette = device.getchannel("A")
        silhouette.paste(255, (screen[0] - x, screen[1] - y), mask)
        over = Image.new("RGBA", size, (0, 0, 0, 0))
        over.alpha_composite(device, (x, y))
        content_scale = screen[2] / content_size[0]
    else:
        screen = (x, y, *scaled)
        radius = min(round(style.corner_radius * unit), min(scaled) // 2)
        mask = silhouette = _rounded_mask(scaled, radius)
        if style.border:
            over = Image.new("RGBA", size, (0, 0, 0, 0))
            width = max(1, round(style.border_width * unit))
            ImageDraw.Draw(over).rounded_rectangle(
                (x, y, x + scaled[0] - 1, y + scaled[1] - 1), radius, outline=_rgba(style.border), width=width
            )
        content_scale = body_scale

    if content_scale > 1.5:
        warnings.append(f"Screenshot is upscaled {content_scale:.2f}x; output may look blurry")

    if style.shadow:
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        offset = round(style.shadow.offset * unit)
        layer.paste(Image.new("RGBA", scaled, _rgba(style.shadow.color)), (x, y + offset), silhouette)
        blur = style.shadow.blur * unit
        canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)) if blur else layer)

    return Layers(canvas, over, screen, mask, content_scale, warnings)


def _place_badge(canvas: Image.Image, pill: Badge, style: TextStyle, w: int, pad: float, rtl: bool, top: float) -> None:
    """Draw the badge lined up with the caption column."""
    left, width = text_column(style, w, pad, rtl)
    x = {"left": left, "center": left + (width - pill.width) / 2, "right": left + width - pill.width}
    _draw_badge(canvas, pill, x[_align(style.align, rtl)], top)


def place(layers: Layers, content: Image.Image) -> Image.Image:
    """Put `content` into the screen area and return the flattened RGB image."""
    x, y, sw, sh = layers.screen
    shot = ImageOps.fit(content.convert("RGBA"), (sw, sh), Image.Resampling.LANCZOS)
    # Transparent pixels in the screenshot show the background, as they would on screen.
    shot.putalpha(ImageChops.multiply(shot.getchannel("A"), layers.mask))
    canvas = layers.under.copy()
    canvas.alpha_composite(shot, (x, y))
    if layers.over:
        canvas.alpha_composite(layers.over)
    flat = Image.new("RGB", canvas.size, (255, 255, 255))
    flat.paste(canvas, mask=canvas.getchannel("A"))
    return flat


def compose(
    screenshot: Image.Image,
    size: Size,
    template: Template,
    caption: str | None = None,
    subtitle: str | None = None,
    locale: str | None = None,
    badge: str | None = None,
) -> tuple[Image.Image, float, list[str]]:
    """Return an RGB image of `size`, the scale applied to the screenshot, and warnings."""
    shot = ImageOps.exif_transpose(screenshot)
    layers = layout(template, size, shot.size, caption, subtitle, locale, badge)
    return place(layers, shot), layers.scale, layers.warnings
