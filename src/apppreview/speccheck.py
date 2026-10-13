"""Detect when the stores change the specs this tool is built on.

Run `python -m apppreview.speccheck` (or the scheduled `spec-check` workflow). It fetches
the official pages and compares them with the bundled data:

- Apple screenshot sizes: every WxH on the page must be a known size and vice versa.
- Apple app preview sizes: every accepted preview size must still be on the page.
- Apple and Google locale codes: the published lists must match data/locales.json.
- Google screenshot rules and both pages' wording: the article text is hashed and compared
  with data/spec_fingerprints.json, so any edit (e.g. a new count limit) is flagged.

Exit status: 0 unchanged, 1 changes found, 2 a page could not be fetched or parsed.
After reviewing a change, update the data files and run with --update-fingerprints.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

APPLE_SCREENSHOTS = (
    "https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications"
)
APPLE_LOCALES = (
    "https://developer.apple.com/tutorials/data/documentation/appstoreconnectapi/"
    "managing-metadata-in-your-app-by-using-locale-shortcodes.json"
)
APPLE_PREVIEWS = (
    "https://developer.apple.com/help/app-store-connect/reference/app-information/app-preview-specifications"
)
GOOGLE_SCREENSHOTS = "https://support.google.com/googleplay/android-developer/answer/9866151?hl=en"
GOOGLE_LOCALES = "https://support.google.com/googleplay/android-developer/answer/9844778?hl=en"

DIMENSION = re.compile(r"(\d{3,4})\s*(?:x|×)\s*(\d{3,4})")
GOOGLE_LOCALE_LINE = re.compile(r"^\s*([^\n–]{2,60}?)\s+–\s*([a-z]{2,3}(?:-[A-Za-z0-9]{2,4})?)\s*$", re.M)


class FetchError(Exception):
    pass


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (app-preview spec check)"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise FetchError(f"{url}: {exc}") from exc


def page_text(document: str, start: str, end: str) -> str:
    """Visible text between two markers, tags stripped and whitespace collapsed."""
    i = document.find(start)
    j = document.find(end, i + len(start))
    if i < 0 or j < 0:
        raise FetchError(f"Page layout changed: markers {start!r}..{end!r} not found")
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", document[i:j], flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", body))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _data(name: str) -> dict:
    return json.loads((resources.files("apppreview") / "data" / name).read_text("utf-8"))


@dataclass
class Report:
    changes: list[str] = field(default_factory=list)
    fingerprints: dict[str, str] = field(default_factory=dict)


def check_apple_screenshots(document: str, report: Report) -> None:
    text = page_text(document, "<main", "</main>")
    report.fingerprints["apple_screenshots"] = fingerprint(text)
    page = {(int(w), int(h)) for w, h in DIMENSION.findall(text)}
    if not page:
        raise FetchError("No screenshot sizes found on the Apple page; the page layout may have changed")
    ours = {
        tuple(s)
        for t in _data("apple.json")["targets"]
        if t.get("kind", "screenshot") == "screenshot"
        for s in t["sizes"]
    }
    for w, h in sorted(page - ours):
        report.changes.append(f"Apple lists a new screenshot size {w}x{h} (not in data/apple.json)")
    for w, h in sorted(ours - page):
        report.changes.append(f"Apple no longer lists {w}x{h} (still in data/apple.json)")


def check_apple_previews(document: str, report: Report) -> None:
    text = page_text(document, "<main", "</main>")
    report.fingerprints["apple_previews"] = fingerprint(text)
    # The page also lists device resolutions, so only check that our accepted sizes are still there.
    page = {(int(w), int(h)) for w, h in DIMENSION.findall(text)}
    ours = {tuple(s) for t in _data("apple.json")["targets"] if t.get("kind") == "video" for s in t["sizes"]}
    for w, h in sorted(ours - page):
        report.changes.append(f"Apple no longer lists the app preview size {w}x{h} (still in data/apple.json)")


def check_google_screenshots(document: str, report: Report) -> None:
    text = page_text(document, 'class="article-content-container"', 'class="article-survey-container"')
    report.fingerprints["google_screenshots"] = fingerprint(text)


def check_apple_locales(document: str, report: Report) -> None:
    rows = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "table":
                for row in node.get("rows", []):
                    cells = [" ".join(re.findall(r'"(?:text|code)": "([^"]*)"', json.dumps(c))) for c in row]
                    rows.append(cells)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(document))
    page = {code.strip() for _, code in (r[:2] for r in rows if len(r) >= 2) if "-" in code or code.isalpha()}
    page.discard("Locale shortcode")
    if len(page) < 20:
        raise FetchError("Too few Apple locale codes found; the documentation format may have changed")
    _compare_locales("Apple", page, set(_data("locales.json")["apple"]["locales"]), report)


def check_google_locales(document: str, report: Report) -> None:
    text = page_text(document, 'class="article-content-container"', 'class="article-survey-container"')
    page = {code for _, code in GOOGLE_LOCALE_LINE.findall(text)}
    if len(page) < 20:
        raise FetchError("Too few Google locale codes found; the page layout may have changed")
    _compare_locales("Google Play", page, set(_data("locales.json")["google"]["locales"]), report)


def _compare_locales(store: str, page: set[str], ours: set[str], report: Report) -> None:
    for code in sorted(page - ours):
        report.changes.append(f"{store} added locale {code} (not in data/locales.json)")
    for code in sorted(ours - page):
        report.changes.append(f"{store} no longer lists locale {code} (still in data/locales.json)")


CHECKS = [
    (APPLE_SCREENSHOTS, check_apple_screenshots),
    (APPLE_PREVIEWS, check_apple_previews),
    (GOOGLE_SCREENSHOTS, check_google_screenshots),
    (APPLE_LOCALES, check_apple_locales),
    (GOOGLE_LOCALES, check_google_locales),
]


def run(fetcher=fetch) -> Report:
    report = Report()
    for url, check in CHECKS:
        check(fetcher(url), report)
    known = _data("spec_fingerprints.json")
    for key, value in report.fingerprints.items():
        if known.get(key) != value:
            report.changes.append(f"The wording of {key.replace('_', ' ')} changed; review the page for new rules")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="Print a JSON report")
    parser.add_argument(
        "--update-fingerprints", action="store_true", help="Store the current page fingerprints after review"
    )
    args = parser.parse_args(argv)
    try:
        report = run()
    except (FetchError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}) if args.json else f"Error: {exc}", file=sys.stdout)
        return 2

    if args.update_fingerprints:
        path = Path(__file__).parent / "data" / "spec_fingerprints.json"
        path.write_text(json.dumps(report.fingerprints, indent=2) + "\n", "utf-8")
        report.changes = [c for c in report.changes if "wording" not in c]

    if args.json:
        print(json.dumps({"ok": not report.changes, "changes": report.changes, "fingerprints": report.fingerprints}))
    elif report.changes:
        print("Store specs changed:\n" + "\n".join(f"- {c}" for c in report.changes))
    else:
        print("Store specs match the bundled data.")
    return 1 if report.changes else 0


if __name__ == "__main__":
    sys.exit(main())
