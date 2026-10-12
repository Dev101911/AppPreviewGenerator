# AppPreviewGenerator — Plan

A CLI (`apppreview`) that turns raw app screenshots into App Store and Google Play
assets that pass store validation the first time, and that is just as easy for an
AI agent to drive as for a developer.

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Language | **Python ≥ 3.10** | Pillow is the most mature image stack; `pipx`/`uvx` give one-line installs; easy to wrap as an MCP server later. |
| CLI framework | `click` | Stable, composable, good help output. |
| Config & templates | YAML validated with `pydantic` | Strict, clear errors, and free JSON Schemas for agents. |
| Imaging | `Pillow` | Resize/crop/pad, alpha flattening, PNG/JPEG encoding, text rendering for later template work. |
| Store specs | Versioned JSON data files (`src/apppreview/data/*.json`) with source URLs and a `verified` date | Specs change (e.g. new iPhone sizes); updating data should not require code changes. |
| Output for machines | Every command supports `--json`; stable exit codes | Lets agents and CI parse results without scraping text. |

## Core concepts

- **Target** — a store + device class, e.g. `apple.iphone-6.9`, `google.phone`.
  A target lists accepted sizes (exact pixel sizes for Apple, constraint rules for
  Google), formats, alpha rules, file-size limits, and screenshot count limits.
- **validate** — check existing images against a target and report every violation.
- **generate** — take source screenshots, fit them to a target size (fit/fill/stretch),
  flatten transparency, encode in an accepted format, then self-validate the output.

## CLI (MVP)

```
apppreview targets [--store apple|google] [--json]
apppreview validate IMAGES... --target ID [--json]
apppreview generate IMAGES... --target ID [--target ID ...] [--out DIR]
                    [--mode fit|fill|stretch] [--background COLOR]
                    [--format png|jpeg] [--size WxH] [--json] [--dry-run]
```

Exit codes: `0` success / all valid, `1` validation failures, `2` usage or input error.

## Milestones

### M1 — MVP ✅ (PR #20)
1. Project scaffolding: packaging, CLI skeleton, lint, tests, CI.
2. Store spec registry (Apple + Google), with source + verified date.
3. `targets` command.
4. `validate` command.
5. `generate` command (resize modes, alpha flattening, format conversion, self-validation).
6. Agent-friendly UX: `--json` everywhere, exit codes, `AGENTS.md`.

### M2 — Marketing-quality output
- ✅ Declarative config file (`apppreview.yaml`) for batch runs across targets and locales (#10).
- ✅ Templates: background colours/gradients, captions, fonts, layout (#11).
- Device frames, for screenshots and later videos (#12).
- ✅ Localization: per-locale fonts, RTL and CJK layout, store locale codes (#13).
- ✅ Google feature graphic, TV banner, and icon generation/validation (#14).

### M3 — Workflow integration
- Output layouts for fastlane `deliver`/`supply` and Gradle Play Publisher.
- Capture from iOS Simulator (`xcrun simctl`) and Android emulator (`adb`).
- MCP server so AI agents can call targets/validate/generate as tools.
- App preview videos (#21).
- Distribution: PyPI, `pipx`/`uvx`, Homebrew; GitHub Action.
- Spec freshness process (scheduled check against store docs).

## Spec sources
- Apple: https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications
- Google: https://support.google.com/googleplay/android-developer/answer/9866151
