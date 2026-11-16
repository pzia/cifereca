"""P0 #5975: ADEPT local readiness and explicit probes for any source.

The Paris-compatible Settings facade remains a wrapper, but the same Core
binding decision runs for future lenders without a branded provider check.
"""
from __future__ import annotations

from cifereca.models import Account
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def _add_source(service, provider: str, credential: str, *, enabled=True):
    service.storage.provider_connection_put({
        "provider_id": provider, "credential_id": credential,
        "identity_id": "alice", "enabled": enabled, "label": provider,
    })
    service.accounts.append(Account(
        provider_id=provider, credential_id=credential,
        identity_id="alice", unique_id="", is_primary=True,
    ))


def test_two_independent_sources_share_core_adept_readiness_without_cross_provider(
    tmp_path,
):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        for provider in ("biblioaccess.la-rochelle", "synthetic-lender"):
            _add_source(service, provider, "alice-paris")
            status = service.normal_source_adept_status(
                "alice", source_provider_id=provider,
            )
            assert status["status"] == "ready"
            assert status["reason"] == "local_configuration_complete"
            assert status["adept_profile_id"] == "alice-profile"
            assert "source_label" in status and "paris_label" not in status
        assert service.normal_paris_adept_status("alice")["status"] == "ready"
        # The same credential under a distinct supplier is not evidence
        # that the selected supplier's own connection remains active.
        service.storage.provider_connection_put({
            "provider_id": "synthetic-lender", "credential_id": "alice-paris",
            "identity_id": "alice", "enabled": False,
        })
        denied = service.normal_source_adept_status(
            "alice", source_provider_id="synthetic-lender",
        )
        assert denied["status"] == "to_configure"
        assert denied["reason"] == "missing_synthetic-lender_connection"
        assert service.normal_source_adept_status(
            "alice", source_provider_id="biblioaccess.la-rochelle",
        )["status"] == "ready"
    finally:
        service.close()


def test_generic_primary_selection_requires_both_enabled_config_and_sqlite_binding(
    tmp_path,
):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        provider = "future-library"
        _add_source(service, provider, "card-a")
        _add_source(service, provider, "card-b")
        chosen = dict(
            source_provider_id=provider, primary_connection_id="card-b",
        )
        assert service.normal_source_adept_status(
            "alice", source_provider_id=provider,
        )["reason"] == "multiple_future-library_connections"
        # SQLite alone cannot authorize a UI-selected connection.
        assert service.normal_source_adept_status(
            "alice", **chosen,
        )["reason"] == "primary_future-library_connection_missing"
        service.config.set("providers.accounts", [{
            "id": "card-b", "provider": provider,
            "identity_id": "alice", "enabled": True,
        }])
        assert service.normal_source_adept_status(
            "alice", **chosen,
        )["status"] == "ready"
        service.config.set("providers.accounts", [{
            "id": "card-b", "provider": provider,
            "identity_id": "alice", "enabled": False,
        }])
        assert service.normal_source_adept_status(
            "alice", **chosen,
        )["reason"] == "primary_future-library_connection_missing"
        assert service.normal_source_adept_status(
            "alice", source_provider_id=provider,
            primary_connection_id="card-a",
        )["status"] != "ready"
    finally:
        service.close()


def test_source_runtime_is_explicit_and_rechecks_binding_after_one_inventory(
    tmp_path,
):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        provider = "biblioaccess.la-rochelle"
        _add_source(service, provider, "reader-1")
        calls = []

        def profiles():
            calls.append("inspect")
            return [{
                "profile_id": "alice-profile",
                "provisioning": {"acsm_fetch_ready": True},
            }]

        service.kobo_profiles = profiles
        assert service.normal_source_adept_status(
            "alice", source_provider_id=provider,
        )["status"] == "ready"
        assert calls == []
        receipt = service.check_source_adept_runtime(
            "alice", source_provider_id=provider,
        )
        assert receipt["status"] == "observed"
        assert receipt["acsm_ready"] is True and calls == ["inspect"]

        def revoke_during_inspection():
            calls.append("revoked")
            service.storage.adept_profile_binding_set(
                "identity", "alice", "new-profile",
            )
            return [{
                "profile_id": "alice-profile",
                "provisioning": {"acsm_fetch_ready": True},
            }]

        service.kobo_profiles = revoke_during_inspection
        assert service.check_source_adept_runtime(
            "alice", source_provider_id=provider,
        ) == {
            "identity_id": "alice", "status": "to_verify",
            "reason": "local_binding_changed", "acsm_ready": None,
        }
        assert calls == ["inspect", "revoked"]
    finally:
        service.close()


def test_invalid_or_unknown_source_is_never_a_runtime_admission(tmp_path):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        service.kobo_profiles = lambda: (_ for _ in ()).throw(
            AssertionError("invalid source called ADEPT runtime")
        )
        for source in ("", "PARIS", "other provider", "all", None, True):
            status = service.normal_source_adept_status(
                "alice", source_provider_id=source,
            )
            assert status["reason"] == "invalid_source_descriptor"
            assert service.check_source_adept_runtime(
                "alice", source_provider_id=source,
            )["reason"] == "local_binding_unverified"
        assert service.normal_paris_adept_status("bob")["reason"] == (
            "missing_paris_connection"
        )
    finally:
        service.close()
