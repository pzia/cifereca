"""#3171 A/B/C convergence: Core-only, pure plan contract (synthetic fixtures)."""
from dataclasses import replace

import pytest

from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)
from cifereca.core.source_convergence import (
    SourceKey, PlacementEvidence, OriginEvidence, ProfileScan,
    LocalCandidate, DeclaredAssociationEvidence, plan_convergence,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
OBSERVED = "2026-10-28T10:00:00+00:00"


def source(provider="provider-alpha", identity="A", credential="cred-A",
           account="account-A", edition="book-1", right="purchase-1",
           kind="purchase", *, state="active", delivery="available",
           evidence_kind="provider_response", status="success", branch="books"):
    scope = ProviderRightsScope(identity, provider, credential, account)
    row = ProviderRightObservation(
        edition, right, kind, state, evidence_kind,
        evidence_ref="receipt-1" if evidence_kind != "legacy_unverified" else "",
        observed_at=OBSERVED if evidence_kind != "legacy_unverified" else None,
        delivery=delivery,
        delivery_offer_id="offer-1" if delivery == "available" else "",
    )
    snapshot = ProviderRightsSnapshot(
        scope=scope, rights=(row,), status=status, coverage="partial",
        branch=branch,
    )
    return snapshot, SourceKey(scope, edition, right, kind, branch)


def placement(identity, placement_id, sha=SHA_A, verified=True):
    return PlacementEvidence(
        identity, placement_id, sha, 1234, verified,
        verification_ref="synthetic-core-file-check" if verified else "",
    )


def origin(key, sha=SHA_A, kind="provider", attested=True):
    return OriginEvidence(
        kind, sha, key, attested, identity_id=key.scope.identity_id,
        origin_ref=f"{kind}:{key.right_id}:{sha}",
        proof_ref="synthetic-core-proof" if attested else "",
        parent_sha256=SHA_B if kind == "derived" else None,
    )


def scan(identity, complete=True):
    return ProfileScan(identity, complete, "core-scan-proof" if complete else "")


def plan(*snapshots, placements=(), origins=(), scans=(), candidates=(), declarations=(), consent=()):
    return plan_convergence(
        tuple(snapshots), placements=tuple(placements), origins=tuple(origins),
        scans=tuple(scans), candidates=tuple(candidates), declarations=tuple(declarations),
        consent_references=frozenset(consent),
    )


def test_two_providers_share_sha_but_require_distinct_exact_origins():
    a, ak = source()
    b, bk = source("provider-beta", "B", "cred-B", "account-B",
                   right="purchase-B")
    files = (placement("A", "file-A"), placement("B", "file-B"))
    result = plan(a, b, placements=files, origins=(origin(ak), origin(bk)),
                  scans=(scan("A"), scan("B")))
    assert [(r.state, r.placement_ids) for r in result.decisions] == [
        ("present_attested", ("file-A",)), ("present_attested", ("file-B",)),
    ]
    assert result.read_only and not result.acquisition_authorized
    assert all(not r.acquisition_authorized for r in result.decisions)
    # Removing B's provider origin must not borrow A's, even with identical SHA.
    no_b_origin = plan(a, b, placements=files, origins=(origin(ak),),
                       scans=(scan("A"), scan("B")),
                       candidates=(LocalCandidate(bk, "file-B"),),
                       consent=(bk,))
    assert no_b_origin.decisions[1].state == "missing_to_download"
    assert not no_b_origin.decisions[1].acquisition_authorized


def test_same_edition_different_sha_does_not_link_by_metadata():
    snap, key = source()
    result = plan(snap, placements=(placement("A", "unrelated", SHA_B),),
                  origins=(origin(key, SHA_A),), scans=(scan("A"),),
                  candidates=(LocalCandidate(key, "unrelated"),),
                  consent=(key,))
    assert result.decisions[0].state == "missing_to_download"
    assert result.decisions[0].reason == "unattested_candidate_no_satisfaction"
    assert result.decisions[0].placement_ids == ()
    assert not result.acquisition_authorized


def test_scope_is_exact_credential_account_identity_edition_and_right():
    snap, key = source()
    changes = (
        replace(key, scope=replace(key.scope, identity_id="B")),
        replace(key, scope=replace(key.scope, credential_id="cred-other")),
        replace(key, scope=replace(key.scope, account_id="account-other")),
        replace(key, edition_id="other-edition"),
        replace(key, right_id="other-purchase"),
        replace(key, kind="loan"),
    )
    for other in changes:
        result = plan(snap, placements=(placement("A", "file-A"),),
                      origins=(origin(other),), scans=(scan("A"),),
                      consent=(key,))
        assert result.decisions[0].state == "missing_to_download"
        assert result.decisions[0].placement_ids == ()


def test_partial_provider_inventory_keeps_positive_but_never_invents_absent_rights():
    snap, key = source(status="partial")
    result = plan(snap, placements=(placement("A", "file-A"),),
                  origins=(origin(key),), scans=(scan("A"),))
    assert len(result.decisions) == 1
    assert result.decisions[0].state == "present_attested"
    assert plan(
        ProviderRightsSnapshot(scope=snap.scope, rights=(), status="partial",
                               branch="books"), scans=(scan("A"),),
    ).decisions == ()


@pytest.mark.parametrize("kind", ["transfer", "cifereca", "manual", "adept", "derived"])
def test_non_provider_origin_never_confers_right_or_restoration(kind):
    snap, key = source()
    result = plan(snap, placements=(placement("A", "file-A"),),
                  origins=(origin(key, kind=kind),), scans=(scan("A"),),
                  candidates=(LocalCandidate(key, "file-A"),))
    assert result.decisions[0].state == "needs_reconciliation"
    assert not result.acquisition_authorized


def test_transfer_A_to_B_to_C_not_automatically_recreated():
    a, ak = source()
    b, bk = source("provider-beta", "B", "cred-B", "account-B",
                   right="purchase-B")
    # A bought it; B bought it separately; B→C was only a one-time transfer.
    origins = (
        origin(ak), origin(bk),
        origin(ak, kind="adept"),
        origin(bk, kind="adept"),
        OriginEvidence("transfer", SHA_A, identity_id="B", origin_ref="A-to-B"),
        OriginEvidence("cifereca", SHA_A, identity_id="B", origin_ref="B-internal"),
        OriginEvidence("transfer", SHA_A, identity_id="C", origin_ref="B-to-C"),
        OriginEvidence("cifereca", SHA_A, identity_id="C", origin_ref="C-internal"),
    )
    assert len(origins) == 8
    result = plan(a, b, placements=(placement("A", "A-file"),),
                  origins=origins, scans=(scan("A"), scan("B")),
                  consent=(bk,))
    assert [d.state for d in result.decisions] == [
        "present_attested", "missing_to_download",
    ]
    # There is no provider SourceReference for C, hence no decision to restore C.
    assert all(decision.source.scope.identity_id != "C" for decision in result.decisions)


def test_missing_file_after_complete_scan_proposes_only_if_deliverable_and_consented():
    snap, key = source()
    assert plan(snap, origins=(origin(key),), scans=(scan("A"),),
                consent=(key,)).decisions[0].state == "missing_to_download"
    assert plan(snap, origins=(origin(key),), scans=(scan("A"),)).decisions[0].state == "pending"
    no_delivery, no_delivery_key = source(delivery="unknown")
    assert plan(no_delivery, scans=(scan("A"),),
                consent=(no_delivery_key,)).decisions[0].state == "pending"
    assert plan(snap, scans=(scan("A", False),),
                consent=(key,)).decisions[0].reason == "scan_incomplete_independent_admission_required"


def test_stale_file_does_not_count_as_presence_or_initiate_download():
    snap, key = source()
    result = plan(snap, placements=(placement("A", "stale", verified=False),),
                  origins=(origin(key),), scans=(scan("A"),),
                  consent=(key,))
    assert result.decisions[0].state == "needs_reconciliation"


@pytest.mark.parametrize("state,evidence,expected", [
    ("inactive", "provider_response", "inactive"),
    ("inactive", "historical_receipt", "pending"),
    ("inactive", "legacy_unverified", "pending"),
    ("unknown", "provider_response", "pending"),
    ("active", "historical_receipt", "pending"),
    ("active", "legacy_unverified", "pending"),
])
def test_unconfirmed_or_inactive_right_cannot_request_download(state, evidence, expected):
    snap, key = source(state=state, evidence_kind=evidence,
                     delivery="available" if evidence != "legacy_unverified" and state == "active" else "unknown")
    result = plan(snap, scans=(scan("A"),), consent=(key,))
    assert result.decisions[0].state == expected
    assert not result.acquisition_authorized
    assert not result.decisions[0].authorizes_negative_reconciliation


def test_loan_is_distinct_from_purchase_even_for_same_edition_and_bytes():
    purchase, pk = source()
    loan, lk = source(right="loan-1", kind="loan")
    result = plan(purchase, loan, placements=(placement("A", "one-copy"),),
                  origins=(origin(pk),), scans=(scan("A"),),
                  consent=(pk,))
    assert result.decisions[0].state == "present_attested"
    assert result.decisions[1].state == "pending"


def test_duplicate_snapshots_or_scan_scope_rejected_instead_of_last_write_wins():
    snap, _ = source()
    with pytest.raises(ValueError, match="duplicate_source_reference"):
        plan(snap, snap)
    with pytest.raises(ValueError, match="ambiguous_profile_scan"):
        plan(snap, scans=(scan("A"), scan("A")))


def test_zero_mutation_deterministic_second_plan():
    snap, key = source()
    inputs = dict(placements=(placement("A", "file"),), origins=(origin(key),),
                  scans=(scan("A"),))
    first = plan(snap, **inputs)
    second = plan(snap, **inputs)
    assert first == second
    assert first.decisions[0].state == "present_attested"
    assert first.decisions[0].provider_satisfied

def test_origin_link_requires_exact_context_and_proof_without_granting_right():
    snap, key = source()
    with pytest.raises(ValueError, match="invalid_origin_evidence"):
        OriginEvidence("provider", SHA_A, key, True,
                       identity_id="B", origin_ref="foreign", proof_ref="receipt")
    with pytest.raises(ValueError, match="invalid_origin_evidence"):
        OriginEvidence("provider", SHA_A, key, True,
                       identity_id="A", origin_ref="unproven")
    with pytest.raises(ValueError, match="invalid_origin_evidence"):
        OriginEvidence("derived", SHA_A, identity_id="A", origin_ref="convert",
                       parent_sha256=SHA_A)
    declared = OriginEvidence(
        "manual", SHA_A, key, False, identity_id="A",
        origin_ref="user-recorded", proof_ref="",
    )
    result = plan(snap, placements=(placement("A", "book"),),
                  origins=(declared,), scans=(scan("A"),),
                  candidates=(LocalCandidate(key, "book"),))
    assert result.decisions[0].state == "needs_reconciliation"


def test_duplicate_attested_origin_is_idempotent_not_a_second_placement():
    snap, key = source()
    link = origin(key)
    result = plan(snap, placements=(placement("A", "copy"),),
                  origins=(link, link), scans=(scan("A"),))
    assert result.decisions[0].placement_ids == ("copy",)
    assert result.decisions[0].state == "present_attested"


def test_conflicting_placement_identifier_is_rejected_across_profiles():
    snap, key = source()
    with pytest.raises(ValueError, match="conflicting_placement_evidence"):
        plan(snap, placements=(
            placement("A", "same-ID", SHA_A),
            placement("B", "same-ID", SHA_B),
        ), origins=(origin(key),), scans=(scan("A"),))


def test_conflicting_origin_identifier_is_rejected_without_overwriting():
    snap, key = source()
    first = origin(key)
    second = replace(first, artifact_sha256=SHA_B)
    with pytest.raises(ValueError, match="conflicting_origin_evidence"):
        plan(snap, origins=(first, second), scans=(scan("A"),))


def test_verified_placement_without_actual_core_check_is_not_admissible():
    with pytest.raises(ValueError, match="invalid_placement_evidence"):
        PlacementEvidence("A", "file", SHA_A, 1234, True)


@pytest.mark.parametrize("provider", ["provider-alpha", "provider-beta"])
def test_fresh_user_declaration_is_present_but_not_attested_or_acquired(provider):
    snap, key = source(provider=provider)
    record = DeclaredAssociationEvidence(
        key, "relation-1", "file-1", "declared_current",
        "fresh-Core-file-verification", identity_id="A",
    )
    expected = plan(snap, scans=(scan("A"),), declarations=(record,),
                    consent=(key,))
    assert expected.decisions[0].state == "present_declared"
    assert not expected.decisions[0].provider_satisfied
    assert expected.decisions[0].reason == "declared_current_independent_admission_required"
    assert expected.decisions[0].independent_acquisition_review_possible
    assert expected.decisions[0].placement_ids == ("file-1",)
    assert not expected.acquisition_authorized
    assert not expected.decisions[0].acquisition_authorized
    # The same declared file without existing, exact-right auto-acquisition
    # consent still remains a visible declaration, never a GET permission.
    no_consent = plan(snap, scans=(scan("A"),), declarations=(record,))
    assert no_consent.decisions[0].state == "present_declared"
    assert no_consent.decisions[0].reason == "fresh_user_declaration_not_provider_proof"
    assert not no_consent.decisions[0].independent_acquisition_review_possible


def test_stale_or_conflicting_declared_links_require_review_not_download():
    snap, key = source()
    current = DeclaredAssociationEvidence(
        key, "rel-current", "local-1", "declared_current", "fresh-check",
        identity_id="A",
    )
    stale = DeclaredAssociationEvidence(key, "rel-stale", "local-2", "needs_review", identity_id="A")
    result = plan(snap, declarations=(current, stale),
                  scans=(scan("A"),), consent=(key,))
    assert result.decisions[0].state == "needs_reconciliation"
    different = replace(current, relation_id="rel-other", placement_id="local-2")
    ambiguous = plan(snap, declarations=(current, different), scans=(scan("A"),))
    assert ambiguous.decisions[0].state == "needs_reconciliation"


def test_revoked_declaration_is_historical_and_does_not_block_new_candidate():
    snap, key = source()
    revoked = DeclaredAssociationEvidence(key, "revoked", "old-file", "revoked", identity_id="A")
    decision = plan(snap, declarations=(revoked,), scans=(scan("A"),),
                    consent=(key,)).decisions[0]
    assert decision.state == "missing_to_download"
    with pytest.raises(ValueError, match="conflicting_declared_association"):
        plan(snap, declarations=(revoked, replace(revoked, status="needs_review")))


def test_declaration_is_scoped_to_exact_right_including_loan():
    purchase, pk = source()
    loan, lk = source(right="temporary-loan", kind="loan")
    declaration = DeclaredAssociationEvidence(
        pk, "rel-purchase", "shared-file", "declared_current", "fresh-file",
        identity_id="A",
    )
    result = plan(purchase, loan, scans=(scan("A"),),
                  declarations=(declaration,), consent=(lk,))
    assert result.decisions[0].state == "present_declared"
    assert result.decisions[1].state == "missing_to_download"


@pytest.mark.parametrize("status,proof", [
    ("declared_current", ""), ("needs_review", "forged"),
    ("revoked", "forged"),
])
def test_inconsistent_declaration_verification_is_rejected(status, proof):
    snap, key = source()
    with pytest.raises(ValueError, match="invalid_declared_association_evidence"):
        DeclaredAssociationEvidence(key, "relation", "file", status, proof,
                                    identity_id="A")


def test_declared_file_in_foreign_profile_cannot_satisfy_scoped_source():
    snap, key = source()
    with pytest.raises(ValueError, match="invalid_declared_association_evidence"):
        DeclaredAssociationEvidence(
            key, "foreign-relation", "file-in-B", "declared_current",
            "check-in-B", identity_id="B",
        )


def test_same_account_same_right_ids_but_distinct_branches_are_independent():
    first, fk = source(branch="syracuse", right="same-id", edition="same-edition")
    second, sk = source(branch="dilicom", right="same-id", edition="same-edition")
    assert fk != sk
    assert fk.scope == sk.scope
    # A verified OriginLink from one library branch must not satisfy another.
    result = plan(first, second,
                  origins=(origin(fk),),
                  placements=(placement("A", "exact-file"),),
                  scans=(scan("A"),), consent=(sk,))
    assert [d.state for d in result.decisions] == [
        "present_attested", "missing_to_download",
    ]
    # Even a distinct fingerprint registered in the other branch remains
    # independent; no cross-branch inference from equal title or SHA.
    third = plan(first, second,
                 origins=(origin(sk, SHA_B),), scans=(scan("A"),),
                 placements=(placement("A", "same-title", SHA_A),),
                 candidates=(LocalCandidate(sk, "same-title"),))
    assert third.decisions[0].state == "pending"
    assert third.decisions[1].state == "needs_reconciliation"


def test_same_scope_branch_identical_positive_right_is_duplicate_fail_closed():
    first, _ = source(branch="same")
    second, _ = source(branch="same")
    with pytest.raises(ValueError, match="duplicate_source_reference"):
        plan(first, second)


def test_incomplete_scan_is_not_negative_proof_nor_a_permanent_download_veto():
    snap, key = source()
    review = plan(snap, scans=(scan("A", False),), consent=(key,)).decisions[0]
    assert review.state == "pending"
    assert review.independent_acquisition_review_possible
    assert not review.acquisition_authorized
    assert review.placement_ids == ()
    # Without a deliverable offer/consent, even independent review cannot
    # be suggested. An unverified local candidate instead needs reconciliation.
    not_consented = plan(snap, scans=(scan("A", False),)).decisions[0]
    assert not not_consented.independent_acquisition_review_possible
    candidate = plan(snap, scans=(scan("A", False),),
                     candidates=(LocalCandidate(key, "uncertain-file"),),
                     consent=(key,)).decisions[0]
    assert candidate.state == "pending"
    assert candidate.independent_acquisition_review_possible
    assert not candidate.acquisition_authorized


def test_bibliographic_only_candidate_does_not_force_a_manual_reconciliation():
    for provider in ("provider-alpha", "provider-beta"):
        snapshot, key = source(provider=provider)
        other_bytes = placement("A", "similar-title-file", SHA_B)
        hint = LocalCandidate(key, "similar-title-file")
        result = plan(snapshot, placements=(other_bytes,),
                      candidates=(hint,), scans=(scan("A"),),
                      consent=(key,))
        decision = result.decisions[0]
        assert decision.state == "missing_to_download"
        assert decision.reason == "unattested_candidate_no_satisfaction"
        assert decision.placement_ids == ()
        assert not decision.acquisition_authorized
        # The same hint without an exact consent/offered delivery cannot
        # authorize an alternative route by title, even after a full scan.
        no_consent = plan(snapshot, placements=(other_bytes,),
                          candidates=(hint,), scans=(scan("A"),))
        assert no_consent.decisions[0].state == "needs_reconciliation"
        assert not no_consent.decisions[0].acquisition_authorized


def test_stale_origin_does_not_become_an_indefinite_manual_veto():
    snap, key = source()
    stale = plan(snap, placements=(placement("A", "old", verified=False),),
                 origins=(origin(key),), scans=(scan("A"),),
                 consent=(key,)).decisions[0]
    assert stale.state == "needs_reconciliation"
    assert stale.independent_acquisition_review_possible
    assert not stale.acquisition_authorized
    no_consent = plan(snap, placements=(placement("A", "old", verified=False),),
                      origins=(origin(key),), scans=(scan("A"),)).decisions[0]
    assert not no_consent.independent_acquisition_review_possible


@pytest.mark.parametrize("right_kind", ["purchase", "loan"])
def test_declared_copy_cannot_veto_pre_authorized_acquisition(right_kind):
    snap, key = source(kind=right_kind, right="same-edition", delivery="available")
    declaration = DeclaredAssociationEvidence(
        key, "recorded-correlation", "candidate-file",
        "declared_current", "fresh-file-check", identity_id="A",
    )
    action = plan(snap, declarations=(declaration,), scans=(scan("A"),),
                  consent=(key,)).decisions[0]
    assert action.state == "present_declared"
    assert not action.provider_satisfied
    assert action.placement_ids == ("candidate-file",)
    assert action.independent_acquisition_review_possible
    assert not action.acquisition_authorized
    # Even with the same physical candidate, a distinct right or another
    # account never inherits either this declaration or the stored consent.
    other, other_key = source(account="account-B", credential="cred-B",
                              right="same-edition", kind=right_kind,
                              delivery="available")
    foreign = plan(other, declarations=(declaration,), scans=(scan("A"),),
                   consent=(key,)).decisions[0]
    assert foreign.state == "pending"
    assert not foreign.independent_acquisition_review_possible
    assert not foreign.acquisition_authorized


def test_explicit_inactive_provider_claim_remains_read_only_even_with_full_branch():
    scope = ProviderRightsScope("A", "provider-alpha", "cred-A", "buyer-A")
    right = ProviderRightObservation(
        "edition-A", "loan-A", "loan", "inactive",
        "provider_response", "synthetic-provider-observation", OBSERVED,
    )
    snap = ProviderRightsSnapshot(
        scope=scope, rights=(right,), status="success",
        coverage="complete", pagination_complete=True, branch="digital",
        observed_at=OBSERVED, snapshot_ref="completed-page",
    )
    first = plan(snap).decisions[0]
    assert first.state == "inactive"
    assert not first.authorizes_negative_reconciliation
    assert not first.acquisition_authorized

    legacy = replace(right, evidence_kind="legacy_unverified",
                     evidence_ref="", observed_at=None)
    unproven = replace(snap, rights=(legacy,), coverage="partial",
                       pagination_complete=None, snapshot_ref="")
    result = plan(unproven)
    assert result.decisions[0].state == "pending"
    assert result.decisions[0].reason == "provider_right_unattested"
    assert not result.decisions[0].authorizes_negative_reconciliation


def test_historical_inactive_claim_cannot_override_exact_attested_file():
    snap, key = source(state="inactive", evidence_kind="historical_receipt",
                       delivery="unknown")
    result = plan(snap, placements=(placement("A", "file"),),
                  origins=(origin(key),), scans=(scan("A"),))
    assert result.decisions[0].state == "pending"
    assert result.decisions[0].reason == "provider_right_unattested"
    assert not result.decisions[0].authorizes_negative_reconciliation
    assert not result.decisions[0].provider_satisfied
