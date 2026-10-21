"""Pure closed-code origin refusals for Adobe-first return (#1120).

No entitlement decisions, identity fallback, Adobe action, SQLite claim,
provider call, filesystem access or mutation occurs here. The caller already
checks exact loan provenance and keeps the single durable return checkpoint.
"""
from __future__ import annotations

from typing import Any, Callable


def missing_acquisition_outcome(
    source: dict[str, Any], *,
    public_origin_status: Callable[..., str],
) -> dict[str, str]:
    """Legacy response when no exact saved acquisition ID was attested."""
    reason = public_origin_status(
        source.get("status"), default="adept_return_binding_required",
    )
    if reason == "ready":
        reason = "adept_return_binding_required"
    return {
        "status": "provider_action_available" if reason == "no_acquisition" else reason,
        "scope": "adobe_first_paris",
        "retry": "provider_action_explicit" if reason == "no_acquisition"
                 else "resolve_identity",
    }


def unready_to_return_outcome(
    source: dict[str, Any], *,
    public_origin_status: Callable[..., str],
) -> dict[str, str] | None:
    """Refuse initial Adobe mutation unless exact origin says ready and allowed."""
    source_status = public_origin_status(
        source.get("status"), default="adept_return_state_required",
    )
    if source_status != "ready" or source.get("return_available") is not True:
        return {
            "status": source_status if source_status != "ready"
                      else "adept_return_state_required",
            "scope": "adobe_first_paris",
            "retry": "verification_only",
        }
    return None
