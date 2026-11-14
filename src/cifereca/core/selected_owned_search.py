"""Explicit metadata-only source card -> exact currently downloadable offer.

A remote catalogue row never attests ownership, a URL or permission to GET.
The live provider session selects an offer only after exact user action, while
the Core executor verifies account/session, completed bytes and placement.
"""
from __future__ import annotations

from ..models import SearchResult
from ..providers import ProviderConfigurationError


def obtain_selected_owned(
    service, item: SearchResult, *, identity_id: str | None = None,
):
    """Compatibility entrypoint: no persisted provider right is consulted."""
    try:
        if (type(identity_id) is not str or not identity_id
                or len(identity_id) > 1024
                or any(c in identity_id for c in "\x00\r\n")
                or type(item) is not SearchResult
                or type(item.source_provider) is not str
                or not item.source_provider
                or type(item.source_credential) is not str
                or not item.source_credential
                or type(item.record_id) is not str or not item.record_id
                or item.id != item.record_id
                or item.source_collection != "library"
                or item.medium != "digital"
                or item.download_url is not None
                or item.owned is not True
                or item.downloadable is not True
                or type(item.metadata) is not dict
                or item.metadata.get("search_provenance") == "known_catalog_stale"):
            raise ValueError("explicit_source_unproven")
        from .selected_source_offer import (
            select_source_offer_target, obtain_source_offer,
        )
        target = select_source_offer_target(
            service, provider_id=item.source_provider,
            credential_id=item.source_credential, item_id=item.record_id,
            identity_id=identity_id,
        )
        if target is None:
            raise ValueError("explicit_source_not_bound")
        return obtain_source_offer(service, target)
    except Exception as exc:
        from ..plugin_api import Cancelled
        if isinstance(exc, Cancelled):
            raise
        raise ProviderConfigurationError("canonical_owned_action_unavailable") from None
