"""P1 #4979 — borrower-scoped loan-cover projection is robust to bad remote rows.

Synthetic snapshots only: no real loan/account, provider I/O, GTK, or file
materialization. A remote entitlement is NEVER evidence of an embedded cover.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.core.loan_cover_fallback import loan_local_cover
from tests.test_core_p1_4979_exact_loan_cover import loan, local_row, remote_row


@pytest.mark.parametrize("invalid", [
    None, "", (), ["selected"], "selected",
    {"status": "selected", "tier": ["origin"], "url": "https://public.example/cover.jpg"},
    {"status": "selected", "tier": "embedded", "url": "https://public.example/cover.jpg"},
    {"status": "selected", "tier": "origin", "url": 123},
])
@pytest.mark.parametrize("bad_first", [False, True])
def test_bad_remote_metadata_isolated_from_independent_exact_local_cover(
    invalid, bad_first,
):
    current = loan()
    remote, _entitlement = remote_row(current)
    remote["metadata"]["cover_resolution"] = invalid
    local = local_row(current)
    snapshot = (remote, local) if bad_first else (local, remote)

    result = loan_local_cover(current, snapshot)
    assert result.status == "selected"
    assert result.tier == "embedded"
    assert result.source == "verified-local-placement"
    assert result.url == local["_exact_loan_cover_proofs"][0]["url"]
    assert len(result.candidates) == 1
    # Invalid remote metadata must never make an otherwise genuine loan fail.
    assert loan_local_cover(current, (remote,)).status == "missing"


@pytest.mark.parametrize("tier,url", [
    ("embedded", "file:///home/other-user/cover.jpg"),
    ("embedded", "https://cdn.example.test/unplaced.jpg"),
])
def test_remote_entitlement_cannot_forge_embedded_priority(tier, url):
    current = loan()
    remote, _ = remote_row(current)
    altered = deepcopy(remote)
    altered["thumbnail_url"] = url
    altered["metadata"]["cover_resolution"].update(
        status="selected", tier=tier, url=url,
    )
    altered["_exact_loan_cover_proofs"][0].update(tier=tier, url=url)
    assert loan_local_cover(current, (altered,)).status == "missing"

    # Forged metadata must not beat a different verified remote origin image.
    legitimate, _ = remote_row(current, url="https://catalog.example.test/legit.jpg",
                                entitlement_id="entitlement-other")
    result = loan_local_cover(current, (altered, legitimate))
    assert result.status == "selected"
    assert result.tier == "origin"
    assert result.url == "https://catalog.example.test/legit.jpg"
    assert result.source == "verified-remote-entitlement"


def test_remote_origin_and_catalogue_cover_keep_their_existing_priority_and_scope():
    current = loan()
    origin, _ = remote_row(
        current, url="https://catalog.example.test/origin.jpg",
        entitlement_id="ent-origin", tier="origin",
    )
    catalogue, _ = remote_row(
        current, url="https://catalog.example.test/catalog.jpg",
        entitlement_id="ent-catalog", tier="catalog",
    )
    result = loan_local_cover(current, (catalogue, origin))
    assert result.status == "selected"
    assert result.tier == "origin"
    assert result.url == origin["thumbnail_url"]
    assert result.source == "verified-remote-entitlement"
    assert result.evidence_id == "ent-origin"

    for alien in (
        loan(identity="bob"), loan(credential="card-b"),
        loan(account="borrower-b"), loan(record="edition-b"),
        loan(identifier="loan-b"),
    ):
        assert loan_local_cover(alien, (catalogue, origin)).status == "missing"
    assert loan_local_cover(current, (origin, origin)).status == "selected"


def test_corrupt_row_does_not_affect_two_conflicting_genuine_remote_covers():
    current = loan()
    corrupt, _ = remote_row(current, entitlement_id="bad")
    corrupt["metadata"]["cover_resolution"] = ["broken"]
    a, _ = remote_row(
        current, url="https://catalog.example.test/a.jpg", entitlement_id="ent-a",
    )
    b, _ = remote_row(
        current, url="https://catalog.example.test/b.jpg", entitlement_id="ent-b",
    )
    result = loan_local_cover(current, (a, corrupt, b))
    assert result.status == "ambiguous"
    assert result.url == ""
    assert len(result.candidates) == 2
