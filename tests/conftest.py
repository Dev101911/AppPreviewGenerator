import pytest
from PIL import Image


@pytest.fixture
def make_image(tmp_path):
    """Create an image file and return its path."""

    def _make(name="shot.png", size=(1320, 2868), mode="RGB", color=(30, 120, 200), fmt=None):
        path = tmp_path / name
        if mode == "RGBA" and len(color) == 3:
            color = (*color, 128)
        Image.new(mode, size, color).save(path, fmt)
        return path

    return _make
