import pytest

from apppreview.errors import AppPreviewError
from apppreview.specs import get_target, load_targets, size_problems


def test_every_target_is_well_formed():
    targets = load_targets()
    assert len(targets) >= 25
    for t in targets.values():
        assert t.id.startswith(f"{t.store}.")
        assert t.output_sizes, f"{t.id} has no output size"
        assert t.count_min <= t.count_max
        assert set(t.formats) <= {"png", "jpeg"}
        assert t.verified and t.source.startswith("https://")
        for size in t.output_sizes:
            assert t.accepts_size(*size), f"{t.id} rejects its own size {size}"
        if t.fallback:
            assert t.fallback in targets


def test_apple_exact_sizes():
    t = get_target("apple.iphone-6.9")
    assert t.accepts_size(1320, 2868)
    assert t.accepts_size(2868, 1320)
    assert not t.accepts_size(1320, 2867)


def test_google_constraints():
    t = get_target("google.phone")
    assert t.accepts_size(1080, 1920)
    assert t.accepts_size(320, 640)
    assert not t.accepts_size(300, 600)  # below min side
    assert not t.accepts_size(1000, 2100)  # aspect > 2:1
    assert not t.accepts_size(2000, 4000)  # above max side
    tablet = get_target("google.tablet-10")
    assert tablet.accepts_size(1440, 2560)
    assert size_problems(tablet, 1600, 2560)  # not 9:16


def test_unknown_target():
    with pytest.raises(AppPreviewError) as exc:
        get_target("apple.iphone-99")
    assert exc.value.code == "unknown_target"
