# Using apppreview as an AI agent

`apppreview` is non-interactive and safe to run unattended. Add `--json` to any
command: stdout then contains only one JSON document. Exit code `0` is success,
`1` is a validation failure, `2` is a usage/input error with
`{"ok": false, "error": {"code": ..., "message": ...}}`.

Prefer tools over shelling out? Run the MCP server: `apppreview mcp` (needs
`pip install "apppreview[mcp]"`). Its tools mirror the commands below and return the
same JSON.

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

**Capture from a simulator or emulator**
`apppreview capture ios -o shots/home.png --json` (or `capture android`) returns `path`,
`width`, `height` and `matches` (targets it fits as-is). `apppreview capture simulators --json`
maps Apple targets to simulator names to boot.

**App preview videos** (needs ffmpeg)
`apppreview generate demo.mov -t apple.iphone-6.9-preview --json` converts a recording;
add `--start N` to choose the 30 s segment or `--extend` for clips under 15 s.
`apppreview slideshow shots/*.png -t apple.iphone-6.9-preview --json` makes a preview from
screenshots. Video sources must go to `-preview` targets and images to the others
(`mixed_media` error otherwise).

**Ready-to-upload folders**: add `--layout fastlane` (deliver + supply) or `--layout gpp`
(Gradle Play Publisher) to `build`, `generate` or `graphic`. Skipped targets appear in
`warnings[]`.

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
Add `--frame auto` (or `phone`, `tablet`, a frame `.png`) to show the screenshot inside a device; `apppreview frames --json` lists built-ins.

**Play feature graphic / TV banner**
```bash
apppreview graphic -t google.feature-graphic --title "App Name" --subtitle "Tagline" --icon icon.png --json
```
Or list them under `graphics:` in `apppreview.yaml`.

**Non-Latin languages**
Check `outputs[].warnings` for "no glyphs": the font can't draw that text. Fix it by
adding `locale_overrides: {<lang>: {caption: {font: <path>}, subtitle: {font: <path>}}}`
to the config. `build` also returns `warnings[]` for locales a store doesn't support;
`apppreview locales <code> --json` maps a locale to each store's code. Only targets with `"kind": "graphic"`
in `apppreview targets --json` work here.

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
| `duration`, `frame_rate` | Video length outside 15-30 s, or over 30 fps |
| `video_codec`, `video_profile` | Not H.264 (High@4.0 or lower) or ProRes 422 HQ |
| `audio` | Missing, not stereo, wrong codec or sample rate |
| `bitrate` | Video bit rate well above Apple's 10-12 Mbps target (warning) |

Fix almost all errors by running `generate` for the same target.

## Usage error codes (exit 2)

Common ones: `unknown_target`, `unknown_template`, `invalid_template`, `invalid_config`,
`config_not_found`, `input_not_found`, `missing_text` (no caption for a locale),
`missing_source`, `template_not_allowed` (target forbids captions/backgrounds),
`too_many_images`, `invalid_size`, `invalid_format`, `text_count_mismatch`,
`not_a_graphic_target`, `nothing_to_build`, `unknown_frame`, `invalid_frame`, `frame_not_found`,
`frame_not_allowed`, `invalid_layout`, `unsupported_in_layout`, `tool_not_found`, `no_device`, `ambiguous_device`,
`capture_failed`, `mixed_media`, `video_too_short`, `invalid_start`,
`not_a_video_target`, `ffmpeg_failed`.
