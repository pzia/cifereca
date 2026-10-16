"""P0 #1337: Kobo authenticated personal manifest -> durable Library rights.

All data is synthetic. SQLite is real; no provider request, ADEPT, download,
filesystem scan or acquisition is permitted by this projection.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile

from cifereca.config import Config
from cifereca.core.kobo_manifest_rights import mirror_kobo_manifest_rights
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


def test_attested_owned_kobo_is_in_library_offline_without_download_or_path(tmp_path):
    service = _service(tmp_path)
    try:
        row = _row()
        assert service.library_entries(identity_id="alice") == []
        count = mirror_kobo_manifest_rights(
            service, [row], identity_id="alice",
            credential_id="vault-alice", account_id="account-a",
        )
        assert count == 1
        rights = service.storage.entitlements(provider_id="kobo")
        assert len(rights) == 1
        assert (rights[0]["credential_id"], rights[0]["account_id"],
                rights[0]["identity_id"], rights[0]["item_id"]) == (
                    "vault-alice", "account-a", "alice", "owned-1"
                )
        library = service.library_entries(identity_id="alice")
        assert len(library) == 1
        assert library[0]["state"] == "remote_owned"
        assert library[0]["title"] == "Livre acheté"
        assert library[0]["local"] is False
        assert library[0]["local_path"] is None
        assert library[0]["artifact_id"] is None
        assert library[0]["entitlement_type"] == "owned"
        assert service.library_entries(identity_id="bob") == []
        # Repeat a fresh manifest without creating another right/row.
        mirror_kobo_manifest_rights(
            service, [row], identity_id="alice",
            credential_id="vault-alice", account_id="account-a",
        )
        assert len(service.storage.entitlements(provider_id="kobo")) == 1
        assert len(service.library_entries(identity_id="alice")) == 1
    finally:
        service.close()
    # SQLite survives restart and reads offline: no connected provider needed.
    reopened = _service(tmp_path)
    try:
        assert [book["state"] for book in reopened.library_entries(identity_id="alice")] == ["remote_owned"]
        assert reopened.library_entries(identity_id="bob") == []
    finally:
        reopened.close()


def test_three_accounts_identity_scoping_archives_and_partial_manifest(tmp_path):
    service = _service(tmp_path)
    try:
        for ident, credential, account in (
            ("alice", "vault-a", "acc-1"),
            ("alice", "vault-b", "acc-2"),
            ("bob", "vault-c", "acc-3"),
        ):
            mirror_kobo_manifest_rights(
                service, [_row(credential=credential)], identity_id=ident,
                credential_id=credential, account_id=account,
            )
        assert len(service.storage.entitlements(provider_id="kobo")) == 3
        alice = service.library_entries(identity_id="alice")
        bob = service.library_entries(identity_id="bob")
        assert len(alice) == len(bob) == 1
        assert {p["account_id"] for p in alice[0]["possessions"]} == {"acc-1", "acc-2"}
        assert {p["account_id"] for p in bob[0]["possessions"]} == {"acc-3"}
        # An archived edition is visible but cannot masquerade as an active
        # remote_owned right; it never becomes local or downloadable.
        mirror_kobo_manifest_rights(
            service, [_row(product="archived", credential="vault-c", archived=True)],
            identity_id="bob", credential_id="vault-c", account_id="acc-3",
        )
        archived = next(x for x in service.library_entries(identity_id="bob")
                        if x["item_id"] == "archived")
        assert archived["state"] == "remote_archived"
        assert archived["local"] is False
        assert archived["possessions"][0]["state"] == "archived"
        # Empty/failed/partial refresh is NOT authoritative for revocation.
        mirror_kobo_manifest_rights(
            service, [], identity_id="alice", credential_id="vault-a",
            account_id="acc-1",
        )
        assert len(service.storage.entitlements(provider_id="kobo")) == 4
    finally:
        service.close()


def test_shared_credentials_never_overwrite_other_identity_grant(tmp_path):
    service = _service(tmp_path)
    try:
        for ident in ("alice", "bob"):
            mirror_kobo_manifest_rights(
                service, [_row(credential="shared")],
                identity_id=ident, credential_id="shared", account_id="same",
            )
        rights = service.storage.entitlements(provider_id="kobo")
        assert len(rights) == 2
        assert {r["identity_id"] for r in rights} == {"alice", "bob"}
        assert len({r["entitlement_id"] for r in rights}) == 2
        assert len(service.library_entries(identity_id="alice")) == 1
        assert len(service.library_entries(identity_id="bob")) == 1
    finally:
        service.close()


def test_public_search_never_asserts_personal_ownership(tmp_path):
    service = _service(tmp_path)
    try:
        catalogue = _row(credential="public")
        service._mirror_remote_results([catalogue], identity_id="alice")
        assert service.storage.entitlements(provider_id="kobo") == []
        assert service.storage.catalog_entries()[0]["local_state"] == "remote_catalog"
        assert service.library_entries(identity_id="alice") == []
        assert mirror_kobo_manifest_rights(
            service, [catalogue], identity_id="alice",
            credential_id="different-credential", account_id="private",
        ) == 0
        assert mirror_kobo_manifest_rights(
            service, [replace(catalogue, owned=False)],
            identity_id="alice", credential_id="public", account_id="private",
        ) == 0
        assert service.storage.entitlements(provider_id="kobo") == []
    finally:
        service.close()


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
        service.storage.provider_connection_put({
            "identity_id": account.identity_id, "provider_id": "kobo",
            "credential_id": account.credential_id, "enabled": True,
        })
        service._sessions[service._session_key("kobo", account.credential_id)] = session
        monkeypatch.setattr(service, "_acquire_personal_library_item",
                            lambda *_args, **_kw: (_ for _ in ()).throw(
                                AssertionError("No acquisition from account sync")
                            ))
        result = service._synchronize_account_source_impl(account)
        assert result["owned_rights"] == 1
        assert calls == ["library"]
        assert service.storage.catalog_entries()[0]["local_state"] == "remote_owned"
        assert len(service.library_entries(identity_id="alice")) == 1
        assert service.library_entries(identity_id="alice")[0]["local"] is False
    finally:
        service.close()


def test_opening_mirror_reuses_one_shot_kobo_personal_manifest(tmp_path, monkeypatch):
    """A generator of attested owned books must feed rights AND catalog once."""
    service = _service(tmp_path)
    credential = "vault-alice"
    service.storage.provider_connection_put({
        "identity_id": "alice", "provider_id": "kobo",
        "credential_id": credential, "label": "Owned Kobo",
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
        assert len(rights) == 1
        assert service.storage.catalog_entries()[0]["local_state"] == "remote_owned"
        library = service.library_entries(identity_id="alice")
        assert len(library) == 1 and library[0]["state"] == "remote_owned"
        assert not library[0]["local"] and library[0]["local_path"] is None
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
    service.storage.provider_connection_put({
        "identity_id": account.identity_id, "provider_id": "kobo",
        "credential_id": account.credential_id, "enabled": True,
    })
    service._sessions[service._session_key("kobo", account.credential_id)] = session
    try:
        result = service._synchronize_account_source_impl(account)
        assert result["owned_rights"] == 1
        assert result["library"] == 1
        assert calls == ["library"]
        assert service.storage.catalog_entries()[0]["local_state"] == "remote_owned"
    finally:
        service.close()
