"""P1 #5136 — a later Paris check must never resubmit a consumed borrow.

Core/SQLite and real Paris session with entirely synthetic supplier transport.
The observation is supplied through the provider's read-only typed ABI.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightObservation, ProviderRightsScope, ProviderRightsSnapshot,
)
from tests.test_paris_manual_borrow_dispatch import attempt, setup


def _snapshot(account, item, *, present=True, coverage="complete"):
    return ProviderRightsSnapshot(
        scope=ProviderRightsScope(
            account.identity_id, "paris", account.credential_id, account.unique_id,
        ),
        rights=((ProviderRightObservation(
            edition_id=item.record_id, right_id="loan-exact", kind="loan",
            state="unknown", evidence_kind="provider_response",
            evidence_ref="fixture.dilicom", observed_at="2026-11-08T18:00:00+00:00",
        ),) if present else ()),
        status="success" if coverage == "complete" else "partial",
        coverage=coverage, pagination_complete=coverage == "complete",
        branch="dilicom", observed_at="2026-11-08T18:00:00+00:00",
        snapshot_ref="fixture.dilicom",
    )


def _pending(tmp_path):
    service, session, account, item, state = setup(
        tmp_path, result="timeout-unconfirmed",
    )
    assert attempt(service, account, item)["status"] == "verification_unavailable"
    checkpoint = service.storage.loan_borrow_state(borrow_claim_key(account, item))
    assert checkpoint["status"] == "verification_unavailable"
    return service, session, account, item, state, checkpoint["operation_id"]


def _verify(service, account, item, operation_id):
    return service.verify_borrow_without_submission(
        item, expected_identity_id="default", expected_account=account,
        expected_operation_id=operation_id,
        expected_provider_id="paris", expected_docbase="DILICOM",
    )


@pytest.mark.parametrize(("present", "coverage", "expected"), [
    (True, "complete", "observed_current_loan"),
    (False, "complete", "unavailable"),
    (True, "partial", "unavailable"),
])
def test_later_verification_never_replays_post_or_modifies_checkpoint(
    tmp_path, present, coverage, expected,
):
    service, session, account, item, state, operation_id = _pending(tmp_path)
    seen = []
    session.rights_observation_snapshot = (
        lambda row: seen.append(row) or _snapshot(
            account, item, present=present, coverage=coverage,
        )
    )
    session.borrow_explicit_with_claim = lambda *_a, **_kw: pytest.fail("second POST")
    before = service.storage.metadata_get(borrow_claim_key(account, item))
    try:
        result = _verify(service, account, item, operation_id)
        assert result["status"] == expected
        assert result["retry_borrow"] is False and result["mutated"] is False
        assert seen == [account]
        assert service.storage.metadata_get(borrow_claim_key(account, item)) == before
        assert state["mutations"] == 1
        assert service.storage.entitlements(provider_id="paris") == []
    finally:
        service.close()


def test_later_verification_rejects_wrong_operation_account_and_edition(tmp_path):
    service, session, account, item, state, operation_id = _pending(tmp_path)
    seen = []
    session.rights_observation_snapshot = lambda *_a: seen.append(True)
    try:
        assert _verify(service, account, item, "another-operation")["status"] == "blocked"
        assert _verify(service, replace(account, identity_id="alex"),
                       item, operation_id)["status"] == "blocked"
        assert _verify(service, account, replace(item, record_id="foreign"),
                       operation_id)["status"] == "blocked"
        assert seen == [] and state["mutations"] == 1
    finally:
        service.close()


def test_later_verification_rechecks_scope_after_observation(tmp_path):
    service, session, account, item, state, operation_id = _pending(tmp_path)
    def rebind(_account):
        service.storage.provider_connection_put(dict(
            identity_id="alex", provider_id="paris", credential_id="card-A",
            label="Synthetic", enabled=True, metadata={},
        ))
        return _snapshot(account, item)
    session.rights_observation_snapshot = rebind
    try:
        result = _verify(service, account, item, operation_id)
        assert result["status"] == "blocked"
        assert result["reason_code"] == "borrow_verification_source_changed"
        assert state["mutations"] == 1
    finally:
        service.close()


def test_later_verification_transport_unavailable_never_claims_absence(tmp_path):
    service, session, account, item, state, operation_id = _pending(tmp_path)
    def timeout(_account):
        raise TimeoutError("synthetic DILICOM")
    session.rights_observation_snapshot = timeout
    try:
        result = _verify(service, account, item, operation_id)
        assert result["status"] == "unavailable"
        assert result["reason_code"] == "borrow_verification_observation_unavailable"
        assert state["mutations"] == 1
    finally:
        service.close()
