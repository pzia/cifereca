"""#3171 A/B/C convergence: Core-only, pure plan contract (synthetic fixtures)."""
from dataclasses import replace

import pytest

from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)
from cifereca.core.source_convergence import (
    SourceKey, PlacementEvidence, OriginEvidence, ProfileScan,
    LocalCandidate, plan_convergence,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
OBSERVED = "2026-10-28T10:00:00+00:00"


def source(provider="provider-alpha", identity="A", credential="cred-A",
           account="account-A", edition="book-1", right="purchase-1",
           kind="purchase", *, state="active", delivery="available",
           evidence_kind="provider_response", status="success"):
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
        branch="books",
    )
    return snapshot, SourceKey(scope, edition, right, kind)


def placement(identity, placement_id, sha=SHA_A, verified=True):
    return PlacementEvidence(identity, placement_id, sha, 1234, verified)


def origin(key, sha=SHA_A, kind="provider", attested=True):
    return OriginEvidence(
        kind, sha, key, attested, identity_id=key.scope.identity_id,
        origin_ref=f"{kind}:{key.right_id}:{sha}",
        proof_ref="synthetic-core-proof" if attested else "",
        parent_sha256=SHA_B if kind == "derived" else None,
    )


def scan(identity, complete=True):
    return ProfileScan(identity, complete, "core-scan-proof" if complete else "")


def plan(*snapshots, placements=(), origins=(), scans=(), candidates=(), consent=()):
    return plan_convergence(
        tuple(snapshots), placements=tuple(placements), origins=tuple(origins),
        scans=tuple(scans), candidates=tuple(candidates),
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
    assert no_b_origin.decisions[1].state == "needs_reconciliation"


def test_same_edition_different_sha_does_not_link_by_metadata():
    snap, key = source()
    result = plan(snap, placements=(placement("A", "unrelated", SHA_B),),
                  origins=(origin(key, SHA_A),), scans=(scan("A"),),
                  candidates=(LocalCandidate(key, "unrelated"),),
                  consent=(key,))
    assert result.decisions[0].state == "needs_reconciliation"
    assert result.decisions[0].placement_ids == ()


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
                consent=(key,)).decisions[0].reason == "local_scan_incomplete"


def test_stale_file_does_not_count_as_presence_or_initiate_download():
    snap, key = source()
    result = plan(snap, placements=(placement("A", "stale", verified=False),),
                  origins=(origin(key),), scans=(scan("A"),),
                  consent=(key,))
    assert result.decisions[0].state == "needs_reconciliation"


@pytest.mark.parametrize("state,evidence,expected", [
    ("inactive", "provider_response", "inactive"),
    ("unknown", "provider_response", "pending"),
    ("active", "historical_receipt", "pending"),
    ("active", "legacy_unverified", "pending"),
])
def test_unconfirmed_or_inactive_right_cannot_request_download(state, evidence, expected):
    snap, key = source(state=state, evidence_kind=evidence,
                     delivery="available" if evidence != "legacy_unverified" and state == "active" else "unknown")
    assert plan(snap, scans=(scan("A"),),
                consent=(key,)).decisions[0].state == expected


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
