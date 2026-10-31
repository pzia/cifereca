"""#3723: v2 loan observation/delivery adapter, no network or Core transaction."""
from __future__ import annotations
import pytest
from cifereca.plugin_api import Account, ProviderRightObservation, ProviderRightsScope, ProviderRightsSnapshot
from cifereca_paris import ParisProviderSession

BRANCH = "dilicom"
ACCOUNT = "paris"


def build(monkeypatch, *, delivery="available", state="unknown"):
    session = object.__new__(ParisProviderSession)
    account = Account(identity_id="profile-a", provider_id=ACCOUNT,
                      credential_id="credential-a", unique_id="reader-a")
    scope = ProviderRightsScope(account.identity_id, account.provider_id,
                               account.credential_id, account.unique_id)
    offer = "fixture.offer:" + "a" * 64 if delivery == "available" else ""
    row = ProviderRightObservation(
        edition_id="edition-123", right_id="loan-456", kind="loan",
        state=state, evidence_kind="provider_response",
        evidence_ref="fixture.active_loans",
        observed_at="2026-10-30T09:00:00+00:00",
        delivery=delivery, delivery_offer_id=offer,
    )
    snapshot = ProviderRightsSnapshot(
        scope=scope, rights=(row,), status="partial", coverage="partial",
        pagination_complete=False, branch=BRANCH,
        observed_at="2026-10-30T09:00:00+00:00",
        snapshot_ref="fixture.active_loans",
    )
    monkeypatch.setattr(ParisProviderSession, "rights_observation_snapshot",
                        lambda self, selected: snapshot)
    def selected(self, selected_account, *, edition_id, right_id, kind,
                 branch, explicit_user_action=False):
        assert explicit_user_action is True and selected_account is account
        assert (edition_id, right_id, kind, branch) == (
            "edition-123", "loan-456", "loan", BRANCH)
        return snapshot
    monkeypatch.setattr(ParisProviderSession, "rights_selected_delivery_offer_snapshot", selected)
    return session, account, row


def test_v2_loan_exact_scope_positive_unknown_right(monkeypatch):
    from cifereca.plugin_api import ResourceSnapshot
    session, account, row = build(monkeypatch)
    inventory = session.downloadable_resource_snapshot(account)
    assert type(inventory) is ResourceSnapshot
    assert not inventory.authorizes_acquisition
    assert not inventory.authorizes_negative_reconciliation
    assert inventory.resources[0].source.resource_id == row.right_id
    selected = session.downloadable_selected_offer_snapshot(
        account, edition_id="edition-123", resource_id="loan-456",
        kind="loan", branch=BRANCH, explicit_user_action=True,
    )
    assert selected.resources[0].can_propose_delivery
    assert selected.resources[0].right.state == "unknown"


@pytest.mark.parametrize("delivery", ["unavailable", "unknown"])
def test_v2_loan_negative_capacity_never_grants_get(monkeypatch, delivery):
    session, account, row = build(monkeypatch, delivery=delivery, state="active")
    selected = session.downloadable_selected_offer_snapshot(
        account, edition_id="edition-123", resource_id="loan-456",
        kind="loan", branch=BRANCH, explicit_user_action=True,
    )
    assert selected.resources[0].right.state == "active"
    assert not selected.resources[0].can_propose_delivery
    assert selected.resources[0].capability.offer_id == ""


def test_v2_candidate_never_publishes_core_placement(monkeypatch, tmp_path):
    from cifereca.plugin_api import DeliveryCandidate
    session, account, row = build(monkeypatch)
    def staged(self, selected, *, edition_id, right_id, kind, branch,
               delivery_offer_id, target_dir, explicit_user_action=False):
        assert selected is account and explicit_user_action is True
        assert (edition_id, right_id, kind, branch) == (
            "edition-123", "loan-456", "loan", BRANCH)
        assert delivery_offer_id == row.delivery_offer_id
        path = target_dir / "fictional.acsm"
        path.write_text("SYNTHETIC CANDIDATE ONLY", encoding="utf-8")
        return path, "acsm"
    monkeypatch.setattr(ParisProviderSession, "rights_selected_delivery_candidate", staged)
    result = session.downloadable_selected_delivery_candidate(
        account, edition_id="edition-123", resource_id="loan-456",
        kind="loan", branch=BRANCH, selected_offer_id=row.delivery_offer_id,
        target_dir=tmp_path, explicit_user_action=True,
    )
    assert type(result) is DeliveryCandidate
    assert result.source.scope.identity_id == "profile-a"
    assert result.source.scope.credential_id == "credential-a"
    assert result.source.resource_id == "loan-456"
    assert result.path.parent == tmp_path
    assert not result.authorizes_publication


def test_v2_rebind_during_candidate_does_not_return_or_keep_staging(monkeypatch, tmp_path):
    from cifereca.plugin_api import ProviderCapabilityError
    session, account, row = build(monkeypatch)
    def staged(self, selected, *, target_dir, **kwargs):
        path = target_dir / "candidate.acsm"
        path.write_bytes(b"fictional")
        selected.identity_id = "profile-b"
        return path, "acsm"
    monkeypatch.setattr(type(session), "rights_selected_delivery_candidate", staged)
    with pytest.raises(ProviderCapabilityError, match="selected_delivery_scope_changed"):
        session.downloadable_selected_delivery_candidate(
            account, edition_id="edition-123", resource_id="loan-456",
            kind="loan", branch=BRANCH, selected_offer_id=row.delivery_offer_id,
            target_dir=tmp_path, explicit_user_action=True,
        )
    assert not list(tmp_path.iterdir())
