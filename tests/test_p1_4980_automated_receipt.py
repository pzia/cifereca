"""Synthetic receipt is not a human review."""
import sys
import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
import notice_c_automated_receipt as receipt

SHA = "a" * 40


def rows(count):
    return [dict(width="1366", stage=str(i), appearance=mode,
                 sha256=digest * 64)
            for i in range(count // 2)
            for mode, digest in (("light", "b"), ("dark", "c"))]


@pytest.fixture
def matrix(monkeypatch):
    generic = Mock(return_value=(rows(128), "b" * 64))
    bnf = Mock(return_value=(rows(96), "c" * 64))
    aba = Mock()
    monkeypatch.setattr(receipt.generic, "verify_captures", generic)
    monkeypatch.setattr(receipt.bnf, "inventory", bnf)
    monkeypatch.setattr(receipt, "native_aba_report", aba)
    monkeypatch.setattr(receipt, "clean_head", lambda: SHA)
    monkeypatch.setattr(receipt, "verify_native_titles", Mock())
    monkeypatch.setattr(receipt, "capture_pixel_digest", lambda root, row, family: row["sha256"])
    return SimpleNamespace(generic=generic, bnf=bnf, aba=aba)


def test_closed_receipt(tmp_path, matrix):
    result = receipt.verify(tmp_path, SHA)
    assert result == dict(status="SYNTHETIC_GTK_MATRIX_VERIFIED", git_sha=SHA,
                          capture_count=224, native_aba_surfaces_per_configuration=12,
                          generic_fingerprint="b" * 64, bnf_fingerprint="c" * 64,
                          human_visual_review=False)
    assert matrix.aba.call_count == 4
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("family,count", [("generic", 127), ("bnf", 95)])
def test_missing_capture_refused(tmp_path, matrix, family, count):
    getattr(matrix, family).return_value = ([{}] * count, "d" * 64)
    with pytest.raises(receipt.CaptureError):
        receipt.verify(tmp_path, SHA)


def test_aba_failure_refused(tmp_path, matrix):
    matrix.aba.side_effect = receipt.CaptureError("invalid native report")
    with pytest.raises(receipt.CaptureError):
        receipt.verify(tmp_path, SHA)


def test_head_changed_refused(tmp_path, matrix, monkeypatch):
    monkeypatch.setattr(receipt, "clean_head", lambda: "f" * 40)
    with pytest.raises(receipt.CaptureError):
        receipt.verify(tmp_path, SHA)
    with pytest.raises(receipt.CaptureError):
        receipt.run(sys.executable, SHA)


def test_identical_theme_refused(tmp_path, matrix):
    captures = rows(128)
    captures[1]["sha256"] = captures[0]["sha256"]
    matrix.generic.return_value = (captures, "b" * 64)
    with pytest.raises(receipt.CaptureError):
        receipt.verify(tmp_path, SHA)


def test_invalid_fingerprint_refused(tmp_path, matrix):
    matrix.bnf.return_value = (rows(96), "not-a-digest")
    with pytest.raises(receipt.CaptureError):
        receipt.verify(tmp_path, SHA)


def test_png_metadata_cannot_fake_distinct_theme_pixels(tmp_path, monkeypatch):
    from PIL import Image, PngImagePlugin

    def png(color, note):
        out = BytesIO()
        info = PngImagePlugin.PngInfo()
        info.add_text("note", note)
        Image.new("RGB", (4, 4), color).save(out, format="PNG", pnginfo=info)
        return out.getvalue()

    light = png("white", "light")
    dark = png("white", "dark")
    sha = lambda raw: hashlib.sha256(raw).hexdigest()
    assert sha(light) != sha(dark)
    captures = [
        dict(width="1366", stage="test", appearance=mode, sha256=sha(raw))
        for mode, raw in (("light", light), ("dark", dark))
    ]
    samples = {"light": light, "dark": dark}
    monkeypatch.setattr(
        receipt, "capture_pixel_digest",
        lambda root, row, family: receipt.pixel_digest(
            samples[row["appearance"]], row["sha256"]),
    )
    with pytest.raises(receipt.CaptureError, match="identical theme pair"):
        receipt.verify_theme_pairs(captures, tmp_path, "generic")
    samples["dark"] = png("black", "dark")
    captures[1]["sha256"] = sha(samples["dark"])
    receipt.verify_theme_pairs(captures, tmp_path, "generic")
    with pytest.raises(receipt.CaptureError, match="changed after validation"):
        receipt.pixel_digest(light, sha(samples["dark"]))


def test_title_geometry_and_contrast_refuse_negative_witnesses():
    from PIL import Image
    from notice_c_native_readability import require_inside, check_pixels
    require_inside((1, 1, 8, 8), (0, 0, 10, 10))
    for clipped in ((-1, 1, 8, 8), (1, 1, 10, 8), (1, 1, 8, 10), (1, 1, 0, 8)):
        with pytest.raises(AssertionError, match="clipped"):
            require_inside(clipped, (0, 0, 10, 10))
    for background, foreground in ((255, 0), (0, 255)):
        image = Image.new("RGB", (12, 12), (background,) * 3)
        with pytest.raises(AssertionError, match="contrast or paint"):
            check_pixels(image, (foreground,) * 3)
        for x in range(4):
            image.putpixel((x + 3, 5), (foreground,) * 3)
        check_pixels(image, (foreground,) * 3)
    image = Image.new("RGB", (12, 12), (255,) * 3)
    for x in range(4):
        image.putpixel((x + 3, 5), (190,) * 3)
    with pytest.raises(AssertionError, match="contrast or paint"):
        check_pixels(image, (190,) * 3)


@pytest.mark.parametrize("bad", [None, False, 1, "true"])
def test_native_title_receipt_refuses_missing_or_nonboolean(tmp_path, monkeypatch, bad):
    import json
    payload = dict(git_sha=SHA, status="PASS", checks={
        "synthetic_native_title_readable": True,
        "synthetic_native_title_0": True,
        "synthetic_native_title_1": bad,
    })
    read = lambda *args: json.dumps(payload).encode()
    monkeypatch.setattr(receipt.generic, "read_regular", read)
    monkeypatch.setattr(receipt.bnf, "read_regular", read)
    with pytest.raises(receipt.CaptureError, match="missing native title witness"):
        receipt.verify_native_titles(tmp_path, SHA)
    payload["checks"]["synthetic_native_title_1"] = True
    receipt.verify_native_titles(tmp_path, SHA)
