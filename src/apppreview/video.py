"""App preview videos: inspect with ffprobe, validate, and convert with ffmpeg.

Needs `ffmpeg` and `ffprobe` on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from apppreview.errors import AppPreviewError
from apppreview.specs import Size, Target, video_rules
from apppreview.validate import ERROR, WARNING, FileReport, Issue

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
# Encoding speed/quality trade-off; tests use "ultrafast".
X264_PRESET = os.environ.get("APPPREVIEW_X264_PRESET", "medium")
# H.264 Level 4.0 limits: 8192 macroblocks per frame, 245760 per second.
LEVEL_40_MAX_MBS = 8192


def is_video(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise AppPreviewError(
            "tool_not_found", f"'{name}' not found. Install ffmpeg (e.g. `brew install ffmpeg` or `apt install ffmpeg`)"
        )
    return path


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float
    fps: float
    codec: str
    profile: str
    level: int | None
    pix_fmt: str
    bit_rate: int | None
    audio: list[dict] = field(default_factory=list)  # codec, channels, sample_rate, bit_rate


def probe(path: str | Path) -> VideoInfo:
    result = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AppPreviewError(
            "unreadable", f"ffprobe cannot read {path}: {result.stderr.decode(errors='replace').strip()}"
        )
    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise AppPreviewError("unreadable", f"{path} has no video stream")
    width, height = int(video["width"]), int(video["height"])
    rotation = _rotation(video)
    if rotation in (90, 270):
        width, height = height, width
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    fps = float(Fraction(rate)) if rate != "0/0" else 0.0
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    bit_rate = video.get("bit_rate") or data.get("format", {}).get("bit_rate")
    audio = [
        {
            "codec": s.get("codec_name", ""),
            "channels": int(s.get("channels", 0)),
            "sample_rate": int(s.get("sample_rate", 0)),
            "bit_rate": int(s["bit_rate"]) if s.get("bit_rate") else None,
        }
        for s in data.get("streams", [])
        if s.get("codec_type") == "audio"
    ]
    level = video.get("level")
    return VideoInfo(
        width, height, duration, fps, video.get("codec_name", ""), video.get("profile", ""),
        int(level) if isinstance(level, int) and level > 0 else None, video.get("pix_fmt", ""),
        int(bit_rate) if bit_rate else None, audio,
    )  # fmt: skip


def _rotation(stream: dict) -> int:
    rotate = stream.get("tags", {}).get("rotate")
    if rotate is None:
        for side in stream.get("side_data_list", []):
            if "rotation" in side:
                rotate = side["rotation"]
    return abs(int(float(rotate or 0))) % 360


def validate_video(path: str | Path, target: Target) -> FileReport:
    path = Path(path)
    report = FileReport(path=str(path))
    if not path.is_file():
        report.issues.append(Issue(ERROR, "not_found", f"File not found: {path}"))
        return report
    report.bytes = path.stat().st_size
    try:
        info = probe(path)
    except AppPreviewError as exc:
        if exc.code == "tool_not_found":
            raise
        report.issues.append(Issue(ERROR, "unreadable", exc.message))
        return report
    report.width, report.height = info.width, info.height
    report.format = path.suffix.lower().lstrip(".")
    report.mode = info.codec
    rules = video_rules()
    hint = f"Run `apppreview generate {path} --target {target.id}` to produce a compliant copy."
    add = report.issues.append

    if report.format not in target.formats:
        add(Issue(ERROR, "format", f"Container is .{report.format}; allowed: {', '.join(target.formats)}", hint))
    if info.codec == "h264":
        codec_rules = rules["h264"]
        if info.profile not in codec_rules["profiles"]:
            add(Issue(ERROR, "video_profile", f"H.264 profile is {info.profile}; use High or lower", hint))
        if info.level and info.level > codec_rules["max_level"]:
            add(Issue(ERROR, "video_profile", f"H.264 level is {info.level / 10:g}; the maximum is 4.0", hint))
        if info.bit_rate and info.bit_rate > codec_rules["target_mbps"][1] * 1.5e6:
            add(
                Issue(WARNING, "bitrate", f"Video bit rate is {info.bit_rate / 1e6:.1f} Mbps; Apple targets 10-12 Mbps")
            )
    elif info.codec == "prores":
        codec_rules = rules["prores"]
        if report.format != "mov":
            add(Issue(ERROR, "format", "ProRes previews must be .mov files", hint))
        if info.profile not in codec_rules["profiles"]:
            add(
                Issue(
                    ERROR,
                    "video_profile",
                    f"ProRes profile is {info.profile or 'unknown'}; only 422 HQ is accepted",
                    hint,
                )
            )
    else:
        codec_rules = rules["h264"]
        add(Issue(ERROR, "video_codec", f"Video codec is {info.codec}; use H.264 or ProRes 422 HQ", hint))

    if (info.width, info.height) not in target.sizes:
        expected = ", ".join(f"{w}x{h}" for w, h in target.sizes)
        add(Issue(ERROR, "dimensions", f"{info.width}x{info.height} is not accepted; expected one of {expected}", hint))
    if info.duration < rules["min_seconds"] - 0.05:
        add(Issue(ERROR, "duration", f"Video is {info.duration:.1f}s; minimum is {rules['min_seconds']}s",
                  "Record a longer clip, or `apppreview generate ... --extend` to hold the last frame."))  # fmt: skip
    if info.duration > rules["max_seconds"] + 0.05:
        add(Issue(ERROR, "duration", f"Video is {info.duration:.1f}s; maximum is {rules['max_seconds']}s",
                  f"`apppreview generate ... --start SECONDS` trims to the best {rules['max_seconds']}s."))  # fmt: skip
    if info.fps > rules["max_fps"] + 0.01:
        add(Issue(ERROR, "frame_rate", f"Frame rate is {info.fps:.2f} fps; maximum is {rules['max_fps']}", hint))

    stereo = [a for a in info.audio if a["channels"] == 2]
    mono = [a for a in info.audio if a["channels"] == 1]
    if not info.audio:
        add(Issue(ERROR, "audio", "No audio track; previews need stereo audio (silence is fine)", hint))
    elif not stereo and len(mono) != 2:
        add(Issue(ERROR, "audio", "Audio must be one stereo track or two mono tracks", hint))
    for track in info.audio:
        if track["codec"] not in codec_rules["audio_codecs"]:
            add(Issue(ERROR, "audio", f"Audio codec is {track['codec']}; use AAC", hint))
        if track["sample_rate"] not in rules["sample_rates"]:
            add(Issue(ERROR, "audio", f"Audio sample rate is {track['sample_rate']} Hz; use 44.1 or 48 kHz", hint))

    if target.max_bytes and report.bytes > target.max_bytes:
        add(Issue(ERROR, "file_size", f"File is {report.bytes:,} bytes; maximum is {target.max_bytes:,} bytes"))
    return report


def needs_prores(size: Size) -> bool:
    w, h = size
    return ((w + 15) // 16) * ((h + 15) // 16) > LEVEL_40_MAX_MBS


def video_extension(size: Size) -> str:
    return "mov" if needs_prores(size) else "mp4"


def choose_video_size(target: Target, width: int, height: int) -> Size:
    portrait = height >= width
    same = [s for s in target.sizes if (s[1] >= s[0]) == portrait] or list(target.sizes)
    aspect = width / height
    return min(same, key=lambda s: abs(s[0] / s[1] - aspect))


@dataclass
class VideoPlan:
    """What a conversion will do, so a dry run can report it without running ffmpeg."""

    size: Size
    start: float
    duration: float
    extend: float  # seconds of frozen last frame appended
    warnings: list[str]


def plan_video(info: VideoInfo, target: Target, size: Size | None, start: float, extend: bool) -> VideoPlan:
    rules = video_rules()
    size = size or choose_video_size(target, info.width, info.height)
    warnings = []
    available = max(0.0, info.duration - start)
    if available <= 0:
        raise AppPreviewError("invalid_start", f"--start {start}s is past the end of the {info.duration:.1f}s video")
    duration = min(available, rules["max_seconds"])
    if available > rules["max_seconds"]:
        warnings.append(f"Trimmed to {rules['max_seconds']}s (from {start:g}s); use --start to pick the segment")
    pad = 0.0
    if duration < rules["min_seconds"]:
        if not extend:
            raise AppPreviewError(
                "video_too_short",
                f"Video is {duration:.1f}s; previews need at least {rules['min_seconds']}s. "
                "Record a longer clip or pass --extend to hold the last frame.",
            )
        pad = rules["min_seconds"] - duration
        warnings.append(f"Held the last frame for {pad:.1f}s to reach {rules['min_seconds']}s")
    if info.fps > rules["max_fps"] + 0.01:
        warnings.append(f"Frame rate reduced from {info.fps:.0f} to {rules['max_fps']} fps")
    if not info.audio:
        warnings.append("Added a silent stereo audio track (previews need audio)")
    if needs_prores(size):
        warnings.append("Encoded as ProRes 422 HQ: H.264 Level 4.0 cannot hold this resolution")
    return VideoPlan(size, start, duration, pad, warnings)


def _hex(color: tuple[int, int, int]) -> str:
    return "0x{:02x}{:02x}{:02x}".format(*color)


def convert(
    src: str | Path,
    dest: str | Path,
    plan: VideoPlan,
    info: VideoInfo,
    *,
    mode: str = "fit",
    background: tuple[int, int, int] = (0, 0, 0),
    layers=None,
) -> None:
    """Run ffmpeg. `layers` (compose.Layers) places the video inside a template."""
    rules = video_rules()
    w, h = plan.size
    total = plan.duration + plan.extend
    fps = f"fps={rules['max_fps']}," if info.fps > rules["max_fps"] + 0.01 else ""
    extend = f",tpad=stop_mode=clone:stop_duration={plan.extend:.3f}" if plan.extend else ""
    args = [
        _tool("ffmpeg"),
        "-v",
        "error",
        "-y",
        "-ss",
        f"{plan.start:.3f}",
        "-t",
        f"{plan.duration:.3f}",
        "-i",
        str(src),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        if layers is None:
            scale = {
                "fit": (
                    f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={_hex(background)}"
                ),
                "fill": f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                "stretch": f"scale={w}:{h}",
            }[mode]
            graph = f"[0:v]{fps}{scale},setsar=1{extend},format=yuv420p[vout]"
            next_input = 1
        else:
            x, y, sw, sh = layers.screen
            under, mask = Path(tmp) / "under.png", Path(tmp) / "mask.png"
            layers.under.save(under)
            layers.mask.save(mask)
            args += ["-loop", "1", "-i", str(under), "-loop", "1", "-i", str(mask)]
            graph = (
                f"[0:v]{fps}scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},setsar=1{extend},"
                f"format=rgba[v];[2:v]format=gray,scale={sw}:{sh}[m];[v][m]alphamerge[vm];"
                f"[1:v]format=rgba[bg];[bg][vm]overlay={x}:{y}:shortest=1[t]"
            )
            last = "t"
            next_input = 3
            if layers.over is not None:
                over = Path(tmp) / "over.png"
                layers.over.save(over)
                args += ["-loop", "1", "-i", str(over)]
                graph += ";[t][3:v]overlay=0:0:shortest=1[o]"
                last = "o"
                next_input = 4
            graph += f";[{last}]format=yuv420p,setsar=1[vout]"

        if info.audio:
            audio_map = ["-map", "0:a:0"]
            if plan.extend:
                graph += f";[0:a:0]apad=pad_dur={plan.extend:.3f}[aout]"
                audio_map = ["-map", "[aout]"]
        else:
            args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            audio_map = ["-map", f"{next_input}:a"]

        args += ["-filter_complex", graph, "-map", "[vout]", *audio_map, "-t", f"{total:.3f}"]
        if needs_prores(plan.size):
            args += ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-vendor", "apl0"]
        else:
            args += [
                "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.0", "-pix_fmt", "yuv420p",
                "-b:v", "11M", "-maxrate", "12M", "-bufsize", "24M", "-preset", X264_PRESET,
                "-movflags", "+faststart",
            ]  # fmt: skip
        args += ["-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2", "-r", str(min(30, round(info.fps) or 30))]
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        args.append(str(dest))
        result = subprocess.run(args, capture_output=True, check=False)
        if result.returncode != 0:
            raise AppPreviewError(
                "ffmpeg_failed", f"ffmpeg failed: {result.stderr.decode(errors='replace').strip()[-2000:]}"
            )


def slideshow(slides: list[Path], dest: str | Path, size: Size, seconds: float, transition: float) -> None:
    """Join same-sized slide images into a preview with crossfades and a silent audio track."""
    n = len(slides)
    total = n * seconds - (n - 1) * transition
    args = [_tool("ffmpeg"), "-v", "error", "-y"]
    for slide in slides:
        args += ["-loop", "1", "-t", f"{seconds:.3f}", "-framerate", "30", "-i", str(slide)]
    args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    parts = [f"[{i}:v]format=yuv420p,setsar=1[s{i}]" for i in range(n)]
    last = "s0"
    for i in range(1, n):
        offset = i * (seconds - transition)
        parts.append(f"[{last}][s{i}]xfade=transition=fade:duration={transition:.3f}:offset={offset:.3f}[x{i}]")
        last = f"x{i}"
    args += ["-filter_complex", ";".join(parts), "-map", f"[{last}]", "-map", f"{n}:a", "-t", f"{total:.3f}"]
    if needs_prores(size):
        args += ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-vendor", "apl0"]
    else:
        args += [
            "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.0", "-pix_fmt", "yuv420p",
            "-b:v", "11M", "-maxrate", "12M", "-bufsize", "24M", "-preset", X264_PRESET, "-movflags", "+faststart",
        ]  # fmt: skip
    args += ["-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2", "-r", "30", str(dest)]
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(args, capture_output=True, check=False)
    if result.returncode != 0:
        raise AppPreviewError(
            "ffmpeg_failed", f"ffmpeg failed: {result.stderr.decode(errors='replace').strip()[-2000:]}"
        )


def produce_video(
    src: str | Path,
    target: Target,
    dest_stem: Path,
    *,
    size: Size | None = None,
    mode: str = "fit",
    background: str = "#000000",
    template=None,
    caption: str | None = None,
    subtitle: str | None = None,
    locale: str | None = None,
    start: float = 0.0,
    extend: bool = False,
    dry_run: bool = False,
) -> tuple[Path, Size, str, float | None, list[str]]:
    """Convert one recording for one video target. `dest_stem` gets the right extension.

    Returns (path, size, container, scale, warnings)."""
    from apppreview.compose import layout  # local: compose imports Pillow-heavy modules

    if not Path(src).is_file():
        raise AppPreviewError("input_not_found", f"Input not found: {src}")
    info = probe(src)
    plan = plan_video(info, target, size, start, extend)
    if size and size not in target.sizes:
        raise AppPreviewError("invalid_size", f"{size[0]}x{size[1]} is not accepted by {target.id}")
    ext = video_extension(plan.size)
    dest = dest_stem.with_suffix(f".{ext}")
    warnings = list(plan.warnings)
    layers = None
    if template is not None:
        layers = layout(template, plan.size, (info.width, info.height), caption, subtitle, locale)
        warnings += layers.warnings
        scale = layers.scale
    else:
        ratios = (plan.size[0] / info.width, plan.size[1] / info.height)
        scale = min(ratios) if mode == "fit" else max(ratios)
        if mode == "fit" and abs(info.width / info.height - plan.size[0] / plan.size[1]) > 0.01:
            warnings.append("Aspect ratios differ; output is letterboxed (use --mode fill to crop instead)")
    if scale > 1.5:
        warnings.append(f"Video is upscaled {scale:.2f}x; output may look blurry")
    if not dry_run:
        from apppreview.generate import resolve_background

        color = (0, 0, 0) if background == "auto" else resolve_background(background, None)
        convert(src, dest, plan, info, mode=mode, background=color, layers=layers)
    return dest, plan.size, ext, scale, warnings
