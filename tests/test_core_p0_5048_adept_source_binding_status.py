"""#5048: local ADEPT account binding has no supplier loan/rights authority."""
from types import SimpleNamespace as NS

from cifereca.core.adept_source_binding_status import normal_source_adept_status


def _source(provider):
    return dict(
        identity_id="alice", source_provider_id=provider,
        source_label="Synthetic Reader", profile_id="profile-alice",
        connections=[{
            "identity_id": "alice", "provider_id": provider,
            "credential_id": "card-a", "enabled": True, "label": "Reader",
        }],
        accounts=[NS(
            identity_id="alice", provider_id=provider,
            credential_id="card-a", is_primary=True,
        )],
        primary_connection_id="card-a",
    )


def test_generic_binding_two_providers_never_claims_acsm_or_loan():
    for provider in ("fixture-lender-a", "fixture-lender-b"):
        params = _source(provider)
        before = list(params["connections"])
        observed = normal_source_adept_status(**params)
        assert observed == {
            "identity_id": "alice", "status": "ready",
            "reason": "local_configuration_complete",
            "source_label": "Reader", "adept_profile_id": "profile-alice",
        }
        assert params["connections"] == before
        params["connections"][0]["provider_id"] = "another-lender"
        rejected = normal_source_adept_status(**params)
        assert rejected["status"] == "to_verify"
        assert rejected["reason"] == f"primary_{provider}_connection_missing"
        params["primary_connection_id"] = None
        unselected = normal_source_adept_status(**params)
        assert unselected["status"] == "to_configure"
        assert unselected["reason"] == f"missing_{provider}_connection"
        assert rejected["adept_profile_id"] is None


def test_paris_provider_keeps_historical_closed_reason_codes():
    params = _source("paris")
    status = normal_source_adept_status(**params)
    assert status["status"] == "ready"
    assert status["reason"] == "local_configuration_complete"
    assert status["source_label"] == "Reader"
    params["primary_connection_id"] = "unrelated"
    denied = normal_source_adept_status(**params)
    assert denied["status"] == "to_verify"
    assert denied["reason"] == "primary_paris_connection_missing"
