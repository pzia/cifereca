"""Provider-neutral static test for a complete canonical Core delivery ABI.

A method exists only if statically declared on the session; probing a live
provider or descriptor must never perform I/O during acquisition admission.
This is a dispatch fence, not a provider authorization or proof of a right.
"""
from __future__ import annotations

from inspect import getattr_static


# A guarded-only producer is a complete Core v2 delivery ABI. Its selected
# candidate is exclusively executable under Core's revocable admission lease
# (and optionally its ADEPT runtime callback). Never route it to the 0.7
# backup_items writer merely because it does not expose an unguarded GET.
_CANONICAL_DELIVERY_CONTRACTS = (
    *(
        (
            "downloadable_resource_snapshot",
            "downloadable_selected_offer_snapshot",
            candidate,
        )
        for candidate in (
            "downloadable_selected_delivery_candidate_runtime_guarded",
            "downloadable_selected_delivery_candidate_guarded",
            "downloadable_selected_delivery_candidate",
        )
    ),
    (
        "rights_observation_snapshot",
        "rights_selected_delivery_offer_snapshot",
        "rights_selected_delivery_candidate",
    ),
)


def has_complete_delivery_abi(session: object) -> bool:
    """True when any entire canonical delivery trio is statically callable."""
    try:
        return any(
            all(callable(getattr_static(session, method, None))
                for method in methods)
            for methods in _CANONICAL_DELIVERY_CONTRACTS
        )
    except (TypeError, AttributeError):
        return False


__all__ = ["has_complete_delivery_abi"]
