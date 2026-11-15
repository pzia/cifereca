"""P1 #6168: admission of exact Core library and borrower cover projections."""
from __future__ import annotations

from copy import deepcopy
import pytest

from cifereca.core.book_cover_source_projection import (
    project_exact_loan_cover, project_verified_local_library_cover,
)
from tests.test_core_p1_4979_exact_loan_cover import (
    loan, local_row, remote_row,
)

PUBLIC = "https://public.example.org/source.png"


def library(current=None, *, url=None, tier="embedded"):
    current = current or loan()
    row = local_row(current, tier=tier, **({"url": url} if url is not None else {}))
    row["key"] = "local:verified:placement-a"
    row["metadata"]["cover_resolution"]["source"] = "local-core"
    return row


def test_local_library_list_and_detail_get_identical_immutable_decisions():
    row = library()
    first = project_verified_local_library_cover(
        row, revision=10, snapshot_complete=True,
    )
    detail = project_verified_local_library_cover(
        deepcopy(row), revision=10, snapshot_complete=True,
    )
    assert first == detail
    assert first.status == "selected"
    assert first.tier == "embedded"
    assert first.uri == row["thumbnail_url"]
    assert first.scope.kind == "placement"
    assert first.scope.placement_id == row["placement_id"]
    assert first.scope.artifact_id == row["artifact_id"]


@pytest.mark.parametrize("fault", [
    ("local", False), ("identity_id", "bob"), ("artifact_id", None),
    ("placement_id", ""), ("key", ""),
])
def test_local_row_must_prove_exact_owner_and_placement(fault):
    row = library()
    row[fault[0]] = fault[1]
    assert project_verified_local_library_cover(
        row, revision=1, snapshot_complete=True,
    ) is None


def test_local_library_source_refuses_stale_or_forged_resolution():
    row = library()
    row["metadata"]["cover_resolution"]["url"] = PUBLIC
    assert project_verified_local_library_cover(
        row, revision=1, snapshot_complete=True,
    ) is None
    row = library()
    row["metadata"]["cover_resolution"]["status"] = "ambiguous"
    assert project_verified_local_library_cover(
        row, revision=1, snapshot_complete=True,
    ) is None


def test_local_missing_only_from_explicit_completed_core_resolution():
    row = library()
    row["thumbnail_url"] = None
    row["metadata"]["cover_resolution"] = {"status": "missing"}
    missing = project_verified_local_library_cover(
        row, revision=3, snapshot_complete=True,
    )
    assert missing.status == "missing" and missing.uri == ""
    del row["metadata"]["cover_resolution"]
    assert project_verified_local_library_cover(
        row, revision=3, snapshot_complete=True,
    ) is None


def test_local_ambiguous_remains_ambiguous_not_missing():
    row = library()
    row["thumbnail_url"] = None
    row["metadata"]["cover_resolution"] = {"status": "ambiguous"}
    ambiguous = project_verified_local_library_cover(
        row, revision=3, snapshot_complete=True,
    )
    assert ambiguous.status == "ambiguous"
    assert ambiguous.uri == ""


def test_partial_snapshot_does_not_claim_selected_or_missing():
    row = library()
    decision = project_verified_local_library_cover(
        row, revision=3, snapshot_complete=False,
    )
    assert decision.status == "pending"
    assert decision.uri == ""


def test_exact_loan_uses_verified_local_cover_without_changing_loan():
    current = loan()
    before = deepcopy(current)
    decision = project_exact_loan_cover(
        current, (local_row(current),), revision=2, snapshot_complete=True,
    )
    assert decision.status == "selected"
    assert decision.tier == "embedded"
    assert decision.scope.kind == "loan"
    assert decision.scope.identity_id == "alice"
    assert decision.scope.credential_id == "card-a"
    assert decision.scope.account_id == "borrower-a"
    assert decision.scope.loan_id == "loan-1"
    assert decision.uri.startswith("file://")
    assert current == before


