"""Offline privacy-safe contract for P1 #757's private GTK review sheets."""
from __future__ import annotations

import csv
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


def _regenerate_same_head_capture(folder: Path) -> None:
    """A valid new screenshot with fresh screen.json digest but unchanged SHA."""
    capture = folder / "00-dashboard-light.png"
    Image.new("RGB", (64, 40), "red").save(capture)
    index = folder / "screen.json"
    report = json.loads(index.read_text(encoding="utf-8"))
    report["screenshots"][0]["sha256"] = hashlib.sha256(capture.read_bytes()).hexdigest()
    index.write_text(json.dumps(report), encoding="utf-8")


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


def _fill_private_review(root: Path, *, edit=None):
    output = root / "modern-light-private-review"
    path = output / "ux-checklist.csv"
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        columns = reader.fieldnames
        rows = list(reader)
    for row in rows:
        for field in module.REVIEW_AXES:
            row[field] = "OK"
        row["ux_verdict"] = "OK"
    if edit is not None:
        edit(rows)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_unreviewed_checklist_never_creates_verdict(tmp_path):
    fixture(tmp_path)
    build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",))
    with pytest.raises(ValueError, match="incomplète"):
        module.verify_completed_review(
            tmp_path, sizes={64: (64, 40)}, stages=("dashboard",),
        )
    assert not (tmp_path / "modern-light-private-review" / "visual-review-verdict.json").exists()


def test_same_head_recapture_cannot_reuse_completed_human_grid(tmp_path):
    folder = fixture(tmp_path)
    opts = {"sizes": {64: (64, 40)}, "stages": ("dashboard",)}
    original = build_review(tmp_path, **opts)
    checklist = _fill_private_review(tmp_path)
    private_notes = checklist.read_bytes()
    _regenerate_same_head_capture(folder)
    # screen.json and its SHA-256 now agree: this is a fresh, valid GTK run.
    verify_capture_folder(folder, width=64, size=(64, 40), stages=("dashboard",))
    with pytest.raises(ValueError, match="même HEAD"):
        build_review(tmp_path, **opts)
    assert checklist.read_bytes() == private_notes
    manifest = json.loads((checklist.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["capture_fingerprint_sha256"] == original["capture_fingerprint_sha256"]


def test_same_head_recapture_cannot_validate_stale_private_receipt(tmp_path):
    folder = fixture(tmp_path)
    opts = {"sizes": {64: (64, 40)}, "stages": ("dashboard",)}
    build_review(tmp_path, **opts)
    _fill_private_review(tmp_path)
    _regenerate_same_head_capture(folder)
    with pytest.raises(ValueError, match="même HEAD"):
        module.verify_completed_review(tmp_path, **opts)
    assert not (tmp_path / "modern-light-private-review"
                / "visual-review-verdict.json").exists()


def test_signed_originals_and_complete_human_grid_produce_private_bounded_receipt(tmp_path):
    fixture(tmp_path)
    build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",))
    checklist = _fill_private_review(tmp_path, edit=lambda rows: rows[0].update(
        notes="Chemin privé /home/operator/token=SECRET never exported",
    ))
    receipt = module.verify_completed_review(
        tmp_path, sizes={64: (64, 40)}, stages=("dashboard",),
        expected_sha="a" * 40,
    )
    assert receipt["status"] == "CHECKLIST_VERIFIED"
    assert receipt["reviewed_rows"] == 2
    assert receipt["findings"] == receipt["rows_with_exclusions"] == 0
    assert receipt["ready_for_ux_decision"] is True
    assert receipt["checklist_sha256"] == hashlib.sha256(checklist.read_bytes()).hexdigest()
    private = tmp_path / "modern-light-private-review" / "visual-review-verdict.json"
    assert private.is_file()
    assert private.stat().st_mode & 0o777 == 0o600
    assert "SECRET" not in private.read_text(encoding="utf-8")
    # No automatic ticket closure or false GTK / visual PASS in this receipt.
    assert "UX_ACCEPTED" not in private.read_text(encoding="utf-8")


def test_observed_ux_issue_is_never_accepted_as_clean(tmp_path):
    fixture(tmp_path)
    build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",))
    _fill_private_review(tmp_path, edit=lambda rows: rows[0].update(
        clipping="KO", ux_verdict="KO", notes="Bouton tronqué",
    ))
    verdict = module.verify_completed_review(
        tmp_path, sizes={64: (64, 40)}, stages=("dashboard",),
    )
    assert verdict["findings"] == 1
    assert verdict["ready_for_ux_decision"] is False


def test_ko_axis_cannot_be_disguised_by_ok_verdict(tmp_path):
    fixture(tmp_path)
    build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",))
    _fill_private_review(tmp_path, edit=lambda rows: rows[0].update(
        clipping="KO", ux_verdict="OK",
    ))
    with pytest.raises(ValueError, match="masquée"):
        module.verify_completed_review(
            tmp_path, sizes={64: (64, 40)}, stages=("dashboard",),
        )


def test_unexamined_visual_axis_needs_explicit_documented_exclusion(tmp_path):
    fixture(tmp_path)
    build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",))
    _fill_private_review(tmp_path, edit=lambda rows: rows[1].update(
        keyboard_focus="N/A",
    ))
    with pytest.raises(ValueError, match="sans justification"):
        module.verify_completed_review(
            tmp_path, sizes={64: (64, 40)}, stages=("dashboard",),
        )
    _fill_private_review(tmp_path, edit=lambda rows: rows[1].update(
        keyboard_focus="N/A", notes="Focus non applicable à cette vue",
    ))
    verdict = module.verify_completed_review(
        tmp_path, sizes={64: (64, 40)}, stages=("dashboard",),
    )
    assert verdict["rows_with_exclusions"] == 1
    assert verdict["ready_for_ux_decision"] is False


def test_stale_original_or_cross_head_review_is_rejected(tmp_path):
    folder = fixture(tmp_path)
    build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",))
    _fill_private_review(tmp_path)
    with pytest.raises(ValueError, match="HEAD différent"):
        module.verify_completed_review(
            tmp_path, sizes={64: (64, 40)}, stages=("dashboard",),
            expected_sha="b" * 40,
        )
    Image.new("RGB", (64, 40), "red").save(folder / "00-dashboard-light.png")
    with pytest.raises(ValueError, match="empreinte"):
        module.verify_completed_review(
            tmp_path, sizes={64: (64, 40)}, stages=("dashboard",),
        )


def test_duplicate_ux_row_is_rejected_and_no_receipt_written(tmp_path):
    fixture(tmp_path)
    build_review(tmp_path, sizes={64: (64, 40)}, stages=("dashboard",))
    checklist = _fill_private_review(tmp_path)
    with checklist.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        columns, rows = reader.fieldnames, list(reader)
    with checklist.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows + rows[:1])
    with pytest.raises(ValueError, match="répétée"):
        module.verify_completed_review(
            tmp_path, sizes={64: (64, 40)}, stages=("dashboard",),
        )
    assert not (tmp_path / "modern-light-private-review" / "visual-review-verdict.json").exists()
