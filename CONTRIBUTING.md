# Contributing

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## Updating store specs

The tool's compliance rules live in data files, not code:

| File | Contents | Source |
|---|---|---|
| `src/apppreview/data/apple.json` | App Store screenshot targets | [Screenshot specifications](https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications) |
| `src/apppreview/data/google.json` | Google Play screenshot and graphic targets | [Preview assets](https://support.google.com/googleplay/android-developer/answer/9866151) |
| `src/apppreview/data/locales.json` | Store locale codes | [Apple shortcodes](https://developer.apple.com/documentation/appstoreconnectapi/managing-metadata-in-your-app-by-using-locale-shortcodes), [Google languages](https://support.google.com/googleplay/android-developer/answer/9844778) |
| `src/apppreview/data/spec_fingerprints.json` | Hashes of the pages' text, to detect wording changes | generated |

The **Store spec check** workflow runs every Monday (and on demand). It runs
`python -m apppreview.speccheck`, and when the published pages differ from the data it
opens an issue titled "Store specs changed" that lists the differences. To resolve it:

1. Run `python -m apppreview.speccheck` locally to see the current differences.
2. Read the changed pages and update the JSON files: add or remove sizes, targets or
   locale codes, and set `verified` to today's date in each file you checked.
3. If only the wording changed, check whether a rule changed (a count, a limit, a new
   device) and update the data if so.
4. Run `python -m apppreview.speccheck --update-fingerprints` to record the reviewed pages.
5. Run the tests and open a PR that links the issue.

Exit codes: `0` everything matches, `1` changes found, `2` a page couldn't be fetched or
its layout changed so much that the checker needs updating.
