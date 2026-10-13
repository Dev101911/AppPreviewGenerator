"""Store screenshot specifications, loaded from the bundled JSON data files."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from functools import cache
from importlib import resources

from apppreview.errors import AppPreviewError

STORES = ("apple", "google")

Size = tuple[int, int]


@dataclass(frozen=True)
class Constraints:
    """Rule-based size limits, used where a store accepts a range of sizes."""

    min_side: int | None = None
    max_side: int | None = None
    max_aspect: float | None = None  # longest side / shortest side
    aspects: tuple[str, ...] = ()  # e.g. "9:16"; empty means any aspect within max_aspect


@dataclass(frozen=True)
class Target:
    id: str
    store: str
    name: str
    kind: str
    formats: tuple[str, ...]
    alpha: bool
    count_min: int
    count_max: int
    sizes: tuple[Size, ...] = ()  # exact accepted sizes, preferred first
    constraints: Constraints | None = None
    recommended_sizes: tuple[Size, ...] = ()
    recommendations: dict = field(default_factory=dict)
    max_bytes: int | None = None
    uniform_size: bool = False
    frames_allowed: bool = True
    templates_allowed: bool = True  # captions/backgrounds may be added
    preview_type: str | None = None  # App Store Connect preview type, for video targets
    required: str | None = None
    fallback: str | None = None
    notes: tuple[str, ...] = ()
    source: str = ""
    verified: str = ""

    @property
    def output_sizes(self) -> tuple[Size, ...]:
        """Sizes `generate` can produce, preferred first."""
        return self.sizes or self.recommended_sizes

    def accepts_size(self, width: int, height: int) -> bool:
        return not size_problems(self, width, height)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["sizes"] = [list(s) for s in self.sizes]
        data["recommended_sizes"] = [list(s) for s in self.recommended_sizes]
        data["count"] = {"min": data.pop("count_min"), "max": data.pop("count_max")}
        if self.constraints:
            data["constraints"]["aspects"] = list(self.constraints.aspects)
        return data


def parse_aspect(text: str) -> float:
    w, h = text.split(":")
    return int(w) / int(h)


def aspect_matches(width: int, height: int, aspect: str, tolerance: float = 0.01) -> bool:
    return abs(width / height - parse_aspect(aspect)) <= tolerance * parse_aspect(aspect)


def size_problems(target: Target, width: int, height: int) -> list[str]:
    """Return human-readable reasons why width x height is not accepted, or []."""
    if target.sizes and not target.constraints:
        if (width, height) in target.sizes:
            return []
        return [f"{width}x{height} is not an accepted size; expected one of {format_sizes(target.sizes)}"]

    c = target.constraints
    if c is None:
        return []
    problems = []
    short, long = sorted((width, height))
    if c.min_side and short < c.min_side:
        problems.append(f"shortest side is {short}px; minimum is {c.min_side}px")
    if c.max_side and long > c.max_side:
        problems.append(f"longest side is {long}px; maximum is {c.max_side}px")
    if c.max_aspect and long / short > c.max_aspect + 1e-9:
        problems.append(f"aspect ratio is {long / short:.2f}:1; maximum is {c.max_aspect:g}:1")
    if c.aspects and not any(aspect_matches(width, height, a) for a in c.aspects):
        problems.append(f"aspect ratio must be one of {', '.join(c.aspects)}")
    return problems


def format_sizes(sizes) -> str:
    return ", ".join(f"{w}x{h}" for w, h in sizes)


def _build_target(raw: dict, defaults: dict, store: dict) -> Target:
    spec = {**defaults, **raw}
    constraints = spec.get("constraints")
    count = spec.get("count", {"min": 1, "max": 10})
    return Target(
        id=spec["id"],
        store=store["store"],
        name=spec["name"],
        kind=spec.get("kind", "screenshot"),
        formats=tuple(spec["formats"]),
        alpha=spec.get("alpha", False),
        count_min=count["min"],
        count_max=count["max"],
        sizes=tuple(tuple(s) for s in spec.get("sizes", [])),
        constraints=Constraints(
            min_side=constraints.get("min_side"),
            max_side=constraints.get("max_side"),
            max_aspect=constraints.get("max_aspect"),
            aspects=tuple(constraints.get("aspects", ())),
        )
        if constraints
        else None,
        recommended_sizes=tuple(tuple(s) for s in spec.get("recommended_sizes", [])),
        recommendations=spec.get("recommendations", {}),
        max_bytes=spec.get("max_bytes"),
        uniform_size=spec.get("uniform_size", False),
        frames_allowed=spec.get("frames_allowed", True),
        templates_allowed=spec.get("templates_allowed", True),
        preview_type=spec.get("preview_type"),
        required=spec.get("required"),
        fallback=spec.get("fallback"),
        notes=tuple(spec.get("notes", ())),
        source=store.get("preview_source", store["source"]) if spec.get("kind") == "video" else store["source"],
        verified=store["verified"],
    )


@cache
def load_targets() -> dict[str, Target]:
    targets: dict[str, Target] = {}
    for store in STORES:
        text = (resources.files("apppreview") / "data" / f"{store}.json").read_text("utf-8")
        data = json.loads(text)
        for raw in data["targets"]:
            target = _build_target(raw, data.get("defaults", {}), data)
            targets[target.id] = target
    return targets


def get_target(target_id: str) -> Target:
    targets = load_targets()
    if target_id in targets:
        return targets[target_id]
    raise AppPreviewError(
        "unknown_target",
        f"Unknown target '{target_id}'. Run `app-preview targets` to list valid IDs.",
    )


@cache
def video_rules() -> dict:
    text = (resources.files("apppreview") / "data" / "apple.json").read_text("utf-8")
    return json.loads(text)["video_rules"]
