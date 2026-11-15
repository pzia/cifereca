"""P1 #774: offline tests for paired PRIVATE GTK review; never use user data."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image

SOURCE = Path(__file__).resolve().parents[1] / "e2e/search_recommendations_review.py"
SPEC = importlib.util.spec_from_file_location("search_recommendations_review", SOURCE)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)

SIZE = (1024, 768)
SIZES = {1024: SIZE}
SHA = "a" * 40


def _one(root: Path, surface: str, sha: str = SHA, appearance: str = "light") -> Path:
    prefix, stage, field, backend, check, geom_key = module.SURFACES[surface]
    folder = root / f"{prefix}-1024"
    folder.mkdir()
    image = folder / f"{stage}.png"
    Image.new("RGB", SIZE, "white" if surface == "search" else "blue").save(image)
    report = {
        "scenario": (
            "search-filter-1024" if surface == "search"
            else "recommendations-detail-gtk"
        ),
        "status": "PASS", "backend": backend, "git_sha": sha,
        "appearance": appearance,
        "checks": {
            check: True,
            (
                "P1_774_search_selected_same_synthetic_book"
                if surface == "search" else
                "P1_774_recommendations_selected_same_synthetic_book"
            ): True,
        },
        "p1_774_paired_book": module.verified_fixture_receipt(),
        geom_key: {
            "display_width": 1024, "display_height": 768,
            "window_width": 984, "window_height": 700,
        },
        "steps": [{
            "stage": stage, field: image.name,
            "appearance": appearance,
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        }],
    }
    if surface == "recommendations":
        report.update(width=1024, scale=1)
    (folder / "screen.json").write_text(json.dumps(report), encoding="utf-8")
    return folder


def _pair(root: Path, *, second_sha: str = SHA,
          first_mode: str = "light", second_mode: str = "light") -> None:
    _one(root, "search", appearance=first_mode)
    _one(root, "recommendations", second_sha, appearance=second_mode)


def _refresh_image(folder: Path) -> None:
    report_path = folder / "screen.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    image = folder / report["steps"][0]["image"]
    Image.new("RGB", SIZE, "red").save(image)
    report["steps"][0]["sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report), encoding="utf-8")


def _human_review(root: Path, *, problem: str | None = None) -> Path:
    checklist = root / "search-recommendations-private-review/ux-checklist.csv"
    with checklist.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        names, rows = reader.fieldnames, list(reader)
    for row in rows:
        for axis in module.REVIEW_AXES:
            row[axis] = "OK"
        row["ux_verdict"] = "OK"
    if problem is not None:
        rows[0][problem] = "KO"
        rows[0]["ux_verdict"] = "KO"
    rows[0]["notes"] = "Contenu privé local secret à ne pas publier"
    with checklist.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)
    return checklist


def test_same_head_real_gtk_pair_generates_private_review_not_ux_acceptance(tmp_path):
    _pair(tmp_path)
    report = module.build_review(tmp_path, sizes=SIZES, expected_sha=SHA)
    private = tmp_path / "search-recommendations-private-review"
    assert report["status"] == "VISUAL_REVIEW_REQUIRED"
    assert report["verified_source_captures"] == 2
    assert report["git_sha"] == SHA
    assert report["appearance"] == "light"
    assert len(report["capture_fingerprint_sha256"]) == 64
    assert (private / "pair-1024.png").is_file()
    assert (private / "ux-checklist.csv").is_file()
    assert private.stat().st_mode & 0o777 == 0o700
    assert (private / "pair-1024.png").stat().st_mode & 0o777 == 0o600


def test_absent_shared_gtk_selection_proof_fails_closed(tmp_path):
    _pair(tmp_path)
    report_path = tmp_path / "search-filter-1024/screen.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    del report["checks"]["P1_774_search_selected_same_synthetic_book"]
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.ReviewError, match="Source/édition"):
        module.build_review(tmp_path, sizes=SIZES)
    assert not (tmp_path / "search-recommendations-private-review").exists()


def test_different_document_fingerprint_cannot_be_compared(tmp_path):
    _pair(tmp_path)
    path = tmp_path / "recommendations-detail-1024/screen.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["p1_774_paired_book"]["document_sha256"] = "b" * 64
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.ReviewError, match="Source/édition"):
        module.build_review(tmp_path, sizes=SIZES)


def test_different_public_source_or_record_is_not_same_book(tmp_path):
    _pair(tmp_path)
    path = tmp_path / "recommendations-detail-1024/screen.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["p1_774_paired_book"]["source_provider"] = "another-source"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.ReviewError, match="Source/édition"):
        module.build_review(tmp_path, sizes=SIZES)


def test_same_head_and_same_pixels_cannot_reuse_review_for_other_document(tmp_path):
    _pair(tmp_path)
    receipt = module.build_review(tmp_path, sizes=SIZES)
    assert receipt["document_sha256"] == module.verified_fixture_receipt()["document_sha256"]
    checklist = _human_review(tmp_path)
    original = checklist.read_bytes()
    path = tmp_path / "search-filter-1024/screen.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["p1_774_paired_book"]["record_id"] = "other-book"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.ReviewError, match="Source/édition"):
        module.verify_completed_review(tmp_path, sizes=SIZES)
    assert original == checklist.read_bytes()


def test_different_commits_cannot_be_compared(tmp_path):
    _pair(tmp_path, second_sha="b" * 40)
    with pytest.raises(module.ReviewError, match="HEAD"):
        module.build_review(tmp_path, sizes=SIZES)
    assert not (tmp_path / "search-recommendations-private-review").exists()


def test_invalid_or_missing_gtk_geometry_cannot_be_paired(tmp_path):
    _pair(tmp_path)
    path = tmp_path / "search-filter-1024/screen.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["p1_774_matched_geometry"]["display_width"] = 1600
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.ReviewError, match="Géométrie"):
        module.build_review(tmp_path, sizes=SIZES)


def test_hidpi_scenario_does_not_impersonate_matched_1x_capture(tmp_path):
    _pair(tmp_path)
    path = tmp_path / "recommendations-detail-1024/screen.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["scale"] = 2
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.ReviewError, match="logique"):
        module.build_review(tmp_path, sizes=SIZES)


def test_forged_or_missing_image_metadata_fails_closed(tmp_path):
    _pair(tmp_path)
    folder = tmp_path / "search-filter-1024"
    path = folder / "screen.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["steps"].append(dict(report["steps"][0]))
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.ReviewError, match="dupliquée"):
        module.build_review(tmp_path, sizes=SIZES)


def test_same_commit_new_pixels_with_new_valid_sha_cannot_reuse_human_grid(tmp_path):
    _pair(tmp_path)
    original = module.build_review(tmp_path, sizes=SIZES)
    checklist = _human_review(tmp_path)
    human_notes = checklist.read_bytes()
    _refresh_image(tmp_path / "recommendations-detail-1024")
    # New PNG and updated report hash are individually valid, but not reviewed.
    module._report_and_image(tmp_path, 1024, SIZE, "recommendations")
    with pytest.raises(module.ReviewError, match="modifiée"):
        module.build_review(tmp_path, sizes=SIZES)
    assert checklist.read_bytes() == human_notes
    assert original["status"] == "VISUAL_REVIEW_REQUIRED"
    with pytest.raises(module.ReviewError, match="correspondent"):
        module.verify_completed_review(tmp_path, sizes=SIZES)


def test_no_receipt_until_actual_complete_private_human_checklist(tmp_path):
    _pair(tmp_path)
    module.build_review(tmp_path, sizes=SIZES)
    private = tmp_path / "search-recommendations-private-review"
    with pytest.raises(module.ReviewError, match="incomplète"):
        module.verify_completed_review(tmp_path, sizes=SIZES)
    assert not (private / "visual-review-verdict.json").exists()


def test_completed_human_grid_produces_bounded_private_receipt_only(tmp_path):
    _pair(tmp_path)
    module.build_review(tmp_path, sizes=SIZES)
    checklist = _human_review(tmp_path)
    result = module.verify_completed_review(tmp_path, sizes=SIZES)
    assert result["status"] == "CHECKLIST_VERIFIED"
    assert result["appearance"] == "light"
    assert result["ready_for_ux_decision"] is True
    assert result["reviewed_widths"] == 1
    assert result["checklist_sha256"] == hashlib.sha256(checklist.read_bytes()).hexdigest()
    receipt = checklist.parent / "visual-review-verdict.json"
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert "Contenu privé" not in receipt.read_text(encoding="utf-8")
    assert "UX_ACCEPTED" not in receipt.read_text(encoding="utf-8")


def test_real_human_ko_cannot_be_accepted(tmp_path):
    _pair(tmp_path)
    module.build_review(tmp_path, sizes=SIZES)
    _human_review(tmp_path, problem="clipping")
    result = module.verify_completed_review(tmp_path, sizes=SIZES)
    assert result["findings"] == 1
    assert result["ready_for_ux_decision"] is False


def test_image_symlink_is_never_used(tmp_path):
    _pair(tmp_path)
    folder = tmp_path / "search-filter-1024"
    image = folder / "00-before.png"
    original = folder / "original.png"
    image.rename(original)
    image.symlink_to(original.name)
    with pytest.raises(module.ReviewError, match="non sûr"):
        module.build_review(tmp_path, sizes=SIZES)

def test_mixed_light_dark_captures_are_rejected_before_any_review(tmp_path):
    _pair(tmp_path, second_mode="dark")
    with pytest.raises(module.ReviewError, match="clair/sombre"):
        module.build_review(tmp_path, sizes=SIZES)
    assert not (tmp_path / "search-recommendations-private-review").exists()


def test_expected_dark_must_be_observed_not_only_declared(tmp_path):
    _pair(tmp_path)
    with pytest.raises(module.ReviewError, match="clair/sombre"):
        module.build_review(tmp_path, sizes=SIZES, expected_appearance="dark")


def test_wrong_stage_appearance_is_rejected(tmp_path):
    _pair(tmp_path)
    path = tmp_path / "search-filter-1024/screen.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["steps"][0]["appearance"] = "dark"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.ReviewError, match="Apparence"):
        module.build_review(tmp_path, sizes=SIZES)


def test_all_dark_capture_pair_is_supported(tmp_path):
    _pair(tmp_path, first_mode="dark", second_mode="dark")
    result = module.build_review(
        tmp_path, sizes=SIZES, expected_sha=SHA, expected_appearance="dark",
    )
    assert result["appearance"] == "dark"
    _human_review(tmp_path)
    receipt = module.verify_completed_review(
        tmp_path, sizes=SIZES, expected_appearance="dark",
    )
    assert receipt["appearance"] == "dark"


def test_same_head_same_pixels_new_mode_cannot_reuse_prior_human_verdict(tmp_path):
    _pair(tmp_path)
    module.build_review(tmp_path, sizes=SIZES)
    checklist = _human_review(tmp_path)
    original = checklist.read_bytes()
    # Even if someone relabels both synthetic JSON reports without changing
    # pixels, a mode switch is not the same UI review. Real probes report
    # Adwaita's observed state for each capture, never the requested label.
    for surface in ("search-filter", "recommendations-detail"):
        path = tmp_path / f"{surface}-1024/screen.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        report["appearance"] = "dark"
        for stage in report["steps"]:
            stage["appearance"] = "dark"
        path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.ReviewError, match="modifiée"):
        module.build_review(tmp_path, sizes=SIZES)
    assert checklist.read_bytes() == original
    with pytest.raises(module.ReviewError, match="correspondent"):
        module.verify_completed_review(tmp_path, sizes=SIZES)
