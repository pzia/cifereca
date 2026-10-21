"""P0 #2565 — local 320-file ceiling: isolate timings and candidate limits.

All catalogue/rights/books are synthetic. Never export a source path, SHA,
ISBN, private title, account or per-file timing. No network or database writes.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cifereca.core.library_inventory import InventoryLimits, scan_identity_roots
from cifereca.core.library_metadata_candidates import metadata_candidates
from cifereca.core.library_reconciliation import EvidenceSnapshot
from tests.test_library_reconciliation import epub, filesystem


def _epub_inventory(tmp_path):
    root = tmp_path / "PRIVATE_LIBRARY_2565"
    root.mkdir()
    # The synthetic OPF must declare an ISBN scheme explicitly: digits alone
    # cannot become identity/edition evidence or even a metadata candidate.
    epub(root / "PRIVATE_BOOK_2565.epub", identifier="urn:isbn:9780306406157")
    before = filesystem(tmp_path)
    report = scan_identity_roots({"PRIVATE_IDENTITY_2565": (root,)})
    assert filesystem(tmp_path) == before
    return report


def _catalogue(*rows):
    return EvidenceSnapshot({
        "catalog_entries": [
            {"identity_id": "PRIVATE_IDENTITY_2565",
             "bibliographic_identifiers": keys}
            for keys in rows
        ],
    })


def test_scan_phase_observations_are_aggregate_closed_and_read_only(tmp_path):
    inventory = _epub_inventory(tmp_path)
    result = inventory.redacted()
    assert result["status"] == "complete"
    assert result["files"] == result["files_started"] == 1
    assert result["hashed_files"] == 1
    assert result["phase_calls"] == {
        "sha256": 1, "epub_opf": 1, "structure": 1, "pdf_signature": 0,
    }
    assert set(result["phase_ms"]) == set(result["phase_calls"])
    assert all(type(v) is int and v >= 0 for v in result["phase_ms"].values())
    dumped = json.dumps(result, ensure_ascii=False)
    assert "PRIVATE" not in dumped
    assert inventory.files[0].sha256 not in dumped
    assert "9780306406157" not in dumped
    assert "PRIVATE" not in repr(inventory)


def test_many_small_files_accumulate_submillisecond_phases_before_rounding(tmp_path):
    # A millisecond rounded separately for each of 32 books could all be 0.
    root = tmp_path / "PRIVATE"
    root.mkdir()
    epub(root / "template.epub", identifier="9780306406157")
    raw = (root / "template.epub").read_bytes()
    for i in range(31):
        (root / f"{i}.epub").write_bytes(raw)
    before = filesystem(tmp_path)
    inventory = scan_identity_roots({"PRIVATE": (root,)})
    result = inventory.redacted()
    assert result["files"] == 32
    assert result["phase_calls"]["epub_opf"] == 32
    assert result["phase_calls"]["structure"] == 32
    assert result["phase_ms"]["epub_opf"] >= 0
    assert result["phase_ms"]["structure"] >= 0
    assert filesystem(tmp_path) == before


def test_started_but_unhashed_file_is_distinct_from_verified_file(tmp_path):
    root = tmp_path / "PRIVATE_ROOT"
    root.mkdir()
    epub(root / "private.epub")
    inventory = scan_identity_roots({"PRIVATE": (root,)},
                                    limits=InventoryLimits(file_bytes=1))
    redacted = inventory.redacted()
    assert redacted["files_started"] == 1
    assert redacted["files"] == 1
    assert redacted["hashed_files"] == 0
    assert redacted["phase_calls"]["sha256"] == 0
    assert "file_byte_budget" in redacted["reasons"]


def test_catalogue_candidate_records_closed_stage_counts_without_private_data(tmp_path):
    inventory = _epub_inventory(tmp_path)
    result = metadata_candidates(
        inventory, _catalogue([("isbn13", "9780306406157")]),
    )
    assert len(result.candidates) == 1
    report = result.redacted()
    assert report["catalog_rows_examined"] == 1
    assert report["references_examined"] == 1
    assert report["files_examined"] == 1
    assert report["index_ms"] >= 0 and report["match_ms"] >= 0
    assert report["certainty"] == "metadata_only"
    assert "PRIVATE" not in json.dumps(report)
    assert "9780306406157" not in json.dumps(report)


def test_cancelled_and_expired_budget_are_distinct_in_the_index_pass(tmp_path):
    inventory = _epub_inventory(tmp_path)
    candidate = _catalogue([("isbn13", "9780306406157")])
    stopped = metadata_candidates(inventory, candidate, cancelled=lambda: True)
    assert "metadata_candidate_cancelled" in stopped.reasons
    assert "metadata_candidate_time_budget" not in stopped.reasons
    with patch("cifereca.core.library_metadata_candidates.time.monotonic",
               side_effect=[0.0, 11.0]):
        late = metadata_candidates(inventory, candidate, seconds=10)
    assert "metadata_candidate_time_budget" in late.reasons
    assert "metadata_candidate_cancelled" not in late.reasons
    assert late.redacted()["catalog_rows_examined"] == 0


def test_expired_budget_during_file_matching_does_not_claim_no_candidates(tmp_path):
    inventory = _epub_inventory(tmp_path)
    with patch("cifereca.core.library_metadata_candidates.time.monotonic",
               side_effect=[0.0, 0.0, 11.0]):
        late = metadata_candidates(
            inventory, _catalogue([("isbn13", "9780306406157")]),
            seconds=10,
        )
    assert late.redacted()["catalog_rows_examined"] == 1
    assert late.redacted()["files_examined"] == 0
    assert late.candidates == []
    assert "metadata_candidate_time_budget" in late.reasons


def test_candidate_count_and_per_notice_identifier_caps_are_distinct(tmp_path):
    inventory = _epub_inventory(tmp_path)
    same = [("isbn13", "9780306406157")]
    candidate = metadata_candidates(inventory, _catalogue(same, same),
                                    max_candidates=1)
    assert candidate.candidates == []
    assert candidate.redacted()["catalog_rows_examined"] == 2
    assert "metadata_candidate_count_budget" in candidate.reasons
    too_many = metadata_candidates(inventory, _catalogue(same * 65))
    assert "metadata_candidate_keys_budget" in too_many.reasons
    assert "metadata_candidate_reference_budget" not in too_many.reasons


def test_reference_budget_and_catalogue_row_cap_have_unique_codes(tmp_path):
    inventory = _epub_inventory(tmp_path)
    # 1563 * 64 references > 100_000, but far below 100k catalogue rows.
    row = [("isbn13", "9780306406157")] * 64
    snapshot = _catalogue(*(row for _ in range(1563)))
    cap = metadata_candidates(inventory, snapshot)
    assert "metadata_candidate_reference_budget" in cap.reasons
    assert "metadata_candidate_row_budget" not in cap.reasons
    assert cap.redacted()["references_examined"] == 100001

    class OversizeCatalogue(list):
        def __len__(self):
            return 100001

    oversize = EvidenceSnapshot({"catalog_entries": OversizeCatalogue([
        {"identity_id": "PRIVATE_IDENTITY_2565",
         "bibliographic_identifiers": row[:1]},
    ])})
    limited = metadata_candidates(inventory, oversize)
    assert "metadata_candidate_row_budget" in limited.reasons
    assert "metadata_candidate_reference_budget" not in limited.reasons
    assert limited.redacted()["catalog_rows_examined"] == 1
