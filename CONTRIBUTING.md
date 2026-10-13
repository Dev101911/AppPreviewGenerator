# Contributing

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## Documentation site

`docs/index.html` is the GitHub Pages site. It is generated: edit `docs/_template.html`
for the guides, then run

```bash
python scripts/build_docs.py
```

The command reference, target tables, config/template fields, error codes and MCP tool list
come from the code, so regenerate after changing any of them. CI fails if the page is stale,
and `tests/test_docs.py` fails if a new error code or command has no documentation.

To publish it (one time): repository **Settings → Pages → Build and deployment**, source
"Deploy from a branch", branch `main`, folder `/docs`.

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

## Releasing

Releases go to PyPI from the **Release** workflow when a `v*` tag is pushed.

**One-time setup** (repository owner):

1. On PyPI, add a *pending trusted publisher* for the project `apppreview`
   (Account settings → Publishing): owner `Dev101911`, repository `AppPreviewGenerator`,
   workflow `release.yml`, environment `pypi`. No API token is stored in the repo.
2. In the GitHub repository settings, create an environment named `pypi`. Optionally
   require a reviewer, so every publish needs a click.

**Each release:**

1. Bump `__version__` in `src/apppreview/__init__.py` and merge to `main`.
2. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`.
3. The workflow checks the tag matches `__version__`, runs the tests, builds and checks
   the sdist and wheel, publishes to PyPI, and creates a GitHub release.
4. Move the action's major tag so `uses: Dev101911/AppPreviewGenerator@v1` picks up the
   release: `git tag -f v1 && git push -f origin v1`.
5. Homebrew: update the tap formula (see `packaging/homebrew/README.md`).
