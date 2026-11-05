"""P1 #1063: explicit, non-authorizing Paris-primary/ADEPT runtime observation.

This is a pure projection, not a profile resolver/activation or permission
check. Only a *separate user action* may enumerate runtime profiles. The
saved local status must already be unambiguous; absence of runtime evidence
never changes configuration or selects another identity's Adobe profile.
"""
from __future__ import annotations

from typing import Any, Mapping


def describe_paris_adept_runtime(
    saved_status: Mapping[str, Any],
    profile_row: Mapping[str, Any] | None,
    *,
    runtime_available: bool = True,
) -> dict[str, object]:
    """Summarize one already-resolved, exact saved identity/profile pair."""
    identity = saved_status.get("identity_id")
    response: dict[str, object] = {
        "identity_id": identity if type(identity) is str else "",
        "status": "to_verify",
        "reason": "local_binding_unverified",
        "acsm_ready": None,
    }
    if (saved_status.get("status") != "ready"
            or type(saved_status.get("adept_profile_id")) is not str
            or not saved_status["adept_profile_id"]):
        response["reason"] = "local_binding_unverified"
        return response
    if runtime_available is not True:
        response["reason"] = "adept_runtime_unavailable"
        return response
    if type(profile_row) is not dict:
        response["reason"] = "adept_profile_not_found"
        return response

    # Kobo-Adept v0.13.3+ publishes provisioning.acsm_fetch_ready;
    # explicit False takes precedence over older fetch_capable=True.
    provisioning = profile_row.get("provisioning")
    if type(provisioning) is dict and "acsm_fetch_ready" in provisioning:
        value = provisioning["acsm_fetch_ready"]
    else:
        value = profile_row.get("fetch_capable")
    if type(value) is not bool:
        response["reason"] = "adept_acsm_readiness_unknown"
        return response
    if not value:
        response["reason"] = "adept_acsm_not_ready"
        response["acsm_ready"] = False
        return response
    response.update(
        status="observed", reason="adept_acsm_capability_reported",
        acsm_ready=True,
    )
    return response


__all__ = ["describe_paris_adept_runtime"]
