"""Provider-neutral static test for a complete canonical Core delivery ABI.

A method exists only if statically declared on the session; probing a live
provider or descriptor must never perform I/O during acquisition admission.
This is a dispatch fence, not a provider authorization or proof of a right.
"""
from __future__ import annotations

from inspect import getattr_static


_CANONICAL_DELIVERY_CONTRACTS = (
    (
        "downloadable_resource_snapshot",
        "downloadable_selected_offer_snapshot",
        "downloadable_selected_delivery_candidate",
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
