"""P0 #3196: private Kobo inventory is bibliographic, not purchase authority.

All data is synthetic. SQLite is real; no provider request, ADEPT, download,
filesystem scan or acquisition is permitted by this projection.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, SearchResult
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(root: Path) -> LibraryService:
    config = Config(root / "config.json")
    config.set("library.directory", str(root / "EBooks"))
    config.set("identities", [
        {"id": "alice", "label": "Alice", "directory_name": "alice"},
        {"id": "bob", "label": "Bob", "directory_name": "bob"},
    ])
    config.set("providers.accounts", [])
    config.save()
    return LibraryService(config, Storage(root / "catalog.sqlite3"), Diagnostics())


def _row(*, product="owned-1", credential="vault-alice", owned=True, archived=False):
    return SearchResult(
        id=f"kobo:{credential}:{product}", record_id=product, docbase="KOBO",
        medium="digital", title="Livre acheté", author="Auteur",
        type="Livre numérique Kobo", format="EPUB", publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="KOBO", source_provider="kobo",
        source_credential=credential, source_collection="library",
        owned=owned, downloadable=False,
        metadata={"archived": archived},
    )


def test_explicit_kobo_account_sync_materializes_right_without_download(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        account = Account(
            provider_id="kobo", credential_id="vault-alice",
            identity_id="alice", unique_id="acc-personal",
        )
        calls = []
        session = SimpleNamespace(
            connected=True, provider_id="kobo", credential_id="vault-alice",
            capabilities=SimpleNamespace(personal_library=True, loans=False,
                                         direct_download=False),
            library=lambda: (calls.append("library") or [_row()]),
        )
        service.accounts = [account]
        connection_id = service.storage.provider_connection_put({
            "identity_id": account.identity_id, "provider_id": "kobo",
            "credential_id": account.credential_id, "enabled": True,
        })
        service.storage.provider_account_put({
            "connection_id": connection_id, "identity_id": account.identity_id,
            "provider_id": "kobo", "credential_id": account.credential_id,
            "account_id": account.unique_id,
        })
        service._sessions[service._session_key("kobo", account.credential_id)] = session
        assert not hasattr(service, "_acquire_personal_library_item")
        result = service._synchronize_account_source_impl(account)
        assert "owned_rights" not in result
        assert service.storage.entitlements(provider_id="kobo") == []
        assert calls == ["library"]
        assert len(service.storage.catalog_entries()) == 1
        assert service.library_entries(identity_id="alice") == []
    finally:
        service.close()


def test_opening_mirror_reuses_one_shot_kobo_personal_manifest(tmp_path, monkeypatch):
    """A generator of attested owned books must feed rights AND catalog once."""
    service = _service(tmp_path)
    credential = "vault-alice"
    connection_id = service.storage.provider_connection_put({
        "identity_id": "alice", "provider_id": "kobo",
        "credential_id": credential, "label": "Owned Kobo",
    })
    service.storage.provider_account_put({
        "connection_id": connection_id, "identity_id": "alice",
        "provider_id": "kobo", "credential_id": credential,
        "account_id": "acc-personal",
    })
    account = Account(
        provider_id="kobo", credential_id=credential,
        identity_id="alice", unique_id="acc-personal",
    )
    service.accounts = [account]
    rows = iter([_row(credential=credential)])
    calls = []
    service._sessions = {service._session_key("kobo", credential): SimpleNamespace(
        provider_id="kobo", credential_id=credential, connected=True,
        capabilities=SimpleNamespace(personal_library=True, direct_download=False),
        library=lambda: (calls.append("library") or rows),
    )}
    monkeypatch.setattr(service, "sync_source_enabled", lambda _account: True)
    try:
        summary = service.mirror_personal_libraries()
        assert summary[f"kobo:{credential}"] == 1
        assert calls == ["library"]
        rights = service.storage.entitlements(provider_id="kobo", state="active")
        assert rights == []  # No grant from a one-shot library iterator.
        assert len(service.storage.catalog_entries()) == 1
        assert service.library_entries(identity_id="alice") == []
    finally:
        service.close()


def test_explicit_sync_reuses_one_shot_kobo_personal_manifest(tmp_path, monkeypatch):
    """Explicit source sync has the same double-consumption boundary."""
    service = _service(tmp_path)
    account = Account(
        provider_id="kobo", credential_id="vault-alice",
        identity_id="alice", unique_id="acc-personal",
    )
    calls = []
    one_shot = iter([_row()])
    session = SimpleNamespace(
        connected=True, provider_id="kobo", credential_id="vault-alice",
        capabilities=SimpleNamespace(personal_library=True, loans=False,
                                     direct_download=False),
        library=lambda: (calls.append("library") or one_shot),
    )
    service.accounts = [account]
    connection_id = service.storage.provider_connection_put({
        "identity_id": account.identity_id, "provider_id": "kobo",
        "credential_id": account.credential_id, "enabled": True,
    })
    service.storage.provider_account_put({
        "connection_id": connection_id, "identity_id": account.identity_id,
        "provider_id": "kobo", "credential_id": account.credential_id,
        "account_id": account.unique_id,
    })
    service._sessions[service._session_key("kobo", account.credential_id)] = session
    try:
        result = service._synchronize_account_source_impl(account)
        assert "owned_rights" not in result
        assert service.storage.entitlements(provider_id="kobo") == []
        assert result["library"] == 1
        assert calls == ["library"]
        assert len(service.storage.catalog_entries()) == 1
    finally:
        service.close()
