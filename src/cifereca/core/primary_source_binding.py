"""Read-only exact primary connection binding for any provider source.

A local enabled connection is only origin/scope, never a current loan,
delivery offer, ADEPT authority or borrowing permission.
"""
from __future__ import annotations

from ..models import SearchResult


def configured_primary_source_current(\n    service, item: SearchResult, identity_id: str, *,\n    provider_id: str, selection_key: str,\n) -> bool:
    """Inert, exact-owner current login check; never select another identity.

    Check again inside the provider's Core lease to cover edits occurring
    after button rendering and during descriptive preflight. No profile ADEPT
    enumeration or remote request belongs to this borrowing admission.
    """
    if (type(provider_id) is not str or not provider_id\n            or type(selection_key) is not str or not selection_key\n            or type(item) is not SearchResult
            or item.source_provider != provider_id
            or type(item.source_credential) is not str
            or not item.source_credential
            or type(identity_id) is not str or not identity_id
            or (type(getattr(item, "metadata", None)) is dict
                and item.metadata.get("catalogue_access") == "shared_read_only")):
        # A shared catalogue projection is never an action source, even
        # when its owner later becomes the selected identity (A -> B).
        return False
    try:
        saved = service.config.identity(identity_id)
        if type(saved) is not dict:
            return False
        selected = saved.get(selection_key)
        if selected is not None and (
                type(selected) is not str or not selected
                or len(selected) > 256 or selected != selected.strip()
                or not selected.isprintable()):
            return False

        rows = service.storage.provider_connections(identity_id=identity_id)
        if type(rows) is not list or len(rows) > 512:
            return False
        enabled = [
            row for row in rows
            if type(row) is dict
            and row.get("identity_id") == identity_id
            and row.get("provider_id") == provider_id
            and row.get("enabled") is True
            and type(row.get("credential_id")) is str
            and row["credential_id"]
        ]
        if selected is None:
            # The legacy implicit selection is safe only with a single
            # eligible provider source. Never take the first of several.
            return (len(enabled) == 1
                    and enabled[0]["credential_id"] == item.source_credential)
        if selected != item.source_credential:
            return False
        if (len([row for row in enabled
                 if row["credential_id"] == selected]) != 1):
            return False
        configured = service.config.provider_accounts()
        if type(configured) is not list or len(configured) > 2048:
            return False
        matches = [
            row for row in configured
            if type(row) is dict
            and type(row.get("id")) is str and row["id"] == selected
            and type(row.get("provider")) is str
            and row["provider"].casefold() == provider_id.casefold()
            and row.get("identity_id") == identity_id
            and row.get("enabled", True) is True
        ]
        return len(matches) == 1
    except Exception:
        return False



__all__ = ["configured_primary_source_current"]
