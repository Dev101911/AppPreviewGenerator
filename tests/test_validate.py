from dataclasses import replace

from apppreview import get_target, identify, validate_images


def codes(report):
    return {i.code for i in report.all_issues}


def test_valid_apple_screenshot(make_image):
    report = validate_images([make_image()], "apple.iphone-6.9")
    assert report.ok
    assert report.to_dict()["summary"]["errors"] == 0


def test_reports_every_problem(make_image):
    path = make_image("bad.png", size=(1000, 2000), mode="RGBA")
    report = validate_images([path], "apple.iphone-6.9")
    assert not report.ok
    assert {"alpha", "dimensions"} <= codes(report)


def test_format_detected_from_content(make_image):
    path = make_image("shot.png", fmt="JPEG")
    report = validate_images([path], "apple.iphone-6.9")
    assert report.ok
    assert "extension_mismatch" in codes(report)


def test_disallowed_format(make_image):
    path = make_image("shot.webp", size=(512, 512))
    report = validate_images([path], "google.icon")
    assert "format" in codes(report)


def test_icon_allows_alpha(make_image):
    report = validate_images([make_image("icon.png", size=(512, 512), mode="RGBA")], "google.icon")
    assert report.ok


def test_missing_and_unreadable(tmp_path):
    junk = tmp_path / "junk.png"
    junk.write_text("not an image")
    report = validate_images([tmp_path / "nope.png", junk], "apple.iphone-6.9")
    assert {"not_found", "unreadable"} <= codes(report)


def test_count_limits(make_image):
    paths = [make_image(f"{i}.png", size=(1080, 1920)) for i in range(9)]
    assert "count" in codes(validate_images(paths, "google.phone"))
    one = validate_images(paths[:1], "google.phone")
    assert one.ok  # too few is a warning, not an error
    assert "count" in codes(one)


def test_google_promotion_recommendations(make_image):
    report = validate_images([make_image(size=(720, 1280)), make_image("b.png", size=(720, 1280))], "google.phone")
    assert report.ok
    assert {"recommended_size", "recommended_count"} <= codes(report)


def test_watch_sizes_must_match(make_image):
    paths = [make_image("a.png", size=(422, 514)), make_image("b.png", size=(416, 496))]
    report = validate_images(paths, "apple.watch")
    assert "mixed_sizes" in codes(report)


def test_file_size_limit(make_image):
    target = replace(get_target("google.icon"), max_bytes=10)
    report = validate_images([make_image("icon.png", size=(512, 512))], target)
    assert "file_size" in codes(report)


def test_identify(make_image):
    matches = identify(make_image(size=(1080, 1920)))
    assert "google.phone" in matches
    assert "apple.iphone-6.9" not in matches
    assert identify(make_image(), store="apple") == ["apple.iphone-6.9"]
