import json

import pytest

from apppreview import speccheck
from apppreview.locales import store_locales
from apppreview.specs import load_targets

_real = speccheck._data

APPLE_SIZES = sorted(
    {s for t in load_targets().values() if t.store == "apple" and t.kind == "screenshot" for s in t.sizes}
)


def apple_page(sizes=APPLE_SIZES, extra=""):
    rows = "".join(f"<td>{w} x {h}</td>" for w, h in sizes)
    return f"<html><main><h1>Screenshot specifications</h1><table>{rows}</table>{extra}</main></html>"


def apple_previews_page():
    sizes = {s for t in load_targets().values() if t.kind == "video" for s in t.sizes}
    return "<main>" + "".join(f"<td>{w} x {h}</td>" for w, h in sorted(sizes)) + "</main>"


def google_article(body):
    return f'<div class="article-content-container">{body}</div><div class="article-survey-container"></div>'


def apple_locales_json(codes):
    rows = [
        [{"inlineContent": [{"type": "text", "text": "Name"}]}, {"inlineContent": [{"type": "codeVoice", "code": c}]}]
        for c in codes
    ]
    return json.dumps({"sections": [{"type": "table", "rows": rows}]})


def google_locales_page(codes):
    return google_article("".join(f"<p>Language {i} – {c}</p>" for i, c in enumerate(codes)))


def fake_fetcher(apple=None, google=None, apple_codes=None, google_codes=None):
    pages = {
        speccheck.APPLE_SCREENSHOTS: apple or apple_page(),
        speccheck.APPLE_PREVIEWS: apple_previews_page(),
        speccheck.GOOGLE_SCREENSHOTS: google or google_article("<p>Up to 8 screenshots</p>"),
        speccheck.APPLE_LOCALES: apple_locales_json(apple_codes or list(store_locales()["apple"])),
        speccheck.GOOGLE_LOCALES: google_locales_page(google_codes or list(store_locales()["google"])),
    }
    return pages.__getitem__


def test_matching_pages_report_only_wording():
    report = speccheck.run(fake_fetcher())
    # The fixtures' wording differs from the real pages, so only fingerprint changes remain.
    assert all("wording" in c for c in report.changes)


def test_new_and_removed_sizes():
    sizes = [s for s in APPLE_SIZES if s != (1320, 2868)] + [(1400, 3000)]
    report = speccheck.run(fake_fetcher(apple=apple_page(sizes)))
    assert any("new screenshot size 1400x3000" in c for c in report.changes)
    assert any("no longer lists 1320x2868" in c for c in report.changes)


def test_locale_changes():
    apple = [*store_locales()["apple"], "xx-XX"]
    google = [c for c in store_locales()["google"] if c != "iw-IL"]
    report = speccheck.run(fake_fetcher(apple_codes=apple, google_codes=google))
    assert any("Apple added locale xx-XX" in c for c in report.changes)
    assert any("Google Play no longer lists locale iw-IL" in c for c in report.changes)


def test_wording_fingerprint_detects_edits(monkeypatch):
    base = speccheck.run(fake_fetcher()).fingerprints
    monkeypatch.setattr(speccheck, "_data", lambda name: base if name == "spec_fingerprints.json" else _real(name))
    assert speccheck.run(fake_fetcher()).changes == []
    edited = speccheck.run(fake_fetcher(google=google_article("<p>Up to 10 screenshots</p>")))
    assert edited.changes == ["The wording of google screenshots changed; review the page for new rules"]


def test_layout_change_is_an_error():
    with pytest.raises(speccheck.FetchError):
        speccheck.run(fake_fetcher(apple="<html>redesigned</html>"))


def test_bundled_fingerprints_cover_every_page():
    assert set(speccheck._data("spec_fingerprints.json")) == {
        "apple_screenshots",
        "apple_previews",
        "google_screenshots",
    }
