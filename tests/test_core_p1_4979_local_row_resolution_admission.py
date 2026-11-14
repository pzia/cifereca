"""P1 #4979 — an exact local Placement does not authorize stale cover facts.

Read-only synthetic candidate snapshots. Library selected-cover resolution and
the exact loan/Placement proof must agree; a corrupt row cannot hide an
independent genuine cover or create rights to another person's local file.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.core.loan_cover_fallback import loan_local_cover
from tests.test_core_p1_4979_exact_loan_cover import loan, local_row, remote_row


@pytest.mark.parametrize("bad", [
    None, "selected", [], ["selected"], {"status": "missing"},
    {"status": "selected", "tier": ["embedded"],
     "url": "file:///home/alice/.cache/cifereca/cover.png"},
    {"status": "selected", "tier": "unknown",
     "url": "file:///home/alice/.cache/cifereca/cover.png"},
    {"status": "selected", "tier": "embedded", "url": 1},
])
@pytest.mark.parametrize("bad_first", [False, True])
def test_corrupt_selected_local_cover_cannot_mask_independent_remote_origin(
    bad, bad_first,
):
    current = loan()
    corrupt = local_row(current)
    corrupt["metadata"]["cover_resolution"] = bad
    genuine, _ = remote_row(current, url="https://public.example/verified.jpg")
    rows = (corrupt, genuine) if bad_first else (genuine, corrupt)
    resolution = loan_local_cover(current, rows)
    assert resolution.status == "selected"
    assert resolution.source == "verified-remote-entitlement"
    assert resolution.tier == "origin"
    assert resolution.url == "https://public.example/verified.jpg"
    assert loan_local_cover(current, (corrupt,)).status == "missing"


@pytest.mark.parametrize("change", [
    "row_thumbnail", "row_selected_url", "row_selected_tier",
    "row_selected_status", "row_no_resolution", "row_no_metadata",
    "proof_url", "proof_tier", "proof_malformed_tier",
])
def test_rebound_or_stale_local_proof_does_not_reclaim_embedded_priority(change):
    current = loan()
    altered = local_row(current)
    if change == "row_thumbnail":
        altered["thumbnail_url"] = "file:///home/another/secret.png"
    elif change == "row_selected_url":
        altered["metadata"]["cover_resolution"]["url"] = "file:///home/another/secret.png"
    elif change == "row_selected_tier":
        altered["metadata"]["cover_resolution"]["tier"] = "origin"
    elif change == "row_selected_status":
        altered["metadata"]["cover_resolution"]["status"] = "ambiguous"
    elif change == "row_no_resolution":
        altered["metadata"]["cover_resolution"] = None
    elif change == "row_no_metadata":
        altered.pop("metadata")
    elif change == "proof_url":
        altered["_exact_loan_cover_proofs"][0]["url"] = "file:///home/another/secret.png"
    elif change == "proof_tier":
        altered["_exact_loan_cover_proofs"][0]["tier"] = "origin"
    elif change == "proof_malformed_tier":
        altered["_exact_loan_cover_proofs"][0]["tier"] = ["embedded"]

    remote, _ = remote_row(current, url="https://public.example/genuine.jpg")
    result = loan_local_cover(current, (remote, altered))
    assert result.status == "selected"
    assert result.tier == "origin"
    assert result.url == "https://public.example/genuine.jpg"
    assert len(result.candidates) == 1


def test_two_genuine_local_placements_preserve_existing_priority_and_ambiguity():
    current = loan()
    embedded = local_row(current, placement="local-embedded")
    catalog = local_row(
        current, url="https://public.example/catalog.jpg",
        tier="catalog", placement="local-catalog",
    )
    a = local_row(
        current, url="https://public.example/a.jpg",
        tier="origin", placement="local-a",
    )
    b = local_row(
        current, url="https://public.example/b.jpg",
        tier="origin", placement="local-b",
    )
    assert loan_local_cover(current, (catalog, embedded)).tier == "embedded"
    ambiguous = loan_local_cover(current, (a, b))
    assert ambiguous.status == "ambiguous" and ambiguous.url == ""
    for other in (
        loan(identity="bob"), loan(credential="card-b"),
        loan(account="borrower-b"), loan(record="other-edition"),
        loan(identifier="other-loan"),
    ):
        assert loan_local_cover(other, (embedded, catalog)).status == "missing"


def test_invalid_local_resolution_isolated_from_second_verified_local_cover():
    current = loan()
    corrupt = local_row(current, placement="broken")
    corrupt["metadata"]["cover_resolution"] = {"status": "selected", "tier": {}, "url": []}
    good = local_row(
        current, url="https://public.example/valid.jpg",
        tier="origin", placement="verified",
    )
    assert loan_local_cover(current, (corrupt, good)).url == good["thumbnail_url"]
