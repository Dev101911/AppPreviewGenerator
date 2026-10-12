# Using apppreview as an AI agent

`apppreview` is non-interactive and safe to run unattended. Add `--json` to any
command: stdout then contains only one JSON document. Exit code `0` is success,
`1` is a validation failure, `2` is a usage/input error with
`{"ok": false, "error": {"code": ..., "message": ...}}`.

## Recipes

**Find target IDs and their exact requirements**
```bash
apppreview targets --json                 # list of target specs
apppreview targets apple.ipad-13 --json   # one spec
```

**Minimum set for a typical iOS + Android app**
`apple.iphone-6.3` (or `apple.iphone-6.9`), `apple.ipad-13` if the app supports iPad,
and `google.phone` (at least 2, ideally 4+ at ≥1080px for promotion).

**Make screenshots compliant**
```bash
apppreview generate raw/*.png -t apple.iphone-6.9 -t google.phone -o out --json
```
Read `outputs[].path` for the written files and `ok` for the overall result. Check
`outputs[].warnings` for upscaling or letterboxing that may need a better source image.

**Check existing screenshots**
```bash
apppreview validate out/apple.iphone-6.9/*.png -t apple.iphone-6.9 --json
```
Each entry in `files[].issues` and `set_issues` has a `severity` (`error`/`warning`),
a stable `code`, a `message`, and often a `fix`.

**Plan without writing files**: add `--dry-run` to `generate` or `build`.

**Captioned marketing screenshots for a whole listing**
1. `apppreview schema config` gives the JSON Schema for `apppreview.yaml`
   (`apppreview schema template` for templates).
2. Write `apppreview.yaml` (or start with `apppreview init`), with one entry in `screens`
   per screenshot and captions keyed by locale.
3. `apppreview build --dry-run --json` validates the config and lists every output. All
   missing sources are reported in a single `input_not_found` error.
4. `apppreview build --json` renders and validates. `validation[]` has one report per
   locale × target.

**One-off captioned screenshot**
```bash
apppreview generate shot.png -t apple.iphone-6.9 --template sunset --caption "Plan your week" --json
```
Pass `--caption` once per input image, in the same order.

## Issue codes

| Code | Meaning |
|---|---|
| `not_found`, `unreadable` | File missing or not an image |
| `format` | Not PNG/JPEG (or not allowed for the target) |
| `extension_mismatch` | Content and file extension disagree (warning) |
| `alpha` | Has an alpha channel where transparency is not allowed |
| `color_mode` | Not RGB |
| `dimensions` | Size, aspect, or min/max side not accepted |
| `file_size` | Over the store's byte limit |
| `count` | Too many (error) or too few (warning) images in the set |
| `mixed_sizes` | Apple Watch screenshots must share one size |
| `mixed_orientation` | Set mixes portrait and landscape (warning) |
| `recommended_*` | Google Play promotion recommendations (warning) |

Fix almost all errors by running `generate` for the same target.

## Usage error codes (exit 2)

Common ones: `unknown_target`, `unknown_template`, `invalid_template`, `invalid_config`,
`config_not_found`, `input_not_found`, `missing_text` (no caption for a locale),
`missing_source`, `template_not_allowed` (target forbids captions/backgrounds),
`too_many_images`, `invalid_size`, `invalid_format`, `text_count_mismatch`.
