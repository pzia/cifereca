"""P1 #1063: explicit Paris/ADEPT runtime check without provider operations."""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.core.paris_adept_runtime_check import describe_paris_adept_runtime
from cifereca.models import Account
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def _local():
    return {
        "identity_id": "alice", "status": "ready",
        "reason": "local_configuration_complete",
        "adept_profile_id": "alice-profile",
    }


@pytest.mark.parametrize(("row", "reason", "ready", "state"), [
    ({"profile_id": "alice-profile", "name": "Reader",
      "provisioning": {"acsm_fetch_ready": True}},
     "adept_acsm_capability_reported", True, "observed"),
    ({"profile_id": "alice-profile", "name": "Reader",
      "provisioning": {"acsm_fetch_ready": False}, "fetch_capable": True},
     "adept_acsm_not_ready", False, "to_verify"),
    ({"profile_id": "alice-profile", "name": "Reader", "fetch_capable": False},
     "adept_acsm_not_ready", False, "to_verify"),
    ({"profile_id": "alice-profile", "name": "Reader"},
     "adept_acsm_readiness_unknown", None, "to_verify"),
    (None, "adept_profile_not_found", None, "to_verify"),
])
def test_pure_readiness_projection_never_claims_adobe_authorization(
    row, reason, ready, state,
):
    local = _local()
    saved = deepcopy(local)
    result = describe_paris_adept_runtime(local, row)
    assert result == {
        "identity_id": "alice", "status": state,
        "reason": reason, "acsm_ready": ready,
    }
    assert local == saved


def test_nonready_binding_never_promoted_by_unrelated_positive_runtime():
    result = describe_paris_adept_runtime(
        dict(_local(), status="to_verify"),
        {"profile_id": "alice-profile", "fetch_capable": True},
    )
    assert result["reason"] == "local_binding_unverified"
    assert result["acsm_ready"] is None


def test_service_profile_observation_is_explicit_and_scoped(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        calls = []
        def rows():
            calls.append(True)
            return [{"profile_id": "alice-profile", "name": "Reader",
                     "provisioning": {"acsm_fetch_ready": True}},
                    {"profile_id": "bob-profile", "name": "Other",
                     "provisioning": {"acsm_fetch_ready": False}}]
        service.kobo_profiles = rows
        original = service.normal_paris_adept_status("alice")
        assert not calls  # rendering/status lookup never calls the runtime
        checked = service.check_normal_paris_adept_runtime("alice")
        assert checked["status"] == "observed"
        assert checked["acsm_ready"] is True
        assert len(calls) == 1
        # A different identity without Paris must never enumerate ADEPT.
        assert service.check_normal_paris_adept_runtime("bob")["reason"] == (
            "local_binding_unverified"
        )
        assert len(calls) == 1
        assert service.normal_paris_adept_status("alice") == original
        assert service.storage.adept_profile_binding_get("identity", "alice") == (
            "alice-profile"
        )
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) is None
    finally:
        service.close()


def test_missing_duplicate_wrong_profile_and_runtime_errors_are_fail_closed(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        service.kobo_profiles = lambda: [
            {"profile_id": "another", "provisioning": {"acsm_fetch_ready": True}},
        ]
        assert service.check_normal_paris_adept_runtime("alice")["reason"] == (
            "adept_profile_not_found"
        )
        service.kobo_profiles = lambda: [
            {"name": "same", "profile_id": "one", "fetch_capable": True},
            {"name": "same", "profile_id": "two", "fetch_capable": True},
        ]
        service.storage.adept_profile_binding_set("identity", "alice", "same")
        assert service.check_normal_paris_adept_runtime("alice")["reason"] == (
            "adept_profile_not_found"
        )
        def broken():
            raise RuntimeError("runtime failed with synthetic credentials")
        service.kobo_profiles = broken
        assert service.check_normal_paris_adept_runtime("alice")["reason"] == (
            "adept_runtime_unavailable"
        )
    finally:
        service.close()


def test_rebinding_during_profile_enumeration_disallows_old_positive(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        def rebind():
            service.storage.adept_profile_binding_set(
                "identity", "alice", "replacement",
            )
            return [{"profile_id": "alice-profile", "fetch_capable": True}]
        service.kobo_profiles = rebind
        assert service.check_normal_paris_adept_runtime("alice") == {
            "identity_id": "alice", "status": "to_verify",
            "reason": "local_binding_changed", "acsm_ready": None,
        }
    finally:
        service.close()
