import pytest
from PIL import Image

from apppreview import generate
from apppreview.errors import AppPreviewError


def test_generates_compliant_output_for_several_targets(make_image, tmp_path):
    src = make_image("home.png", size=(1179, 2556), mode="RGBA")
    result = generate([src], ["apple.iphone-6.9", "apple.ipad-13", "google.phone"], tmp_path / "out")
    assert result.ok
    sizes = {o.target: (o.width, o.height) for o in result.outputs}
    assert sizes == {
        "apple.iphone-6.9": (1320, 2868),
        "apple.ipad-13": (2064, 2752),
        "google.phone": (1080, 1920),
    }
    out = tmp_path / "out" / "apple.iphone-6.9" / "01_home.png"
    with Image.open(out) as img:
        assert img.mode == "RGB"


def test_picks_landscape_size_for_landscape_input(make_image, tmp_path):
    src = make_image(size=(2556, 1179))
    result = generate([src], ["apple.iphone-6.9"], tmp_path)
    assert (result.outputs[0].width, result.outputs[0].height) == (2868, 1320)


def test_exact_match_is_kept(make_image, tmp_path):
    src = make_image(size=(1260, 2736))
    result = generate([src], ["apple.iphone-6.9"], tmp_path)
    assert (result.outputs[0].width, result.outputs[0].height) == (1260, 2736)
    assert result.outputs[0].scale == 1


@pytest.mark.parametrize("mode", ["fit", "fill", "stretch"])
def test_modes(make_image, tmp_path, mode):
    src = make_image(size=(800, 800))
    result = generate([src], ["google.phone"], tmp_path, mode=mode)
    assert result.ok


def test_fit_letterboxes_with_background(make_image, tmp_path):
    src = make_image(size=(1000, 1000), color=(255, 0, 0))
    generate([src], ["google.phone"], tmp_path, background="#00ff00")
    with Image.open(tmp_path / "google.phone" / "01_shot.png") as img:
        assert img.getpixel((540, 5)) == (0, 255, 0)
        assert img.getpixel((540, 960)) == (255, 0, 0)


def test_auto_background(make_image, tmp_path):
    src = make_image(size=(1000, 1000), color=(10, 20, 30))
    generate([src], ["google.phone"], tmp_path, background="auto")
    with Image.open(tmp_path / "google.phone" / "01_shot.png") as img:
        assert img.getpixel((540, 5)) == (10, 20, 30)


def test_jpeg_and_explicit_size(make_image, tmp_path):
    result = generate([make_image()], ["apple.iphone-6.9"], tmp_path, fmt="jpeg", size="1290x2796")
    assert result.ok
    assert result.outputs[0].path.endswith(".jpg")
    assert (result.outputs[0].width, result.outputs[0].height) == (1290, 2796)


def test_dry_run_writes_nothing(make_image, tmp_path):
    out = tmp_path / "out"
    result = generate([make_image()], ["apple.iphone-6.9"], out, dry_run=True)
    assert result.dry_run and result.outputs
    assert not out.exists()


def test_upscale_warning(make_image, tmp_path):
    result = generate([make_image(size=(300, 650))], ["apple.iphone-6.9"], tmp_path)
    assert any("upscaled" in w for w in result.outputs[0].warnings)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"size": "100x100"}, "invalid_size"),
        ({"size": "big"}, "invalid_size"),
        ({"fmt": "jpeg", "targets": ["google.icon"]}, "invalid_format"),
        ({"background": "notacolor"}, "invalid_color"),
        ({"targets": ["nope"]}, "unknown_target"),
    ],
)
def test_usage_errors(make_image, tmp_path, kwargs, code):
    args = {"targets": ["apple.iphone-6.9"], "out_dir": tmp_path, **kwargs}
    with pytest.raises(AppPreviewError) as exc:
        generate([make_image()], **args)
    assert exc.value.code == code


def test_too_many_images(make_image, tmp_path):
    paths = [make_image(f"{i}.png") for i in range(11)]
    with pytest.raises(AppPreviewError) as exc:
        generate(paths, ["apple.iphone-6.9"], tmp_path)
    assert exc.value.code == "too_many_images"


def test_missing_input(tmp_path):
    with pytest.raises(AppPreviewError) as exc:
        generate([tmp_path / "missing.png"], ["apple.iphone-6.9"], tmp_path)
    assert exc.value.code == "input_not_found"
