"""Synthetic persisted-right fixture for read-only UI tests.

Only creates explicit test-owned SQLite rows. Never interprets provider
SearchResult.owned, manifests, catalogues, or availability as grant evidence.
"""
from __future__ import annotations

from cifereca.acquisition_engine import Entitlement, SourceIdentity


def seed_persisted_purchase(service, *, identity_id: str, credential_id: str,
                            account_id: str, edition_id: str,
                            state: str = "active") -> None:
    source = SourceIdentity("kobo", credential_id, account_id, identity_id)
    key = Entitlement(source=source, item_id=edition_id).entitlement_id
    service.storage.entitlement_put({
        "entitlement_id": key,
        "provider_id": "kobo",
        "credential_id": credential_id,
        "account_id": account_id,
        "identity_id": identity_id,
        "item_id": edition_id,
        "entitlement_type": "owned",
        "state": state,
        "metadata": {"title": edition_id, "source": "synthetic-test-only"},
    })
