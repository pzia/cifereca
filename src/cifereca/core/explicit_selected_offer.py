"""Core-only, passive validation of one *current selected provider offer*.

This is not purchase-right reconstruction and never grants a GET. It checks
the exact origin and offer coordinates of the producer's one-shot response.
The caller must separately hold the global lock, recheck account/session,
obtain explicit user consent, and enforce transaction-time publication fences.
"""
from __future__ import annotations

from ..plugin_api.downloadable_resources import ResourceSnapshot
from .source_convergence import SourceKey


def exact_selected_offer_id(snapshot: object, source: SourceKey) -> str | None:
    """Return one exact, currently available offer ID, else fail closed.

    The optional provider right metadata is deliberately ignored. In
    particular, neither a cached `owned` flag nor legal-right history can
    substitute for an offer returned by the selected provider callback.
    """
    if type(source) is not SourceKey or type(snapshot) is not ResourceSnapshot:
        return None
    if (snapshot.scope != source.scope or snapshot.branch != source.branch
            or snapshot.status not in ("success", "partial")
            or len(snapshot.resources) != 1):
        return None
    offer = snapshot.resources[0]
    if (offer.source.scope != source.scope
            or offer.source.branch != source.branch
            or (offer.source.edition_id, offer.source.resource_id, offer.source.kind)
            != (source.edition_id, source.right_id, source.kind)
            or offer.can_propose_delivery is not True):
        return None
    offer_id = offer.capability.offer_id
    return offer_id if type(offer_id) is str and offer_id else None
