"""#3252: v2 purchase resource ABI; synth only, no private provider requests."""
from __future__ import annotations

import pytest

from cifereca.plugin_api import (
    Account, ProviderRightObservation, ProviderRightsScope, ProviderRightsSnapshot,
    ResourceSnapshot, DeliveryCandidate,
)
from cifereca_numilog import NumilogSession

BRANCH = "meslivres"


def fake(monkeypatch, *, state="unknown", delivery="available"):
    session = object.__new__(NumilogSession)
    account = Account(
        identity_id="owner-a", provider_id="numilog", credential_id="cred-a",
        unique_id="upstream-a",
    )
    scope = ProviderRightsScope("owner-a", "numilog", "cred-a", "upstream-a")
    row = ProviderRightObservation(
        edition_id="purchase-17", right_id="purchase-17", kind="purchase",
        state=state, evidence_kind="provider_response",
        evidence_ref="synthetic.private.inventory",
        observed_at="2026-10-30T10:00:00+00:00",
        delivery=delivery,
        delivery_offer_id=("fixture.offer:"+"a"*64) if delivery=="available" else "",
        delivery_unavailable_reason="archived" if delivery=="unavailable" else "",
    )
    snapshot = ProviderRightsSnapshot(
        scope=scope, rights=(row,), status="partial", coverage="partial",
        pagination_complete=False, branch=BRANCH,
        observed_at="2026-10-30T10:00:00+00:00",
        snapshot_ref="synthetic.private.inventory",
    )
    monkeypatch.setattr(NumilogSession, "rights_observation_snapshot",
                        lambda self, chosen: snapshot)
    def chosen(self, caller, *, edition_id, right_id, kind, branch,
               explicit_user_action=False):
        assert caller is account
        assert explicit_user_action is True
        assert (edition_id, right_id, kind, branch) == (
            "purchase-17", "purchase-17", "purchase", BRANCH)
        return snapshot
    monkeypatch.setattr(NumilogSession, "rights_selected_delivery_offer_snapshot", chosen)
    return session, account, row


def test_v2_positive_capacity_with_unknown_right_and_exact_scope(monkeypatch):
    session, account, row = fake(monkeypatch)
    observed = session.downloadable_resource_snapshot(account)
    assert type(observed) is ResourceSnapshot
    assert not observed.authorizes_acquisition
    assert not observed.authorizes_negative_reconciliation
    assert observed.resources[0].source.scope.account_id == "upstream-a"
    assert observed.resources[0].source.resource_id == "purchase-17"
    selected = session.downloadable_selected_offer_snapshot(
        account, edition_id="purchase-17", resource_id="purchase-17",
        kind="purchase", branch=BRANCH, explicit_user_action=True,
    )
    assert selected.resources[0].can_propose_delivery
    assert selected.resources[0].right.state == "unknown"


@pytest.mark.parametrize("delivery", ["unknown", "unavailable"])
def test_v2_active_right_cannot_synthesize_download_offer(monkeypatch, delivery):
    session, account, row = fake(monkeypatch, state="active", delivery=delivery)
    result = session.downloadable_selected_offer_snapshot(
        account, edition_id="purchase-17", resource_id="purchase-17",
        kind="purchase", branch=BRANCH, explicit_user_action=True,
    )
    assert result.resources[0].right.state == "active"
    assert not result.resources[0].can_propose_delivery
    assert result.resources[0].capability.offer_id == ""
    if delivery == "unavailable":
        assert result.resources[0].capability.unavailable_reason == "archived"


def test_v2_candidate_exact_source_no_core_receipt(monkeypatch, tmp_path):
    session, account, row = fake(monkeypatch)
    def staged(self, caller, *, edition_id, right_id, kind, branch,
               delivery_offer_id, target_dir, explicit_user_action=False):
        assert caller is account and explicit_user_action is True
        assert (edition_id, right_id, kind, branch) == (
            "purchase-17", "purchase-17", "purchase", BRANCH)
        assert delivery_offer_id == row.delivery_offer_id
        path = target_dir / "candidate.epub"
        path.write_bytes(b"FAKE NON CANONICAL FILE")
        return path, ""
    monkeypatch.setattr(NumilogSession, "rights_selected_delivery_candidate", staged)
    result = session.downloadable_selected_delivery_candidate(
        account, edition_id="purchase-17", resource_id="purchase-17",
        kind="purchase", branch=BRANCH,
        selected_offer_id=row.delivery_offer_id,
        target_dir=tmp_path, explicit_user_action=True,
    )
    assert type(result) is DeliveryCandidate
    assert result.source.scope.identity_id == "owner-a"
    assert result.source.scope.provider_id == "numilog"
    assert result.source.scope.credential_id == "cred-a"
    assert result.source.resource_id == "purchase-17"
    assert result.path.parent == tmp_path
    assert not result.authorizes_publication


@pytest.mark.parametrize(("field", "value"), [
    ("identity_id", "owner-b"),
    ("provider_id", "other-provider"),
    ("credential_id", "cred-b"),
    ("unique_id", "upstream-b"),
])
def test_v2_candidate_rebind_rejected_without_core_provenance(monkeypatch, tmp_path, field, value):
    """No v2 source claim for an Account modified during the selected GET."""
    from cifereca.plugin_api import ProviderCapabilityError

    session, account, row = fake(monkeypatch)

    def staged(self, caller, *, target_dir, **kwargs):
        path = target_dir / "candidate.epub"
        path.write_bytes(b"STAGING ONLY; NOT A CANONICAL EPUB")
        setattr(caller, field, value)
        return path, ""

    monkeypatch.setattr(type(session), "rights_selected_delivery_candidate", staged)
    with pytest.raises(ProviderCapabilityError, match="selected_delivery_scope_changed"):
        session.downloadable_selected_delivery_candidate(
            account, edition_id="purchase-17", resource_id="purchase-17",
            kind="purchase", branch=BRANCH,
            selected_offer_id=row.delivery_offer_id,
            target_dir=tmp_path, explicit_user_action=True,
        )
    # Only this attempt's newly created candidate is removed, not durable files.
    assert not list(tmp_path.iterdir())
