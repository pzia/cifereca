"""P0 #5048: read-only, exact-owner admission for private source UI actions.

Catalogue-only sources are handled separately, before any private lookup.
This guard never opens a provider session and never derives rights from
source metadata, catalogue presence, or a current UI selection.
"""
from __future__ import annotations


def private_source_owner_matches(
    service: object, item: object, *, identity_id: str | None = None,
) -> bool:
    """Require one durable provider/credential owner for an admitted identity.

    Headless Core never consults the mutable GTK-selected identity. Its
    synchronous service facade must snapshot the selected scope beforehand.
    """
    provider = getattr(item, "source_provider", None)
    credential = getattr(item, "source_credential", None)
    metadata = getattr(item, "metadata", None)
    if (type(provider) is not str or not provider
            or type(credential) is not str or not credential
            or credential == "__public__"
            or type(identity_id) is not str or not identity_id
            or (type(metadata) is dict
                and metadata.get("catalogue_access") == "shared_read_only")):
        return False
    try:
        owner = service.storage.identity_for_connection(provider, credential)
    except (AttributeError, LookupError, TypeError, ValueError):
        return False
    return type(owner) is str and owner == identity_id


def public_catalogue_source(item: object) -> bool:
    """Passively recognize a *declared* public, non-owned catalogue row.

    This only preserves public browse/UI paths; neither a catalogue label
    nor an unauthenticated row proves a downloadable item or private right.
    """
    return (
        getattr(item, "source_collection", None) in ("public", "public_catalog")
        and getattr(item, "owned", None) is False
    )
