"""P0 #3171: legacy Numilog inventory/preflight requires the real Core account.

A connected session plus a credential's enabled provider connection is NOT
proof that this specific borrower is still attached to that connection. All
tests are local SQLite v14, no private HTTP or book delivery.
"""
from cifereca.core.numilog_manifest_rights import (
    mirror_numilog_manifest_rights, numilog_inventory_binding_valid,
)
from types import SimpleNamespace as NS
import hashlib

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, SearchResult
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path):
    cfg = Config(tmp_path / "config.json")
    cfg.set("library.directory", str(tmp_path / "EBooks"))
    cfg.set("identities", [
        {"id": "default", "label": "Hugues", "directory_name": "hugues", "preferences": {"reading_languages": ["fr"]}},
        {"id": "alex", "label": "Alex", "directory_name": "alex", "preferences": {"reading_languages": ["fr"]}},
    ])
    cfg.set("providers.accounts", [])
    return LibraryService(cfg, Storage(tmp_path / "db.sqlite3"), Diagnostics())

def _row(credential: str, index: int) -> SearchResult:
    scope = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:20]
    variant = hashlib.sha256(f"variant-{index}".encode("utf-8")).hexdigest()[:16]
    record_id = f"numilog:{scope}:lid{index}:{variant}"
    return SearchResult(
        id=record_id, record_id=record_id, docbase="NUMILOG",
        medium="digital", title=f"Achat #{index}", author=None,
        type="Livre numérique", format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="NUMILOG_LIBRARY",
        source_provider="numilog", source_credential=credential,
        source_collection="library", content_type="ebook",
        owned=True, downloadable=True,
    )


def _connect(service, *, identity="default", credential="numilog-private",
             number=2, failed=False):
    calls = []
    account = Account(
        display_name="Boutique Numilog", unique_id="account",
        provider_id="numilog", credential_id=credential, identity_id=identity,
    )
    service.accounts.append(account)
    connection = service.storage.provider_connection_put({
        "provider_id": "numilog", "credential_id": credential,
        "identity_id": identity, "enabled": True,
    })
    service.storage.provider_account_put({
        "connection_id": connection, "identity_id": identity,
        "provider_id": "numilog", "credential_id": credential,
        "account_id": account.unique_id, "label": "Synthetic borrower",
    })
    rows = [_row(credential, i) for i in range(number)]

    def library():
        calls.append("library")
        if failed:
            raise RuntimeError(
                "ACSM https://entrepot2r.numilog.com/private?token=SIGNATURE-SECRET "
                "Cookie=SESSION-SECRET"
            )
        # Generator: mirroring must not exhaust this before indexing.
        yield from rows

    def forbidden(*_args, **_kwargs):
        calls.append("FORBIDDEN DOWNLOAD")
        raise AssertionError("No download may be launched from inventory")

    session = NS(
        provider_id="numilog", credential_id=credential, connected=True,
        capabilities=NS(loans=False, personal_library=True, direct_download=True),
        library=library, download_direct=forbidden,
        # Deliberately no download_library_item method: the real Numilog
        # subclass inherits an unsupported one from ProviderSession.
    )
    service._sessions[service._session_key("numilog", credential)] = session
    return account, session, calls


def test_legacy_numilog_borrower_must_be_registered_not_just_connected(tmp_path):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        assert numilog_inventory_binding_valid(service, account, session=session)
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "DELETE FROM provider_accounts WHERE identity_id=? "
                "AND provider_id='numilog' AND credential_id=? "
                "AND account_id=?", (
                    account.identity_id, account.credential_id, account.unique_id,
                ),
            )
        assert not numilog_inventory_binding_valid(service, account, session=session)
        assert mirror_numilog_manifest_rights(
            service, [_row(account.credential_id, 0)],
            identity_id=account.identity_id,
            credential_id=account.credential_id,
            account_id=account.unique_id,
        ) == 0
        assert service.storage.entitlements(provider_id="numilog") == []
        assert calls == []
    finally:
        service.close()


def test_foreign_connection_cannot_claim_borrower_amid_100_siblings(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        provider, credential, owner = (
            account.provider_id, account.credential_id, account.identity_id,
        )
        current = next(
            c["connection_id"] for c in service.storage.provider_connections(
                identity_id=owner,
            ) if c["provider_id"] == provider and c["credential_id"] == credential
        )
        for n in range(100):
            service.storage.provider_account_put({
                "connection_id": current, "identity_id": owner,
                "provider_id": provider, "credential_id": credential,
                "account_id": f"sibling-{n}", "label": "Synthetic sibling",
            })
        other = service.storage.provider_connection_put({
            "provider_id": "unrelated", "credential_id": "foreign-card",
            "identity_id": owner, "enabled": True,
        })
        # An old registry walk or identity_for_connection() would still
        # accept the exact textual borrower; the current-connection join
        # must reject it. Forbid falling back to those old enumeration APIs.
        monkeypatch.setattr(
            service.storage, "provider_connections",
            lambda **kw: (_ for _ in ()).throw(
                AssertionError("unbounded provider registry scan")
            ),
        )
        monkeypatch.setattr(
            service.storage, "identity_for_connection",
            lambda *_: (_ for _ in ()).throw(
                AssertionError("separate unpinned owner read")
            ),
        )
        assert numilog_inventory_binding_valid(service, account, session=session)
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id=? AND provider_id='numilog' "
                "AND credential_id=? AND account_id=?",
                (other, owner, credential, account.unique_id),
            )
        assert not numilog_inventory_binding_valid(service, account, session=session)
        assert mirror_numilog_manifest_rights(
            service, [_row(account.credential_id, 0)],
            identity_id=owner, credential_id=credential,
            account_id=account.unique_id,
        ) == 0
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id=? AND provider_id='numilog' "
                "AND credential_id=? AND account_id=?",
                (current, owner, credential, account.unique_id),
            )
        assert numilog_inventory_binding_valid(service, account, session=session)
        assert calls == []
    finally:
        service.close()
