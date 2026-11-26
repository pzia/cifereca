"""Offline contract of the private #4980 visual receipt (no GTK or network)."""
import csv
import importlib.util
import json
from pathlib import Path

from PIL import Image
import pytest

PATH = Path(__file__).resolve().parents[1] / "e2e/notice_c_private_review.py"
SPEC = importlib.util.spec_from_file_location("notice_c_private_review", PATH)
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)
HEAD = "a" * 40


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "captures"
    for mode in review.MODES:
        for width, size in review.SIZES.items():
            folder = root / mode / f"book-detail-{width}"
            folder.mkdir(parents=True)
            image = Image.new("RGB", size, (255, 255, 255))
            image.putpixel((0, 0), (0, 0, 0))
            steps = []
            for stage in review.STAGES:
                path = folder / (stage + ".png")
                image.save(path)
                steps.append(dict(stage=stage, file=path.name, appearance=mode,
                                  sha256=review.digest(path.read_bytes())))
            report = dict(status="PASS", scenario="book-detail-gtk", backend="GTK4/X11/XTEST",
                          git_sha=HEAD, width=width, local_c_stage="complete", steps=steps,
                          checks=dict.fromkeys(review.CHECKS, True))
            for phase, gets in (("cold", 1), ("offline", 0)):
                report["notice_c_shared_six_views_" + phase] = dict(
                    routes=list(review.ROUTES), views=12, same_document=True,
                    same_epoch=True, image_gets_total=1)
                report["notice_c_cover_metrics_" + phase] = dict(
                    fixture="one_c_six_views_mock_transport", n3={"download_ok": gets})
            (folder / "screen.json").write_text(json.dumps(report))
    return root, tmp_path / "review"


def test_receipt_requires_complete_independent_checklist_and_rechecks_bytes(fixture):
    root, output = fixture
    assert review.build(root, output, HEAD) == 124
    with pytest.raises(review.ReviewError):
        review.verify_review(root, output, HEAD)
    path = output / "review.csv"
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row.update(verdict="OK", notes="PRIVATE_NOTE")
    with path.open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=review.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    result = review.verify_review(root, output, HEAD)
    assert result["capture_count"] == 124
    assert "PRIVATE_NOTE" not in repr(result)
    png = root / "light/book-detail-1366" / (review.STAGES[0] + ".png")
    png.write_bytes(png.read_bytes() + b"changed")
    with pytest.raises(review.ReviewError):
        review.verify_review(root, output, HEAD)


@pytest.mark.parametrize("change", ["wrong_head", "wrong_theme", "duplicate", "false_check",
                                    "false_epoch", "private_path"])
def test_refuses_unqualified_capture(fixture, change):
    root, _ = fixture
    path = root / "light/book-detail-1366/screen.json"
    report = json.loads(path.read_text())
    if change == "wrong_head":
        report["git_sha"] = "b" * 40
    elif change == "wrong_theme":
        report["steps"][0]["appearance"] = "dark"
    elif change == "duplicate":
        report["steps"].append(report["steps"][0])
    elif change == "false_check":
        report["checks"][review.CHECKS[0]] = 1
    elif change == "false_epoch":
        report["notice_c_shared_six_views_cold"]["same_epoch"] = False
    else:
        report["steps"][0]["file"] = "../private.png"
    path.write_text(json.dumps(report))
    with pytest.raises(review.ReviewError):
        review.verify_captures(root, HEAD)
