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
