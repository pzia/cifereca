"""#5054: legacy Numilog owned-right collision is plugin-owned integration coverage.

Synthetic account/session and SQLite only. No imports from Core tests, HTTP,
DRM, acquisition, consent, filesystem placement or actual Numilog credentials.
The legacy Core projector remains under test until its production callers go.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace as NS

from cifereca.acquisition_engine import Entitlement, SourceIdentity, stable_id
from cifereca.core.numilog_manifest_rights import mirror_numilog_manifest_rights
from cifereca.models import Account, SearchResult
from cifereca.storage import Storage


def _setup(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    for identity in ("owner", "foreign"):
        storage.identity_put({
            "identity_id": identity, "label": identity, "directory_name": identity,
        })
    credential = "synthetic-credential"
    account = Account(
        display_name="Synthetic owned inventory", unique_id="borrower",
        provider_id="numilog", credential_id=credential, identity_id="owner",
    )
    connection = storage.provider_connection_put({
        "provider_id": "numilog", "credential_id": credential,
        "identity_id": "owner", "enabled": True,
    })
    storage.provider_account_put({
        "connection_id": connection, "identity_id": "owner",
        "provider_id": "numilog", "credential_id": credential,
        "account_id": account.unique_id, "label": "Synthetic borrower",
    })
    session = NS(
        provider_id="numilog", credential_id=credential, connected=True,
        capabilities=NS(personal_library=True), library=lambda: (),
    )
    service = NS(
        storage=storage, accounts=[account],
        _sessions={("numilog", credential): session},
        sync_source_enabled=lambda _account: True,
        identity_by_id=lambda identity: NS(identity_id=identity)
            if identity in ("owner", "foreign") else None,
    )
    digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:20]
    variant = hashlib.sha256(b"synthetic-variant").hexdigest()[:16]
    record = f"numilog:{digest}:edition:{variant}"
    row = SearchResult(
        id=record, record_id=record, docbase="NUMILOG",
        medium="digital", title="Synthetic purchase", author=None,
        type="Livre numérique", format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="NUMILOG_LIBRARY",
        source_provider="numilog", source_credential=credential,
        source_collection="library", content_type="ebook",
        owned=True, downloadable=True,
    )
    return service, account, row


def test_legacy_numilog_canonical_race_preserves_foreign_owner(tmp_path, monkeypatch):
    service, account, purchase = _setup(tmp_path)
    try:
        source = SourceIdentity(
            "numilog", account.credential_id, account.unique_id,
            account.identity_id,
        )
        canonical = Entitlement(source=source, item_id=purchase.record_id).entitlement_id
        key = (
            account.identity_id, account.credential_id,
            account.unique_id, purchase.record_id,
        )
        fallback = stable_id("numilog-owned-identity", *key)
        original = service.storage.numilog_entitlement_reservations
        injected = [False]

        def race_after_reservation(*args, **kwargs):
            observed = original(*args, **kwargs)
            if not injected[0]:
                injected[0] = True
                service.storage.entitlement_put({
                    "entitlement_id": canonical, "provider_id": "numilog",
                    "credential_id": account.credential_id,
                    "account_id": account.unique_id,
                    "identity_id": "foreign", "item_id": purchase.record_id,
                    "entitlement_type": "owned", "state": "active",
                    "metadata": {"title": "PRIVATE FOREIGN OWNER"},
                })
            return observed

        monkeypatch.setattr(
            service.storage, "numilog_entitlement_reservations",
            race_after_reservation,
        )
        assert mirror_numilog_manifest_rights(
            service, [purchase], identity_id=account.identity_id,
            credential_id=account.credential_id, account_id=account.unique_id,
        ) == 1
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == 2
        foreign = next(row for row in rights if row["entitlement_id"] == canonical)
        own = next(row for row in rights if row["entitlement_id"] == fallback)
        assert foreign["identity_id"] == "foreign"
        assert foreign["metadata"]["title"] == "PRIVATE FOREIGN OWNER"
        assert own["identity_id"] == "owner"
        assert own["account_id"] == account.unique_id
        assert own["metadata"]["source"] == "numilog.authenticated_library"
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
        assert mirror_numilog_manifest_rights(
            service, [purchase], identity_id=account.identity_id,
            credential_id=account.credential_id, account_id=account.unique_id,
        ) == 1
        assert len(service.storage.entitlements(provider_id="numilog")) == 2
    finally:
        service.storage.close()
