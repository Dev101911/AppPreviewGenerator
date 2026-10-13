"""Device frames: wrap a screenshot in a phone/tablet bezel.

Built-in frames are drawn to fit each screenshot exactly. Custom frames are PNG images
with a transparent screen area, optionally with a manifest giving the screen rectangle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from apppreview.errors import AppPreviewError

BUILTIN_FRAMES = {
    "auto": "Phone or tablet, chosen from the screenshot's aspect ratio",
    "phone": "Modern phone with thin bezels and a Dynamic Island-style cutout",
    "tablet": "Tablet with even bezels",
}


@dataclass(frozen=True)
class FrameStyle:
    name: str = "auto"
    color: tuple[int, int, int, int] = (28, 28, 30, 255)
    island: bool = True


def _rounded(size, radius, fill=255) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=fill)
    return mask


def _lighten(color, amount):
    return tuple(min(255, c + amount) for c in color[:3]) + (color[3],)


Rect = tuple[int, int, int, int]  # x, y, width, height


@dataclass
class FrameLayer:
    """A device image whose screen is transparent, and where the screen is."""

    device: Image.Image  # RGBA
    screen: Rect
    mask: Image.Image  # L, screen-sized; 255 where content shows


def _builtin(content_size: tuple[int, int], kind: str, style: FrameStyle) -> FrameLayer:
    sw, sh = content_size
    short = min(sw, sh)
    if kind == "phone":
        bezel, screen_radius = round(0.04 * short), round(0.12 * short)
    else:
        bezel, screen_radius = round(0.055 * short), round(0.035 * short)
    w, h = sw + 2 * bezel, sh + 2 * bezel
    outer_radius = screen_radius + bezel

    device = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    body = Image.new("RGBA", (w, h), style.color)
    device.paste(body, (0, 0), _rounded((w, h), outer_radius))
    # A thin lighter rim makes dark bodies read as metal against dark backgrounds.
    rim = max(1, round(0.004 * short))
    ImageDraw.Draw(device).rounded_rectangle(
        (0, 0, w - 1, h - 1), outer_radius, outline=_lighten(style.color, 60), width=rim
    )
    screen_mask = _rounded((sw, sh), screen_radius)
    device.paste(Image.new("RGBA", (sw, sh), (0, 0, 0, 0)), (bezel, bezel), screen_mask)

    if kind == "phone" and style.island:
        portrait = sh >= sw
        long_w, thick = round(0.3 * short), round(0.09 * short)
        offset = round(0.03 * short)
        if portrait:
            box = ((w - long_w) // 2, bezel + offset, (w + long_w) // 2, bezel + offset + thick)
        else:  # landscape: the cutout sits on the left edge
            box = (bezel + offset, (h - long_w) // 2, bezel + offset + thick, (h + long_w) // 2)
        ImageDraw.Draw(device).rounded_rectangle(box, thick // 2, fill=(0, 0, 0, 255))
    return FrameLayer(device, (bezel, bezel, sw, sh), screen_mask)


def _load_manifest(path: Path) -> tuple[Path, tuple[int, int, int, int] | None]:
    data = json.loads(path.read_text("utf-8")) if path.suffix == ".json" else yaml.safe_load(path.read_text("utf-8"))
    if not isinstance(data, dict) or "image" not in data:
        raise AppPreviewError("invalid_frame", f"Frame manifest {path} needs an 'image' entry")
    screen = data.get("screen")
    if screen is not None:
        if not (isinstance(screen, list) and len(screen) == 4 and all(isinstance(v, int) for v in screen)):
            raise AppPreviewError("invalid_frame", f"'screen' in {path} must be [x, y, width, height]")
        screen = tuple(screen)
    return path.parent / data["image"], screen


def find_screen(frame: Image.Image) -> tuple[tuple[int, int, int, int], Image.Image]:
    """Find the transparent screen area enclosed by the bezel, starting from the center.

    Returns (x, y, width, height) and a mask of the screen area at that size.
    """
    w, h = frame.size
    transparent = frame.getchannel("A").point(lambda a: 255 if a < 128 else 0)
    if transparent.getpixel((w // 2, h // 2)) != 255:
        raise AppPreviewError(
            "invalid_frame",
            "The frame image has no transparent screen at its center; add a manifest with "
            "'screen: [x, y, width, height]'",
        )
    ImageDraw.floodfill(transparent, (w // 2, h // 2), 128)
    region = transparent.point(lambda v: 255 if v == 128 else 0)
    x0, y0, x1, y1 = region.getbbox()
    if x0 == 0 or y0 == 0 or x1 == w or y1 == h:
        raise AppPreviewError("invalid_frame", "The frame's transparent screen area is not enclosed by the bezel")
    # Grow slightly so the screenshot also sits under the bezel's anti-aliased inner edge.
    mask = region.filter(ImageFilter.MaxFilter(5)).crop((x0, y0, x1, y1))
    return (x0, y0, x1 - x0, y1 - y0), mask


def _custom(path: Path) -> FrameLayer:
    screen = None
    if path.suffix.lower() in (".json", ".yaml", ".yml"):
        path, screen = _load_manifest(path)
    try:
        with Image.open(path) as img:
            frame = img.convert("RGBA")
    except (FileNotFoundError, OSError) as exc:
        raise AppPreviewError("frame_not_found", f"Cannot read frame image {path}: {exc}") from None
    if screen:
        x, y, sw, sh = screen
        # Cut the screen out so content placed underneath shows through.
        frame.paste(Image.new("RGBA", (sw, sh), (0, 0, 0, 0)), (x, y))
        return FrameLayer(frame, screen, Image.new("L", (sw, sh), 255))
    rect, mask = find_screen(frame)
    return FrameLayer(frame, rect, mask)


def frame_layer(content_size: tuple[int, int], style: FrameStyle) -> FrameLayer:
    """The frame for content of `content_size`: built-in frames are drawn to fit it exactly;
    custom frames have a fixed screen that content is cropped to fill."""
    name = style.name
    if name == "auto":
        ratio = max(content_size) / min(content_size)
        name = "phone" if ratio > 1.6 else "tablet"
    if name in BUILTIN_FRAMES:
        return _builtin(content_size, name, style)
    path = Path(name)
    if path.suffix.lower() not in (".png", ".json", ".yaml", ".yml"):
        raise AppPreviewError(
            "unknown_frame",
            f"Unknown frame '{name}'. Built-in frames: {', '.join(BUILTIN_FRAMES)}; or give a .png/.yaml path.",
        )
    return _custom(path)


def aspect_warning(content_size: tuple[int, int], screen: Rect) -> str | None:
    sw, sh = screen[2], screen[3]
    if abs(content_size[0] / content_size[1] - sw / sh) > 0.03 * (sw / sh):
        return f"Content aspect ratio differs from the frame's screen ({sw}x{sh}); it was cropped to fit"
    return None


def apply_frame(shot: Image.Image, style: FrameStyle, warnings: list[str]) -> Image.Image:
    """Return an RGBA image of the screenshot inside its device frame."""
    shot = ImageOps.exif_transpose(shot).convert("RGBA")
    layer = frame_layer(shot.size, style)
    warning = aspect_warning(shot.size, layer.screen)
    if warning:
        warnings.append(warning)
    x, y, sw, sh = layer.screen
    fitted = ImageOps.fit(shot, (sw, sh), Image.Resampling.LANCZOS)
    device = Image.new("RGBA", layer.device.size, (0, 0, 0, 0))
    device.paste(fitted, (x, y), layer.mask)
    device.alpha_composite(layer.device)
    return device
