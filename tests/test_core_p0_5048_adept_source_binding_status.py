"""#5048: local ADEPT account binding has no supplier loan/rights authority."""
from types import SimpleNamespace as NS

from cifereca.core.adept_source_binding_status import normal_source_adept_status
from cifereca.core.paris_adept_normal import normal_paris_adept_status


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
        assert rejected["status"] == "to_configure"
        assert rejected["reason"] == f"missing_{provider}_connection"
        assert rejected["adept_profile_id"] is None


def test_paris_compatibility_preserves_closed_legacy_presentation():
    params = _source("paris")
    neutral = normal_source_adept_status(**params)
    legacy = normal_paris_adept_status(
        identity_id=params["identity_id"],
        connections=params["connections"], accounts=params["accounts"],
        profile_id=params["profile_id"],
        primary_connection_id=params["primary_connection_id"],
    )
    assert legacy == dict(
        (("paris_label" if key == "source_label" else key), value)
        for key, value in neutral.items()
    )
    params["primary_connection_id"] = "unrelated"
    assert normal_paris_adept_status(
        identity_id=params["identity_id"], connections=params["connections"],
        accounts=params["accounts"], profile_id=params["profile_id"],
        primary_connection_id=params["primary_connection_id"],
    )["reason"] == "primary_paris_connection_missing"
