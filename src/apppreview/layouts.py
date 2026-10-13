"""Output folder layouts that upload tools read directly.

- default:  OUT/<locale>/<target>/NN_<name>.png
- fastlane: Apple targets for `deliver` (OUT/fastlane/screenshots/<locale>/...) and
            Google targets for `supply` (OUT/fastlane/metadata/android/<locale>/images/...)
- gpp:      Google targets for Gradle Play Publisher (OUT/play/listings/<locale>/graphics/...);
            Apple targets use the fastlane deliver layout, the only Apple upload layout.

Locale folders use each store's own codes (e.g. `ja` for Apple, `ja-JP` for Google).
"""

from __future__ import annotations

from pathlib import Path

from apppreview.errors import AppPreviewError
from apppreview.locales import store_locale
from apppreview.specs import Target

LAYOUTS = ("default", "fastlane", "gpp")

# fastlane supply: screenshot folders and single-image files under images/
SUPPLY = {
    "google.phone": "phoneScreenshots",
    "google.tablet-7": "sevenInchScreenshots",
    "google.tablet-10": "tenInchScreenshots",
    "google.tv": "tvScreenshots",
    "google.wear-os": "wearScreenshots",
    "google.feature-graphic": "featureGraphic",
    "google.icon": "icon",
    "google.tv-banner": "tvBanner",
}

# Gradle Play Publisher: one directory per media type under graphics/
GPP = {
    "google.phone": "phone-screenshots",
    "google.tablet-7": "tablet-screenshots",
    "google.tablet-10": "large-tablet-screenshots",
    "google.tv": "tv-screenshots",
    "google.wear-os": "wear-screenshots",
    "google.feature-graphic": "feature-graphic",
    "google.icon": "icon",
    "google.tv-banner": "tv-banner",
}

# deliver detects the device from image size. 2048x2732 goes to the newer 12.9"/13" slot
# unless the file name carries this marker (fastlane 2.230.0+).
DELIVER_MARKERS = {"apple.ipad-12.9": "APP_IPAD_PRO_129"}
DELIVER_UNSUPPORTED = {"apple.icon": "deliver takes the app icon from the build, not the screenshots folder"}


def check_layout(layout: str) -> None:
    if layout not in LAYOUTS:
        raise AppPreviewError("invalid_layout", f"Unknown layout '{layout}'; use one of {', '.join(LAYOUTS)}")


def unsupported_reason(layout: str, target: Target) -> str | None:
    """Why `target` has no place in `layout`, or None if it does."""
    if layout == "default":
        return None
    if target.store == "apple":
        return DELIVER_UNSUPPORTED.get(target.id)
    table = SUPPLY if layout == "fastlane" else GPP
    if target.id not in table:
        tool = "fastlane supply" if layout == "fastlane" else "Gradle Play Publisher"
        return f"{tool} has no folder for {target.id}; upload it in Play Console"
    return None


def layout_path(
    layout: str,
    out_root: Path,
    target: Target,
    locale: str | None,
    filename: str,
    default_locale: str = "en-US",
) -> Path:
    """Where an output file goes. `filename` is the default-layout name, e.g. 01_home.png."""
    if layout == "default":
        return (out_root / locale / target.id if locale else out_root / target.id) / filename

    store_code = store_locale(target.store, locale or default_locale) or locale or default_locale
    if target.store == "apple":
        # All devices share one folder per locale, so prefix with the target to keep names
        # unique and grouped; deliver sorts by name within each device.
        marker = DELIVER_MARKERS.get(target.id)
        name = f"{target.id.split('.', 1)[1]}_{filename}"
        if marker:
            name = f"{marker}_{name}"
        return out_root / "fastlane" / "screenshots" / store_code / name

    ext = filename.rsplit(".", 1)[1]
    if layout == "fastlane":
        images = out_root / "fastlane" / "metadata" / "android" / store_code / "images"
        folder = SUPPLY[target.id]
        if target.kind == "screenshot":
            return images / folder / filename
        return images / f"{folder}.{ext}"

    graphics = out_root / "play" / "listings" / store_code / "graphics"
    return graphics / GPP[target.id] / filename


def deliver_collisions(targets: list[Target]) -> list[str]:
    """Warnings for Apple targets deliver cannot tell apart, because they share a pixel size."""
    apple = [
        t for t in targets if t.store == "apple" and t.id not in DELIVER_MARKERS and t.id not in DELIVER_UNSUPPORTED
    ]
    warnings = []
    for i, a in enumerate(apple):
        for b in apple[i + 1 :]:
            shared = set(a.sizes) & set(b.sizes)
            if shared:
                w, h = sorted(shared)[0]
                warnings.append(
                    f"{a.id} and {b.id} share the size {w}x{h}, so deliver cannot tell them apart; "
                    "build them separately (-t) and upload with the matching deliver platform"
                )
    return warnings
