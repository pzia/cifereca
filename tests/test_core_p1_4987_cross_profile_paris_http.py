"""P1 #4987: real Paris adapter, fully synthetic HTTP, cross-profile one shot."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_core_p1_4662_live_lender_preview import _candidate
from tests.test_paris_manual_borrow_dispatch import setup


def _cross_profile_fixture(tmp_path, *, result="no-acsm"):
    service, session, account, notice, http_state = setup(
        tmp_path, result=result,
    )
    account.identity_id = "alex"
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": "paris",
        "credential_id": account.credential_id, "label": "Paris",
        "enabled": True, "metadata": {},
    })
    identity = service.config.identity("alex")
    identity["primary_paris_connection_id"] = account.credential_id
    service.config.upsert_identity(identity)
    service.config.upsert_provider_account({
        "id": account.credential_id, "provider": "paris",
        "identity_id": "alex", "enabled": True,
    })
    service._plugin_manager.enabled_for_identity = lambda *_a: True

    original = _candidate(credential=account.credential_id)
    row = replace(
        original,
        notice=notice,
        edition_evidence=replace(
            original.edition_evidence, record_id=notice.record_id,
            source_collection=notice.source_collection,
        ),
    )
    assert service.current_identity.identity_id == "default"
    preview = service.preview_borrow_for_current_identity(
        expected_edition_id="edition-1",
        observations=[row], now=1100,
    )
    assert preview.status == "external", preview
    return service, account, notice, row, preview, http_state


def _execute(service, row, preview, *, confirmed=True):
    return service.execute_cross_profile_borrow(
        captured_preview=preview,
        selected_option_id=preview.options[0].option_id,
        expected_edition_id="edition-1", observations=[row],
        now=1100, confirmed=confirmed,
    )


def test_real_paris_cross_profile_borrow_creates_one_loan_without_profile_switch(
    tmp_path,
):
    service, account, notice, row, preview, http = _cross_profile_fixture(tmp_path)
    requester = service.current_identity
    service.materialize_to_identity = lambda *_a, **_kw: pytest.fail(
        "lender borrow is not transfer permission"
    )
    try:
        result = _execute(service, row, preview)
        assert result["status"] == "confirmed", result
        assert result["provider_verified"] is True
        assert result["delivery"] == "unavailable"
        assert result["transfer"] == "blocked"
        assert service.current_identity is requester
        assert http["mutations"] == 1
        key = borrow_claim_key(account, notice)
        assert service.storage.loan_borrow_state(key)["status"] == "confirmed"
        assert _execute(service, row, preview)["status"] == "blocked"
        assert http["mutations"] == 1
    finally:
        service.close()


@pytest.mark.parametrize("result,expected", [
    ("timeout-unconfirmed", "verification_unavailable"),
    ("preflight-failure", "blocked"),
])
def test_real_paris_cross_profile_unsent_and_unknown_never_replay(
    tmp_path, result, expected,
):
    service, account, notice, row, preview, http = _cross_profile_fixture(
        tmp_path, result=result,
    )
    try:
        outcome = _execute(service, row, preview)
        assert outcome["status"] == expected, outcome
        assert outcome["retry_borrow"] is False
        assert service.current_identity.identity_id == "default"
        if result == "timeout-unconfirmed":
            assert http["mutations"] == 1
            assert service.storage.loan_borrow_state(
                borrow_claim_key(account, notice),
            )["status"] == "verification_unavailable"
            assert _execute(service, row, preview)["status"] == "blocked"
            assert http["mutations"] == 1
        else:
            assert http["mutations"] == 0
            assert service.storage.metadata_get(
                borrow_claim_key(account, notice),
            ) is None
    finally:
        service.close()
