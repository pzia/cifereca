"""Exact provider-neutral Library acquisition, never a historical entitlement.

Only a current, unambiguous remote origin may produce a selected offer target.
The provider's live ABI and the Core canonical A→B executor independently
recheck account, consent, source epoch, final SHA and placement on explicit click.
"""
from __future__ import annotations


class OwnedDownloadRejected(ValueError):
    """Fixed public refusal; never include private supplier identifiers."""


def owned_library_download_target(service, entry):
    """Passively select one exact source origin, never a stored pseudo-right."""
    if type(entry) is dict and type(entry.get("possessions")) in (list, tuple):
        identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
        origins = [
            row for row in entry["possessions"]
            if type(row) is dict and row.get("identity_id") == identity
            and row.get("state") == "active" and row.get("kind") == "owned"
        ]
        # No fallback may choose one supplier from multiple explicit origins.
        if len(origins) > 1:
            return None
    from .selected_source_offer import select_library_source_offer_target
    return select_library_source_offer_target(service, entry)


def obtain_owned_library_item(service, target):
    """Only a typed, currently valid selected-source target is executable."""
    from .selected_source_offer import SourceOfferTarget, obtain_source_offer
    if type(target) is not SourceOfferTarget:
        raise OwnedDownloadRejected("owned_download_unavailable")
    try:
        return obtain_source_offer(service, target)
    except OwnedDownloadRejected:
        raise
    except Exception:
        raise OwnedDownloadRejected("owned_download_unavailable") from None
