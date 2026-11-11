"""#5048: ADEPT runtime projection is source-agnostic and does not grant loans."""
from cifereca.core.adept_runtime_observation import describe_adept_runtime


def test_generic_adept_projection_matches_existing_paris_compatibility():
    saved = {
        "identity_id": "fictitious-identity", "status": "ready",
        "adept_profile_id": "synthetic-adept-profile",
    }
    observed = {"provisioning": {"acsm_fetch_ready": False},
                "fetch_capable": True}
    before = dict(saved)
    assert describe_adept_runtime(saved, observed) == {
        "identity_id": "fictitious-identity", "status": "to_verify",
        "reason": "adept_acsm_not_ready", "acsm_ready": False,
    }
    assert saved == before


def test_generic_adept_projection_rejects_unsupported_runtime():
    saved = {
        "identity_id": "another-identity", "status": "ready",
        "adept_profile_id": "synthetic-profile",
    }
    assert describe_adept_runtime(saved, {"fetch_capable": True},
                                  runtime_available=False) == {
        "identity_id": "another-identity", "status": "to_verify",
        "reason": "adept_runtime_unavailable", "acsm_ready": None,
    }
