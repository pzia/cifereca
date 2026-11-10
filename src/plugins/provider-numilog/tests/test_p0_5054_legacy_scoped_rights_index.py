"""Legacy Numilog projector security regressions, isolated in provider tests until its removal."""
from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from cifereca.acquisition_engine import Entitlement, SourceIdentity, stable_id
from cifereca.core import numilog_manifest_rights
from cifereca.core.numilog_manifest_rights import mirror_numilog_manifest_rights
from cifereca.storage import Storage
from types import SimpleNamespace as NS
from cifereca.models import Account, SearchResult
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService


def _service(tmp_path):
    cfg = Config(tmp_path / "config.json")
    cfg.set("library.directory", str(tmp_path / "EBooks"))
    cfg.set("identities", [
        {"id": "default", "label": "Default", "directory_name": "default"},
        {"id": "alex", "label": "Alex", "directory_name": "alex"},
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



def _foreign(storage, entitlement_id, item_id, credential, account_id):
    storage.entitlement_put({
        "entitlement_id": entitlement_id, "provider_id": "numilog",
        "credential_id": credential, "account_id": account_id,
        "identity_id": "alex", "item_id": item_id,
        "entitlement_type": "owned", "state": "active",
        "metadata": {"title": "SECRET_OTHER_IDENTITY"},
    })


@pytest.mark.parametrize("n", [1, 310])
def test_numilog_rights_mirror_queries_only_current_scope_and_candidate_ids(
    tmp_path, monkeypatch, n,
):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=0)
        # Foreign rights are legitimate storage rows; their metadata may
        # never need to be decoded or enumerated to mirror this account.
        for i in range(65):
            item = "unrelated-%d" % i
            _foreign(
                service.storage, "foreign-owned-%d" % i, item,
                "other-vault", "other-account",
            )
        rows = [_row(account.credential_id, i) for i in range(n)]
        monkeypatch.setattr(
            service.storage, "entitlements",
            lambda **_kw: pytest.fail("global rights enumeration forbidden"),
        )
        statements = []
        db = service.storage._connect()
        db.set_trace_callback(statements.append)
        try:
            count = mirror_numilog_manifest_rights(
                service, rows, identity_id=account.identity_id,
                credential_id=account.credential_id,
                account_id=account.unique_id,
            )
        finally:
            db.set_trace_callback(None)
        assert count == n
        assert all("metadata_json" not in sql for sql in statements
                   if "SELECT" in sql and "entitlements" in sql)
        assert any("AND identity_id=" in sql and "AND account_id=" in sql
                   for sql in statements)
        assert any("entitlement_id IN (" in sql for sql in statements)
        # 310 rows = 620 candidate IDs; bounded 300-ID chunks, no N+1 reads.
        lookups = [sql for sql in statements if "entitlement_id IN (" in sql]
        assert len(lookups) == (2 * n + 299) // 300
        assert calls == []
    finally:
        service.close()


def test_foreign_collision_uses_scoped_fallback_without_reassigning_owner(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=0)
        row = _row(account.credential_id, 0)
        source = SourceIdentity(
            "numilog", account.credential_id, account.unique_id,
            account.identity_id,
        )
        canonical = Entitlement(source=source, item_id=row.record_id).entitlement_id
        _foreign(
            service.storage, canonical, row.record_id,
            account.credential_id, account.unique_id,
        )
        for _ in range(2):
            assert mirror_numilog_manifest_rights(
                service, [row], identity_id=account.identity_id,
                credential_id=account.credential_id,
                account_id=account.unique_id,
            ) == 1
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == 2
        assert {r["identity_id"] for r in rights} == {"alex", "default"}
        foreign = next(r for r in rights if r["identity_id"] == "alex")
        own = next(r for r in rights if r["identity_id"] == "default")
        assert foreign["entitlement_id"] == canonical
        assert foreign["metadata"]["title"] == "SECRET_OTHER_IDENTITY"
        key = (account.identity_id, account.credential_id,
               account.unique_id, row.record_id)
        assert own["entitlement_id"] == stable_id("numilog-owned-identity", *key)
        assert own["metadata"]["source"] == "numilog.authenticated_library"
        assert calls == []
    finally:
        service.close()


def test_double_foreign_collision_refuses_to_overwrite_fallback(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=0)
        row = _row(account.credential_id, 0)
        source = SourceIdentity("numilog", account.credential_id,
                                account.unique_id, account.identity_id)
        key = (account.identity_id, account.credential_id,
               account.unique_id, row.record_id)
        _foreign(service.storage,
                 Entitlement(source=source, item_id=row.record_id).entitlement_id,
                 row.record_id, account.credential_id, account.unique_id)
        _foreign(service.storage, stable_id("numilog-owned-identity", *key),
                 row.record_id, account.credential_id, account.unique_id)
        assert mirror_numilog_manifest_rights(
            service, [row], identity_id=account.identity_id,
            credential_id=account.credential_id,
            account_id=account.unique_id,
        ) == 0
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == 2
        assert all(r["identity_id"] == "alex"
                   and r["metadata"]["title"] == "SECRET_OTHER_IDENTITY"
                   for r in rights)
        assert calls == []
    finally:
        service.close()


def test_lazy_numilog_inventory_rechecks_binding_before_any_right_write(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=0)

        def old_owner_pages():
            yield _row(account.credential_id, 0)
            # Credential is reassigned while the next page is yielded.
            service.storage.provider_connection_put({
                "provider_id": "numilog", "credential_id": account.credential_id,
                "identity_id": "alex", "enabled": True,
            })
            yield _row(account.credential_id, 1)

        assert mirror_numilog_manifest_rights(
            service, old_owner_pages(), identity_id=account.identity_id,
            credential_id=account.credential_id,
            account_id=account.unique_id,
        ) == 0
        assert service.storage.entitlements(provider_id="numilog") == []
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("invalid", [
    ("", "vault", "account", ()),
    ("alice", "vault", "account", ("",)),
    ("alice", "vault", "account", ("valid-id", None)),
    ("alice", "vault", "account", ["valid-id"]),
    ("alice\nSECRET", "vault", "account", ()),
])
def test_numilog_scope_index_rejects_invalid_inputs_without_sql(tmp_path, invalid):
    storage = Storage(tmp_path / "numilog-index.sqlite3")
    try:
        seen = []
        storage._connect().set_trace_callback(seen.append)
        with pytest.raises(ValueError, match="^invalid_numilog_entitlement_scope$"):
            storage.numilog_entitlement_reservations(
                identity_id=invalid[0], credential_id=invalid[1],
                account_id=invalid[2], candidate_ids=invalid[3],
            )
        assert seen == []
    finally:
        storage.close()


def test_overbudget_numilog_manifest_is_error_not_fake_empty_success(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=0)
        monkeypatch.setattr(numilog_manifest_rights, "_MAX_NUMILOG_MIRROR_ROWS", 2)
        with pytest.raises(ValueError, match="^numilog_rights_budget_exhausted$"):
            mirror_numilog_manifest_rights(
                service, (_row(account.credential_id, i) for i in range(3)),
                identity_id=account.identity_id,
                credential_id=account.credential_id,
                account_id=account.unique_id,
            )
        assert service.storage.entitlements(provider_id="numilog") == []
        assert calls == []
    finally:
        service.close()
