import json

from click.testing import CliRunner

from apppreview.cli import main


def run(*args):
    return CliRunner().invoke(main, [str(a) for a in args])


def test_targets_json():
    result = run("targets", "--store", "google", "--json")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {t["store"] for t in data} == {"google"}


def test_target_detail_json():
    data = json.loads(run("targets", "apple.iphone-6.9", "--json").output)
    assert data["sizes"][0] == [1320, 2868]
    assert data["count"] == {"min": 1, "max": 10}


def test_unknown_target_exit_code():
    result = run("targets", "nope", "--json")
    assert result.exit_code == 2
    assert json.loads(result.output)["error"]["code"] == "unknown_target"


def test_validate_exit_codes(make_image):
    good = run("validate", make_image(), "--target", "apple.iphone-6.9", "--json")
    assert good.exit_code == 0
    assert json.loads(good.output)["ok"] is True
    bad = run("validate", make_image("b.png", size=(10, 10)), "-t", "apple.iphone-6.9")
    assert bad.exit_code == 1
    assert "dimensions" in bad.output


def test_generate_then_validate(make_image, tmp_path):
    out = tmp_path / "out"
    result = run("generate", make_image(size=(1080, 1920)), "-t", "apple.iphone-6.9", "-o", out, "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    path = data["outputs"][0]["path"]
    assert run("validate", path, "-t", "apple.iphone-6.9").exit_code == 0


def test_generate_usage_error_json(make_image, tmp_path):
    result = run("generate", make_image(), "-t", "apple.iphone-6.9", "--size", "1x1", "--json")
    assert result.exit_code == 2
    assert json.loads(result.output)["error"]["code"] == "invalid_size"


def test_identify(make_image):
    data = json.loads(run("identify", make_image(), "--json").output)
    assert "apple.iphone-6.9" in next(iter(data["matches"].values()))
