"""Provider-neutral, read-only primary-source ↔ ADEPT configuration.

Normal configuration is an identity-owned source connection, its primary
borrower account and a previously bound ADEPT profile. Do not confuse this
local projection with permission to activate an ACSM, successful live login,
a supplier origin or a per-borrower exceptional grant.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def normal_source_adept_status(
    *,
    identity_id: str,
    source_provider_id: str,
    source_label: str,
    connections: Iterable[Mapping[str, Any]],
    accounts: Iterable[Any],
    profile_id: str | None,
    primary_connection_id: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic local-only UI status, never an Adobe decision."""
    # Configuration IDs are opaque strings. Coercing malformed booleans or
    # numbers may fabricate a matching Paris/ADEPT association.
    identity = identity_id.strip() if type(identity_id) is str else ""
    provider = source_provider_id if type(source_provider_id) is str else ""
    label = source_label if type(source_label) is str else ""
    response: dict[str, Any] = {
        "identity_id": identity,
        "status": "to_configure",
        "reason": f"missing_{provider}_connection",
        "source_label": None,
        "adept_profile_id": None,
    }
    if not provider or not label:
        response["reason"] = "invalid_source_descriptor"
        return response
    if not identity:
        response["reason"] = "unknown_identity"
        return response
    rows = [
        row for row in connections
        if type(row.get("identity_id")) is str
        and row["identity_id"] == identity
        and str(row.get("provider_id") or "").casefold() == provider.casefold()
        and row.get("enabled") is True
        and type(row.get("credential_id")) is str
        and row["credential_id"].strip()
    ]
    if not rows:
        if primary_connection_id is not None:
            response.update(status="to_verify", reason=f"primary_{provider}_connection_missing")
        return response
    # An explicitly designated source never falls back to another Paris
    # login when its credential disappears, is disabled, or changes owner.
    if primary_connection_id is not None:
        if (type(primary_connection_id) is not str
                or not primary_connection_id.strip()
                or primary_connection_id != primary_connection_id.strip()):
            response.update(status="to_verify", reason=f"primary_{provider}_connection_invalid")
            return response
        selected = [row for row in rows
                    if row["credential_id"] == primary_connection_id]
        if len(selected) != 1:
            response.update(status="to_verify", reason=(
                "primary_paris_connection_missing" if not selected
                else f"primary_{provider}_connection_duplicate"))
            return response
        connection = selected[0]
    else:
        if len(rows) != 1:
            response.update(status="to_verify", reason=f"multiple_{provider}_connections")
            return response
        connection = rows[0]
    response["source_label"] = str(connection.get("label") or label)
    profile = profile_id.strip() if type(profile_id) is str else ""
    if not profile:
        response["reason"] = "missing_adept_profile"
        return response
    response["adept_profile_id"] = profile

    exact_primary = [
        account for account in accounts
        if type(getattr(account, "identity_id", None)) is str
        and getattr(account, "identity_id") == identity
        and str(getattr(account, "provider_id", "") or "").casefold() == "paris"
        and type(getattr(account, "credential_id", None)) is str
        and getattr(account, "credential_id") == connection["credential_id"]
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
