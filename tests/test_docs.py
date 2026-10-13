"""The docs page is generated from the code; make sure it stays complete."""

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_builder():
    spec = importlib.util.spec_from_file_location("build_docs", ROOT / "scripts" / "build_docs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def codes_in_source():
    usage, issues = set(), set()
    for path in (ROOT / "src" / "apppreview").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            first = node.args[0] if node.args else None
            if node.func.id == "AppPreviewError" and isinstance(first, ast.Constant):
                usage.add(first.value)
            if node.func.id == "Issue" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                issues.add(node.args[1].value)
    return usage, issues


def test_every_code_is_documented():
    docs = load_builder()
    usage, issues = codes_in_source()
    assert set(docs.USAGE_CODES) == usage, "update USAGE_CODES in scripts/build_docs.py"
    assert set(docs.ISSUE_CODES) == issues, "update ISSUE_CODES in scripts/build_docs.py"


def test_every_command_has_examples():
    docs = load_builder()
    names = {name for name, _ in docs._commands()}
    assert names == set(docs.EXAMPLES), "add examples for new commands in scripts/build_docs.py"


def test_page_renders_completely():
    docs = load_builder()
    page = docs.render()
    assert "<!-- @" not in page
    for name, _ in docs._commands():
        assert f'id="cmd-{docs.slug(name)}"' in page
    for code in docs.USAGE_CODES:
        assert f"<code>{code}</code>" in page
