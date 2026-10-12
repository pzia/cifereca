"""P0 #1063 — read-only *normal* Paris-primary ↔ ADEPT configuration.

Normal configuration is an identity-owned Paris connection, its primary
borrower account and a previously bound ADEPT profile. Do not confuse this
local projection with permission to activate an ACSM, successful live login,
the historical return origin or a per-borrower exceptional grant (#976).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def normal_paris_adept_status(
    *,
    identity_id: str,
    connections: Iterable[Mapping[str, Any]],
    accounts: Iterable[Any],
    profile_id: str | None,
) -> dict[str, Any]:
    """Return a deterministic local-only UI status, never an Adobe decision."""
    identity = str(identity_id or "").strip()
    response: dict[str, Any] = {
        "identity_id": identity,
        "status": "to_configure",
        "reason": "missing_paris_connection",
        "paris_label": None,
        "adept_profile_id": None,
    }
    if not identity:
        response["reason"] = "unknown_identity"
        return response
    rows = [
        row for row in connections
        if str(row.get("identity_id") or "") == identity
        and str(row.get("provider_id") or "").casefold() == "paris"
        and row.get("enabled") is True
        and str(row.get("credential_id") or "").strip()
    ]
    if not rows:
        return response
    if len(rows) != 1:
        response.update(status="to_verify", reason="multiple_paris_connections")
        return response

    connection = rows[0]
    response["paris_label"] = str(connection.get("label") or "Paris")
    profile = str(profile_id or "").strip()
    if not profile:
        response["reason"] = "missing_adept_profile"
        return response
    response["adept_profile_id"] = profile

    exact_primary = [
        account for account in accounts
        if str(getattr(account, "identity_id", "") or "") == identity
        and str(getattr(account, "provider_id", "") or "").casefold() == "paris"
        and str(getattr(account, "credential_id", "") or "") == str(connection["credential_id"])
        and getattr(account, "is_primary", None) is True
    ]
    if len(exact_primary) != 1:
        response.update(
            status="to_verify",
            reason=("missing_primary_account" if not exact_primary
                    else "multiple_primary_accounts"),
        )
        return response

    response.update(status="ready", reason="local_configuration_complete")
    return response
