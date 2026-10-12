# AppPreviewGenerator

`apppreview` is a CLI that turns raw app screenshots into **App Store and Google Play
screenshots that pass store validation**. It is built for developers and AI agents
alike: every command has `--json` output and predictable exit codes.

## Install

```bash
pipx install git+https://github.com/dev101911/apppreviewgenerator
# or run without installing
uvx --from git+https://github.com/dev101911/apppreviewgenerator apppreview --help
```

Requires Python 3.10+.

## Quickstart

```bash
# What can I target?
apppreview targets                      # all Apple and Google targets
apppreview targets apple.iphone-6.9     # full spec for one target

# Are my screenshots compliant?
apppreview validate shots/*.png --target apple.iphone-6.9

# Make them compliant for several stores at once
apppreview generate shots/*.png \
  -t apple.iphone-6.9 -t apple.ipad-13 -t google.phone \
  --out store-assets --background auto

# Which targets does an image already fit?
apppreview identify shot.png
```

`generate` writes `OUT/<target-id>/NN_<name>.png` and re-validates every file it writes.

| Option | Meaning |
|---|---|
| `--mode fit` (default) | Scale to fit, letterbox with `--background` |
| `--mode fill` | Scale to cover, center-crop |
| `--mode stretch` | Scale to exact size, distorting if needed |
| `--background` | Color name, `#RRGGBB`, or `auto` (average edge color). Also used to flatten transparency. |
| `--format` | `png` (default) or `jpeg` |
| `--size WxH` | Force a specific accepted size, e.g. `1290x2796` |
| `--dry-run` | Show what would be written |

## What gets checked

- Exact pixel sizes (Apple) or size, aspect-ratio rules (Google)
- Format (detected from content, not extension): PNG/JPEG
- No alpha channel / transparency (except Play app icon)
- RGB color mode
- File size limits
- Number of screenshots per set; uniform size for Apple Watch
- Google Play promotion recommendations (as warnings)

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success / all images valid |
| 1 | Validation failed |
| 2 | Usage or input error (unknown target, unreadable file, bad option) |

## Targets

Apple: iPhone (Duo, 6.9", 6.5", 6.3", 6.1", 5.5", 4.7", 4", 3.5"), iPad (13", 12.9", 11",
10.5", 9.7"), Mac, Apple TV, Vision Pro, Apple Watch.

Google: phone, 7" and 10" tablets, Chromebook, Wear OS, Android TV, Android XR,
Automotive, feature graphic, TV banner, app icon.

Specs live in [`src/apppreview/data/`](src/apppreview/data) with their source URL and the
date they were last verified. Run `apppreview targets --json` for the full data.

## Using from Python

```python
from apppreview import generate, validate_images

report = validate_images(["shot.png"], "apple.iphone-6.9")
print(report.ok, report.to_dict())

result = generate(["shot.png"], ["apple.iphone-6.9", "google.phone"], out_dir="out")
```

## For AI agents

See [AGENTS.md](AGENTS.md).

## Roadmap

See [docs/PLAN.md](docs/PLAN.md) and the GitHub issues.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```
