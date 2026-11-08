"""P1 #4978: exact BnF cover path across four views, no provider I/O.

Pure stage observations use synthetic profile/account/resource IDs. A missing
stage is unknown, never evidence of a missing thumbnail or borrowing rights.
"""
from dataclasses import replace

import pytest

from cifereca_bnf_pret_cover_matrix import (
    STAGES, CoverObservation, ExactCoverScope,
    public_cover_matrix_summary, reconcile_exact_cover_matrix,
)


def key(*, identity="A", credential="card-A", account="reader-A",
        loan="loan-1", resource="resource-1"):
    return ExactCoverScope(identity, "bnf-pret", credential, account, loan, resource)


def obs(scope, stage, state):
    return CoverObservation(scope, stage, state)


def states(row):
    return dict(row.states)


def test_empty_matrix_and_unknown_stage_do_not_claim_missing_or_positive():
    assert reconcile_exact_cover_matrix((), ()) == ()
    a = key()
    (row,) = reconcile_exact_cover_matrix((a,), ())
    assert states(row) == {stage: "unobserved" for stage in STAGES}
    assert row.transitions == ()
    report = public_cover_matrix_summary((row,))
    assert report["incomplete_scopes"] == 1
    assert report["rights_verified"] is False
    assert report["supplier_reachability_verified"] is False
    assert "reader-A" not in repr(row)
    assert "card-A" not in str(report)


def test_exact_source_to_loan_and_borrow_with_library_local_fallback_gap():
    a = key()
    rows = (
        obs(a, "source_card", "observed"),
        obs(a, "provider_loan", "missing"),
        obs(a, "library", "observed"),  # Local/EPUB evidence, not loan proof.
        obs(a, "borrow_view", "missing"),
        obs(a, "search", "unrecognized") if False else obs(a, "search", "missing"),
    )
    (row,) = reconcile_exact_cover_matrix((a,), rows)
    assert row.transitions == (
        "source_to_loan_missing", "library_to_borrow_missing",
    )
    assert states(row)["detail"] == "unobserved"
    assert states(row)["gtk_image"] == "unobserved"
    assert public_cover_matrix_summary((row,))["transition_counts"][
        "library_to_borrow_missing"
    ] == 1


def test_catalogue_search_detail_and_gtk_transitions_are_distinct():
    a = key()
    rows = reconcile_exact_cover_matrix((a,), (
        obs(a, "catalogue", "observed"),
        obs(a, "search", "missing"),
        obs(a, "provider_loan", "observed"),
        obs(a, "borrow_view", "observed"),
        obs(a, "gtk_image", "failed"),
    ))
    assert rows[0].transitions == (
        "catalogue_to_search_missing", "gtk_render_failed",
    )
    assert public_cover_matrix_summary(rows)["transition_counts"][
        "gtk_render_failed"
    ] == 1

    rows = reconcile_exact_cover_matrix((a,), (
        obs(a, "search", "observed"),
        obs(a, "detail", "missing"),
        obs(a, "borrow_view", "observed"),
        obs(a, "gtk_image", "missing"),
    ))
    assert rows[0].transitions == (
        "search_to_detail_missing", "gtk_image_not_observed",
    )


def test_two_loans_same_title_different_accounts_not_joined():
    # Titles/ISBN are not even representable in the API.
    one = key(identity="A", credential="card-A", account="reader-A")
    two = key(identity="B", credential="card-B", account="reader-B")
    rows = reconcile_exact_cover_matrix((one, two), (
        obs(one, "source_card", "observed"),
        obs(two, "source_card", "missing"),
        obs(one, "provider_loan", "missing"),
        obs(two, "provider_loan", "observed"),
    ))
    assert rows[0].transitions == ("source_to_loan_missing",)
    assert rows[1].transitions == ()
    assert states(rows[0])["provider_loan"] == "missing"
    assert states(rows[1])["source_card"] == "missing"


def test_ambiguous_duplicate_facts_are_order_independent():
    a = key()
    conflicting = (
        obs(a, "source_card", "observed"),
        obs(a, "source_card", "rejected"),
        obs(a, "source_card", "observed"),
    )
    for order in (conflicting, tuple(reversed(conflicting))):
        (row,) = reconcile_exact_cover_matrix((a,), order)
        assert states(row)["source_card"] == "ambiguous"
        assert row.transitions == ()


def test_repeated_identical_fact_is_idempotent_and_n_stage_chain_scales():
    scopes = [key(loan=f"loan-{n}", resource=f"resource-{n}") for n in range(6)]
    observations = [
        obs(scope, stage, "observed")
        for scope in scopes for stage in STAGES
    ]
    assert reconcile_exact_cover_matrix(tuple(scopes), observations) == (
        reconcile_exact_cover_matrix(tuple(scopes), tuple(reversed(observations)))
    )
    report = public_cover_matrix_summary(
        reconcile_exact_cover_matrix(tuple(scopes), observations)
    )
    assert report["observed_scopes"] == 6
    assert report["incomplete_scopes"] == 0
    assert all(count == 0 for count in report["transition_counts"].values())


def test_foreign_account_and_resource_are_refused_not_silently_mapped():
    a = key()
    foreign = (
        key(identity="B"),
        key(credential="other-card"),
        key(account="other-reader"),
        key(loan="loan-2"),
        key(resource="edition-two"),
    )
    for outsider in foreign:
        with pytest.raises(ValueError, match="foreign_cover_observation"):
            reconcile_exact_cover_matrix((a,), (obs(outsider, "library", "observed"),))


def test_invalid_stage_state_and_unbound_account_fail_closed():
    a = key()
    for stage, state in (("borrow", "observed"), ("library", "allow_loan"),
                         ("source_card", "unobserved")):
        with pytest.raises(ValueError, match="invalid_cover_observation"):
            obs(a, stage, state)
    with pytest.raises(ValueError, match="invalid_exact_cover_scope"):
        replace(a, account_id="")
    with pytest.raises(ValueError, match="duplicate_cover_matrix_scope"):
        reconcile_exact_cover_matrix((a, a), ())
    with pytest.raises(ValueError, match="invalid_cover_matrix_batch"):
        reconcile_exact_cover_matrix((a,) * 65, ())
