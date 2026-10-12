"""Store locale codes, mapping between them, and text-layout hints per locale."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources

# Languages written right to left.
RTL_LANGUAGES = {"ar", "he", "iw", "fa", "ur", "yi", "ps", "sd", "ug", "dv", "ckb"}

# Codes that mean the same thing but are spelled differently across stores.
_EQUIVALENT = {
    "he": "iw",
    "iw": "he",
    "zh-hans": "zh-cn",
    "zh-cn": "zh-hans",
    "zh-hant": "zh-tw",
    "zh-tw": "zh-hant",
    "zh-hk": "zh-hant",
    "zh-sg": "zh-hans",
    "es-mx": "es-419",
    "es-419": "es-mx",
}


@cache
def store_locales() -> dict[str, dict[str, str]]:
    """{store: {code: language name}}"""
    data = json.loads((resources.files("apppreview") / "data" / "locales.json").read_text("utf-8"))
    return {store: entry["locales"] for store, entry in data.items()}


def language(locale: str) -> str:
    return locale.replace("_", "-").split("-")[0].lower()


def store_locale(store: str, locale: str) -> str | None:
    """Map a locale such as 'ja-JP' or 'zh-Hans' to the store's own code, or None if unsupported.

    Tries an exact (case-insensitive) match, a known equivalent spelling, then a unique
    match on the language alone. Ambiguous bare languages (e.g. 'en') return None.
    """
    codes = store_locales()[store]
    by_lower = {code.lower(): code for code in codes}
    key = locale.replace("_", "-").lower()
    for candidate in (key, _EQUIVALENT.get(key), _EQUIVALENT.get(language(key))):
        if candidate in by_lower:
            return by_lower[candidate]
    lang = _EQUIVALENT.get(language(key), language(key))
    same_language = [code for code in codes if language(code) in (language(key), lang)]
    if len(same_language) == 1:
        return same_language[0]
    region = key.split("-")[1] if "-" in key else None
    if region:
        matches = [c for c in same_language if c.lower().endswith("-" + region)]
        if len(matches) == 1:
            return matches[0]
    return None


def is_rtl(locale: str | None) -> bool:
    return bool(locale) and language(locale) in RTL_LANGUAGES
