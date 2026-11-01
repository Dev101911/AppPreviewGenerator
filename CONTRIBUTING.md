# Contributing

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Before opening a PR, run everything CI checks in one go:

```bash
scripts/check.sh
```

It runs ruff, the tests, the docs page check and the action's smoke test (through the CLI) on
one Python, creating `.venv` with uv if needed. CI (`.github/workflows/ci.yml`) still runs on
every PR, across Python 3.10 to 3.13, and has to pass before merging.

## Where work runs: the build PC first

**Every task that can run on the build PC runs there.** The build PC is an Ubuntu machine reached
over SSH and Tailscale. The Mac is kept for work that needs macOS, and for orchestrating: splitting
tasks, handing work to the PC, reviewing what comes back, and opening PRs. The MacBook slows down
when many tasks run at once, so anything it doesn't have to do goes elsewhere. `app-preview` is a
cross-platform Python CLI, so nearly all of its work runs on the PC.

- **Runs on the PC:** development, the tests and `scripts/check.sh`, docs (including
  `scripts/build_docs.py`), store spec checks, Android capture (`capture android` via `adb`), video
  work with ffmpeg (`generate` for `-preview` targets, `slideshow`), and issue admin.
- **Runs on the Mac:** `capture ios`, `capture simulators`, and anything else that needs Xcode or
  the simulator. The Mac also orchestrates: it reviews the PC's branches, opens every PR and moves
  the board, even for work done entirely on the PC.
- **Releases** stay where their credentials are: the Release workflow (`release.yml`) publishes
  through PyPI trusted publishing on GitHub. No tokens are copied to the PC.
- **Handing work over:** work moves between the machines only as pushed branches.
  - The Mac session creates the issue branch, `issue-<n>-<slug>`, and pushes it. The PC works on
    `issue-<n>-<slug>-pc`, based on it, and pushes that.
  - A change that needs an iOS capture check gets that check on the Mac before the PR: the Mac
    merges the PC branch back, runs the check and opens the PR.
  - A change the PC's own checks cover (`scripts/check.sh`) can be finished on the PC. The Mac only
    reviews it, opens the PR and moves the board.
- **Never:**
  - let the PC connect back to the Mac: no remote build script, no Remote Login, no Tailscale rule
    from the PC to the Mac. The Mac reaches the PC, never the other way round.
  - put release or publishing credentials on the PC (PyPI tokens, Homebrew tap keys, or any GitHub
    token beyond the PC's own one, which is limited to these repos' contents, issues and PRs).
  - push to `main`, or force-push.

The tests don't need macOS: `capture ios` is tested with a fake `simctl`, so a Linux run covers
it. Setting up the PC needs uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`, since Ubuntu's
Python has no `venv` or `pip` without extra packages) and ffmpeg (`sudo apt install ffmpeg`).
Without ffmpeg the video tests are skipped.

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

Releases go to PyPI from the **Release** workflow (`.github/workflows/release.yml`).

**One-time setup** (repository owner):

1. On PyPI, add a *pending trusted publisher* for the project `app-preview`
   (Account settings → Publishing): owner `Dev101911`, repository `AppPreviewGenerator`,
   workflow `release.yml`, environment `pypi`. No API token is stored in the repo.
2. In the GitHub repository settings, create an environment named `pypi` that allows the
   `main` branch (and `v*` tags, if you also release by pushing tags). Optionally require a
   reviewer, so every publish needs a click.

**Each release:**

1. Bump `__version__` in `src/apppreview/__init__.py` and merge to `main`.
2. Start the release: **Actions → Release → Run workflow** on `main`, entering the version
   (e.g. `0.2.0`) as a safety check. Pushing a matching tag (`git push origin v0.2.0`)
   works too.
3. The workflow checks the version, runs the tests, builds and checks the sdist and wheel,
   and publishes to PyPI. Only then does it create the `v0.2.0` tag and GitHub release,
   and move the action's major tag (`v0` for 0.x releases, `v1` from 1.0) so
   `uses: Dev101911/AppPreviewGenerator@v0` picks up the release.
4. Homebrew: update the tap formula (see `packaging/homebrew/README.md`).
