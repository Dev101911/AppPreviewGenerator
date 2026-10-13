"""Turn an apppreview --json result into a Markdown job summary (used by action.yml)."""

import json
import sys


def main(path: str, args: str) -> None:
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        print(f"### apppreview `{args}`\n\nNo JSON result was produced.")
        return
    if not isinstance(data, dict):
        print(f"### apppreview `{args}`\n\n{len(data)} item(s) returned.")
        return
    status = "✅ passed" if data.get("ok", True) else "❌ failed"
    print(f"### apppreview `{args}`: {status}\n")
    if "error" in data:
        print(f"**{data['error']['code']}**: {data['error']['message']}")
        return
    reports = data.get("validation") or ([data] if "files" in data else [])
    if reports:
        print("| Target | Locale | Files | Errors | Warnings |\n|---|---|---|---|---|")
        for r in reports:
            s = r.get("summary", {})
            cells = [
                r.get("target"),
                r.get("locale") or "",
                s.get("files", 0),
                s.get("errors", 0),
                s.get("warnings", 0),
            ]
            print("| " + " | ".join(str(c) for c in cells) + " |")
        problems = [
            (f["path"], i)
            for r in reports
            for f in r.get("files", [])
            for i in f.get("issues", [])
            if i["severity"] == "error"
        ]
        if problems:
            print("\n**Errors**\n")
            for path, issue in problems[:50]:
                print(f"- `{path}`: [{issue['code']}] {issue['message']}")
    if data.get("outputs"):
        print(f"\n{len(data['outputs'])} file(s) {'planned' if data.get('dry_run') else 'written'}.")
    for warning in data.get("warnings", []):
        print(f"- ⚠️ {warning}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
