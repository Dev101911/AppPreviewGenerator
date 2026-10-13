import asyncio
import json

import pytest
from PIL import Image

from apppreview import mcp_server as tools

pytest.importorskip("mcp")


def test_server_registers_every_tool():
    server = tools.create_server()
    listed = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(listed) == {fn.__name__ for fn in tools.READ_ONLY + tools.WRITES}
    generate = listed["generate"]
    schema = getattr(generate, "input_schema", None) or generate.inputSchema
    assert set(schema["required"]) == {"inputs", "targets"}
    hints = generate.annotations
    assert getattr(hints, "read_only_hint", getattr(hints, "readOnlyHint", None)) is False
    validate_hints = listed["validate"].annotations
    assert getattr(validate_hints, "read_only_hint", getattr(validate_hints, "readOnlyHint", None)) is True


def test_call_through_server():
    server = tools.create_server()
    result = asyncio.run(server.call_tool("get_target", {"target_id": "apple.ipad-13"}))
    text = result.content[0].text if hasattr(result, "content") else result[0].text
    assert json.loads(text)["target"]["sizes"][0] == [2064, 2752]


def test_errors_are_returned_as_data():
    result = tools.get_target("nope")
    assert result == {"ok": False, "error": {"code": "unknown_target", "message": result["error"]["message"]}}


def test_list_targets_filters():
    previews = tools.list_targets(store="apple", kind="video")["targets"]
    assert previews and all(t["id"].endswith("-preview") for t in previews)
    phone = next(t for t in tools.list_targets(store="google")["targets"] if t["id"] == "google.phone")
    assert phone["rule_based"] and phone["sizes"] == [[1080, 1920], [1920, 1080]]


def test_generate_validate_identify(tmp_path):
    src = tmp_path / "shot.png"
    Image.new("RGB", (1179, 2556), "white").save(src)
    result = tools.generate([str(src)], ["apple.iphone-6.9"], str(tmp_path / "out"), template="dark", captions=["Hi"])
    assert result["ok"], result
    path = result["outputs"][0]["path"]
    assert tools.validate([path], "apple.iphone-6.9")["ok"]
    assert "apple.iphone-6.9" in tools.identify([path], store="apple")["matches"][path]


def test_build_init_schema_and_locales(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert tools.init_config()["ok"]
    assert tools.init_config()["error"]["code"] == "file_exists"
    assert tools.build(dry_run=True)["error"]["code"] == "input_not_found"
    assert "screens" in tools.schema("config")["schema"]["properties"]
    assert tools.locales("he")["stores"] == {"apple": "he", "google": "iw-IL"}
    assert {t["name"] for t in tools.list_templates()["templates"]} >= {"clean", "dark"}


def test_graphic(tmp_path):
    result = tools.graphic("google.feature-graphic", "My App", out=str(tmp_path))
    assert result["ok"] and result["outputs"][0]["width"] == 1024


def test_capture_reports_missing_tool(tmp_path, monkeypatch):
    monkeypatch.setattr("apppreview.capture.shutil.which", lambda name: None)
    assert tools.capture("ios", str(tmp_path / "a.png"))["error"]["code"] == "tool_not_found"
