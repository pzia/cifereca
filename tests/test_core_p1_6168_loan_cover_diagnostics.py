"""N0/N2 #6168: reason about La Rochelle-like loan cover gaps without joining."""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.core.book_cover_diagnostics import diagnose_exact_loan_cover
from tests.test_core_p1_4979_exact_loan_cover import (
    loan, local_row, remote_row,
)

COVER = "https://public.example.org/library.jpg"


def unlinked_library_row(current, *, url=COVER, tier="origin"):
    row = local_row(current, url=url, tier=tier)
    row["provider_id"] = current.source_provider
    row["item_id"] = current.record_id
    row.pop("_exact_loan_cover_proofs")
    return row


def diagnose(current, rows=(), *, complete=True):
    return diagnose_exact_loan_cover(
        current, tuple(rows), revision=3, snapshot_complete=complete,
    )


def test_visible_library_cover_without_loan_proof_is_diagnostic_not_fallback():
    current = loan()
    row = unlinked_library_row(current)
    original = deepcopy(current)
    decision, report = diagnose(current, (row,))
    assert decision.status == "missing"
    assert decision.uri == ""
    assert report.public_fields() == {
        "status": "missing",
        "reason": "visible_library_image_without_loan_proof",
        "library_candidate_observed": True,
        "exact_loan_proof_selected": False,
    }
    assert current == original


def test_exact_local_proof_upgrades_to_selected_embedded_without_new_right():
    current = loan()
    verified = local_row(current)
    decision, report = diagnose(current, (verified,))
    assert decision.status == "selected"
    assert decision.tier == "embedded"
    assert report.reason == "attested_embedded"
    assert report.exact_loan_proof_selected is True
    assert current.thumbnail_url is None


def test_exact_remote_entitlement_proof_can_select_origin_without_placement():
    current = loan()
    remote, _entitlement = remote_row(current)
    decision, report = diagnose(current, (remote,))
    assert decision.status == "selected"
    assert decision.tier == "origin"
    assert report.reason == "attested_origin"
    assert report.exact_loan_proof_selected is True


def test_incomplete_library_is_pending_not_no_evidence():
    current = loan()
    decision, report = diagnose(
        current, (unlinked_library_row(current),), complete=False,
    )
    assert decision.status == "pending"
    assert report.reason == "partial_snapshot"
    assert report.library_candidate_observed is False


def test_without_library_or_supplier_evidence_cover_is_missing():
    decision, report = diagnose(loan())
    assert decision.status == "missing"
    assert report.reason == "no_admissible_evidence"
    assert report.exact_loan_proof_selected is False


@pytest.mark.parametrize("wrong", [
    {"identity": "bob"}, {"record": "another"},
    {"credential": "card-b"}, {"account": "borrower-b"},
])
def test_other_borrower_or_edition_never_attaches_image(wrong):
    current = loan()
    observed = unlinked_library_row(loan(**wrong))
    decision, report = diagnose(current, (observed,))
    assert decision.status == "missing"
    # The observation does not join the borrower's record by title/ISBN.
    assert report.exact_loan_proof_selected is False


def test_foreign_profile_does_not_even_count_private_cover_hint():
    current = loan()
    foreign = unlinked_library_row(loan(identity="bob"))
    foreign["title"] = current.title
    foreign["isbn"] = "9780306406157"
    decision, report = diagnose(current, (foreign,))
    assert decision.status == "missing"
    assert report.reason == "no_admissible_evidence"
    assert report.library_candidate_observed is False


def test_untrusted_library_url_cannot_count_as_admissible_observation():
    current = loan()
    bad = unlinked_library_row(
        current, url="https://public.example.org/cover?token=PRIVATE",
    )
    decision, report = diagnose(current, (bad,))
    assert decision.status == "missing"
    assert report.reason == "no_admissible_evidence"


def test_supplier_only_cover_has_a_distinct_closed_reason():
    current = loan()
    current.thumbnail_url = COVER
    decision, report = diagnose(current)
    assert decision.status == "selected"
    assert decision.uri == COVER
    assert report.reason == "supplier_catalogue_only"
    assert report.exact_loan_proof_selected is False


def test_ambiguous_two_exact_placements_remains_ambiguous_not_first_wins():
    current = loan()
    a = local_row(current, "https://one.example/cover.jpg", tier="origin",
                  artifact="artifact-a", placement="placement-a")
    b = local_row(current, "https://two.example/cover.jpg", tier="origin",
                  artifact="artifact-b", placement="placement-b")
    decision, report = diagnose(current, (a, b))
    assert decision.status == "ambiguous"
    assert decision.uri == ""
    assert report.reason == "conflicting_evidence"


def test_unqualified_borrower_is_not_assigned_a_book_from_library():
    current = loan(account=None)
    decision, report = diagnose(current, (unlinked_library_row(loan()),))
    assert decision is None
    assert report.status == "invalid"
    assert report.reason == "source_unqualified"


def test_report_is_bounded_and_contains_no_identifiers_or_private_urls():
    current = loan(credential="SECRET-CARD", account="PRIVATE-BORROWER")
    observed = unlinked_library_row(
        current, url="https://public.example.org/library.jpg",
    )
    _decision, report = diagnose(current, (observed,))
    public = report.public_fields()
    assert set(public) == {
        "status", "reason", "library_candidate_observed",
        "exact_loan_proof_selected",
    }
    assert all(type(value) is bool or type(value) is str for value in public.values())
    for secret in ("SECRET-CARD", "PRIVATE-BORROWER", COVER, "alice", "edition-1"):
        assert secret not in repr(public)
        assert secret not in repr(report)


def test_existing_core_admission_rejects_lazy_and_oversize_snapshot():
    current = loan()
    with pytest.raises(TypeError):
        diagnose_exact_loan_cover(
            current, iter((unlinked_library_row(current),)),
            revision=1, snapshot_complete=True,
        )
    with pytest.raises(TypeError):
        diagnose_exact_loan_cover(
            current, (unlinked_library_row(current),) * 10001,
            revision=1, snapshot_complete=True,
        )