def test_loan_library_evidence_does_not_cross_borrower_or_credential():
    current = loan()
    exact_row = local_row(current)
    for changed in (
        loan(identity="bob"), loan(credential="card-b"),
        loan(account="borrower-b"), loan(identifier="loan-2"),
        loan(record="edition-2"),
    ):
        decision = project_exact_loan_cover(
            changed, (exact_row,), revision=2, snapshot_complete=True,
        )
        assert decision.status == "missing"
        assert decision.uri == ""


def test_explicit_empty_primary_borrower_account_is_supported():
    primary = loan(account="")
    decision = project_exact_loan_cover(
        primary, (local_row(primary),), revision=1, snapshot_complete=True,
    )
    assert decision.status == "selected"
    assert decision.scope.account_id == ""


def test_unknown_borrower_is_not_invented_from_library_image():
    unknown = loan(account=None)
    assert project_exact_loan_cover(
        unknown, (local_row(loan()),), revision=1, snapshot_complete=True,
    ) is None


def test_remote_exact_entitlement_can_supply_a_loan_cover_without_placement():
    current = loan()
    verified, _ = remote_row(current, url=PUBLIC)
    decision = project_exact_loan_cover(
        current, (verified,), revision=4, snapshot_complete=True,
    )
    assert decision.status == "selected"
    assert decision.uri == PUBLIC
    assert decision.tier == "origin"


def test_loan_provider_image_is_lower_priority_than_verified_placement():
    current = loan()
    current.thumbnail_url = PUBLIC
    selected = project_exact_loan_cover(
        current, (local_row(current),), revision=4, snapshot_complete=True,
    )
    assert selected.tier == "embedded"
    assert selected.uri.startswith("file://")
    # Provider's own safe card still works without any local join.
    provider = project_exact_loan_cover(
        current, (), revision=4, snapshot_complete=True,
    )
    assert provider.status == "selected"
    assert provider.uri == PUBLIC and provider.tier == "catalog"


def test_ambiguous_verified_local_origins_do_not_fall_back_to_first():
    current = loan()
    rows = (
        local_row(current, "https://first.example/cover.png", tier="origin",
                  artifact="artifact-1", placement="placement-1"),
        local_row(current, "https://second.example/cover.png", tier="origin",
                  artifact="artifact-2", placement="placement-2"),
    )
    decision = project_exact_loan_cover(
        current, rows, revision=7, snapshot_complete=True,
    )
    assert decision.status == "ambiguous"
    assert decision.uri == ""


def test_partial_loan_inventory_is_pending_even_with_supplier_image():
    current = loan()
    current.thumbnail_url = PUBLIC
    pending = project_exact_loan_cover(
        current, (), revision=1, snapshot_complete=False,
    )
    assert pending.status == "pending" and pending.uri == ""


def test_signed_provider_url_never_crosses_public_core_resolution():
    current = loan()
    current.thumbnail_url = "https://public.example.org/img?token=PRIVATE"
    decision = project_exact_loan_cover(
        current, (), revision=3, snapshot_complete=True,
    )
    assert decision.status == "missing" and decision.uri == ""


def test_missing_library_row_not_a_local_cover_proof_by_title_only():
    current = loan()
    unrelated = local_row(loan(identifier="unrelated"), tier="embedded")
    unrelated["title"] = current.title
    unrelated["isbn"] = "9780306406157"
    decision = project_exact_loan_cover(
        current, (unrelated,), revision=1, snapshot_complete=True,
    )
    assert decision.status == "missing"


def test_invalid_and_unbounded_library_snapshots_fail_closed():
    current = loan()
    with pytest.raises(TypeError):
        project_exact_loan_cover(
            current, iter((local_row(current),)), revision=1,
            snapshot_complete=True,
        )
    with pytest.raises(TypeError):
        project_exact_loan_cover(
            current, (local_row(current),) * 10001, revision=1,
            snapshot_complete=True,
        )


def test_revision_flows_unchanged_to_same_scope():
    current = loan()
    first = project_exact_loan_cover(
        current, (local_row(current),), revision=3, snapshot_complete=True,
    )
    second = project_exact_loan_cover(
        current, (local_row(current),), revision=4, snapshot_complete=True,
    )
    assert first.scope == second.scope and first.revision == 3 and second.revision == 4
