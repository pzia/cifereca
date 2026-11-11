"""Paris-compatible ADEPT configuration presentation, backed by a neutral Core check."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .adept_source_binding_status import normal_source_adept_status


def normal_paris_adept_status(
    *,
    identity_id: str,
    connections: Iterable[Mapping[str, Any]],
    accounts: Iterable[Any],
    profile_id: str | None,
    primary_connection_id: str | None = None,
) -> dict[str, Any]:
    result = normal_source_adept_status(
        identity_id=identity_id, source_provider_id="paris",
        source_label="Paris", connections=connections, accounts=accounts,
        profile_id=profile_id, primary_connection_id=primary_connection_id,
    )
    result["paris_label"] = result.pop("source_label")
    return result


__all__ = ["normal_paris_adept_status"]
