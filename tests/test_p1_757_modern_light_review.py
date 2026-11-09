"""Offline privacy-safe contract for P1 #757's private GTK review sheets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image

# This is a standalone script, not a dependency on an installed e2e package.
import importlib.util

SCRIPT = Path(__file__).resolve().parents[1] / "e2e/modern_light_review.py"
SPEC = importlib.util.spec_from_file_location("modern_light_review", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)
build_review = module.build_review
verify_capture_folder = module.verify_capture_folder


def fixture(tmp_path: Path, *, width=64, height=40, stages=("dashboard",)):
    folder = tmp_path / f"modern-light-{width}"
    folder.mkdir()
    rows = []
    for index, mode in enumerate(("light", "dark")):
        name = f"{index:02d}-dashboard-{mode}.png"
        path = folder / name
        Image.new("RGB", (width, height), "white" if mode == "light" else "black").save(path)
        rows.append({
            "stage": "dashboard", "appearance": mode,
            "file": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    (folder / "screen.json").write_text(json.dumps({
        "status": "PASS", "width": width, "backend": "GTK4/X11",
        "git_sha": "a" * 40, "screenshots": rows,
    }), encoding="utf-8")
    return folder


def test_verified_synthetic_review_stays_private_and_never_auto_accepts(tmp_path):
    fixture(tmp_path)
    report = build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",),
                          tile_width=80)
    output = tmp_path / "modern-light-private-review"
    assert report["verified_original_captures"] == 2
    assert report["status"] == "VISUAL_REVIEW_REQUIRED"
    assert report["git_sha"] == "a" * 40
    assert (output / "modern-light-64-01.png").is_file()
    assert "ux_verdict" in (output / "ux-checklist.csv").read_text(encoding="utf-8")
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "ux-checklist.csv").stat().st_mode & 0o777 == 0o600


def test_tampered_capture_is_rejected(tmp_path):
    folder = fixture(tmp_path)
    Image.new("RGB", (64, 40), "red").save(folder / "00-dashboard-light.png")
    with pytest.raises(ValueError, match="empreinte"):
        verify_capture_folder(folder, width=64, size=(64, 40),
                              stages=("dashboard",))


def test_missing_or_duplicate_capture_is_rejected(tmp_path):
    folder = fixture(tmp_path)
    source = folder / "screen.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    report["screenshots"].append(report["screenshots"][0])
    source.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="répétée"):
        verify_capture_folder(folder, width=64, size=(64, 40),
                              stages=("dashboard",))


def test_mismatched_resolution_is_rejected(tmp_path):
    folder = fixture(tmp_path)
    with pytest.raises(ValueError, match="géométrie"):
        verify_capture_folder(folder, width=64, size=(64, 41),
                              stages=("dashboard",))


def test_unreviewed_private_sheet_is_not_a_ci_acceptance_flag(tmp_path):
    fixture(tmp_path)
    result = build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",))
    assert result["status"] != "PASS"
    assert result["status"] != "UX_ACCEPTED"


def test_filename_cannot_swap_a_light_capture_with_dark(tmp_path):
    folder = fixture(tmp_path)
    path = folder / "screen.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["screenshots"][0]["file"] = "01-dashboard-dark.png"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="incohérent"):
        verify_capture_folder(folder, width=64, size=(64, 40),
                              stages=("dashboard",))


def test_symlink_capture_is_rejected(tmp_path):
    folder = fixture(tmp_path)
    original = folder / "00-dashboard-light.png"
    backup = folder / "original-light.png"
    original.rename(backup)
    original.symlink_to(backup.name)
    with pytest.raises(ValueError, match="symboliquement"):
        verify_capture_folder(folder, width=64, size=(64, 40),
                              stages=("dashboard",))


def test_stale_head_is_rejected_before_generating_review(tmp_path):
    fixture(tmp_path)
    with pytest.raises(ValueError, match="HEAD différent"):
        build_review(tmp_path, sizes={64: (64, 40)},
                     stages=("dashboard",), expected_sha="b" * 40)
    assert not (tmp_path / "modern-light-private-review").exists()


def test_repeat_review_preserves_human_checklist_notes(tmp_path):
    fixture(tmp_path)
    arguments = {"sizes": {64: (64, 40)}, "stages": ("dashboard",)}
    build_review(tmp_path, **arguments)
    checklist = tmp_path / "modern-light-private-review" / "ux-checklist.csv"
    notes = checklist.read_text(encoding="utf-8") + "Observation UX locale conservée\\n"
    checklist.write_text(notes, encoding="utf-8")
    build_review(tmp_path, **arguments)
    assert checklist.read_text(encoding="utf-8") == notes


def test_new_head_never_overwrites_previous_human_review(tmp_path):
    folder = fixture(tmp_path)
    build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",))
    checklist = tmp_path / "modern-light-private-review" / "ux-checklist.csv"
    checklist.write_text("verdict privé non perdu\\n", encoding="utf-8")
    report_path = folder / "screen.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["git_sha"] = "b" * 40
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="autre HEAD"):
        build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",))
    assert checklist.read_text(encoding="utf-8") == "verdict privé non perdu\\n"


def test_symlinked_private_sheet_cannot_write_outside_review_directory(tmp_path):
    fixture(tmp_path)
    output = tmp_path / "modern-light-private-review"
    output.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"intact")
    (output / "modern-light-64-01.png").symlink_to(outside)
    with pytest.raises(ValueError, match="non sûre"):
        build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",),
                     tile_width=80)
    assert outside.read_bytes() == b"intact"
