import json

import pytest
import yaml
from PIL import Image

from apppreview.config import build, config_schema, load_config, localized, pick_for_target
from apppreview.errors import AppPreviewError


@pytest.fixture
def project(tmp_path):
    """A project with en-US/de-DE screenshots and an iPad-specific source."""
    for locale in ("en-US", "de-DE"):
        (tmp_path / "shots" / locale).mkdir(parents=True)
        Image.new("RGB", (1179, 2556), "white").save(tmp_path / "shots" / locale / "home.png")
        Image.new("RGB", (1179, 2556), "gray").save(tmp_path / "shots" / locale / "list.png")
    Image.new("RGB", (2064, 2752), "white").save(tmp_path / "shots" / "ipad.png")

    def write(**overrides):
        config = {
            "out": "out",
            "targets": ["apple.iphone-6.9", "apple.ipad-13", "google.phone"],
            "locales": ["en-US", "de-DE"],
            "template": "clean",
            "screens": [
                {
                    "id": "home",
                    "source": {"apple.ipad-*": "shots/ipad.png", "default": "shots/{locale}/home.png"},
                    "caption": {"en": "Hello", "de-DE": "Hallo"},
                },
                {"id": "list", "source": "shots/{locale}/list.png", "caption": "Lists"},
            ],
            **overrides,
        }
        path = tmp_path / "apppreview.yaml"
        path.write_text(yaml.safe_dump(config))
        return path

    return write


def test_build_all(project, tmp_path):
    result = build(project())
    assert result.ok
    assert len(result.outputs) == 3 * 2 * 2
    assert (tmp_path / "out" / "de-DE" / "apple.ipad-13" / "01_home.png").is_file()
    ipad = next(o for o in result.outputs if o.target == "apple.ipad-13" and o.screen == "home")
    assert ipad.source.endswith("ipad.png")
    assert {o.locale for o in result.outputs} == {"en-US", "de-DE"}


def test_filters_and_dry_run(project, tmp_path):
    result = build(project(), only_targets=["google.phone"], only_locales=["en-US"], dry_run=True)
    assert len(result.outputs) == 2
    assert not (tmp_path / "out").exists()


def test_no_locales_and_no_template(project, tmp_path):
    result = build(project(locales=[], template=None, screens=[{"id": "home", "source": "shots/en-US/home.png"}]))
    assert result.ok
    assert (tmp_path / "out" / "google.phone" / "01_home.png").is_file()


def test_missing_sources_listed_together(project):
    with pytest.raises(AppPreviewError) as exc:
        build(project(screens=[{"id": "a", "source": "nope1.png"}, {"id": "b", "source": "nope2.png"}]))
    assert exc.value.code == "input_not_found"
    assert "nope1.png" in exc.value.message and "nope2.png" in exc.value.message


def test_template_not_allowed_for_wear_os(project):
    with pytest.raises(AppPreviewError) as exc:
        build(project(targets=["google.wear-os"]))
    assert exc.value.code == "template_not_allowed"


@pytest.mark.parametrize(
    "overrides",
    [{"targets": []}, {"screens": [{"id": "bad id!", "source": "x.png"}]}, {"extra": True}, {"mode": "zoom"}],
)
def test_invalid_config(project, overrides):
    with pytest.raises(AppPreviewError) as exc:
        load_config(project(**overrides))
    assert exc.value.code == "invalid_config"


def test_missing_config(tmp_path):
    with pytest.raises(AppPreviewError) as exc:
        load_config(tmp_path)
    assert exc.value.code == "config_not_found"


def test_localized_fallbacks():
    text = {"en": "Hello", "de-DE": "Hallo", "default": "Hi"}
    assert localized(text, "en-GB", "x") == "Hello"
    assert localized(text, "de-DE", "x") == "Hallo"
    assert localized(text, "fr-FR", "x") == "Hi"
    with pytest.raises(AppPreviewError):
        localized({"en": "Hello"}, "fr-FR", "x")


def test_pick_for_target():
    mapping = {"apple.ipad-*": "ipad", "google.phone": "phone", "default": "other"}
    assert pick_for_target(mapping, "apple.ipad-13") == "ipad"
    assert pick_for_target(mapping, "google.phone") == "phone"
    assert pick_for_target(mapping, "apple.iphone-6.9") == "other"


def test_schema_is_json():
    schema = config_schema()
    json.dumps(schema)
    assert "screens" in schema["properties"]
