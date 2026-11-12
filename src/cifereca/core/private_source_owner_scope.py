"""P0 #5048: read-only, exact-owner admission for private source UI actions.

Catalogue-only sources are handled separately, before any private lookup.
This guard never opens a provider session and never derives rights from
source metadata, catalogue presence, or a current UI selection.
"""
from __future__ import annotations


def private_source_owner_matches(service: object, item: object) -> bool:
    """Require one durable provider/credential owner equal to current identity."""
    provider = getattr(item, "source_provider", None)
    credential = getattr(item, "source_credential", None)
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    metadata = getattr(item, "metadata", None)
    if (type(provider) is not str or not provider
            or type(credential) is not str or not credential
            or credential == "__public__"
            or type(identity) is not str or not identity
            or (type(metadata) is dict
                and metadata.get("catalogue_access") == "shared_read_only")):
        return False
    try:
        owner = service.storage.identity_for_connection(provider, credential)
    except (AttributeError, LookupError, TypeError, ValueError):
        return False
    return type(owner) is str and owner == identity
