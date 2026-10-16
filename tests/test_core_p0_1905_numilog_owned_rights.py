"""P0 #1905: private Numilog purchases, immutable source provenance and no download.

Fixtures use only fictitious stable Numilog (lid, variant) identifiers; neither
live Numilog nor ACSM, ADEPT, DRM/network requests are exercised.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca.core.numilog_manifest_rights import mirror_numilog_manifest_rights
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
from tests.test_sync_state_contract import _service


def test_authenticated_numilog_inventory_persists_distinct_rights_offline(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=3)
        monkeypatch.setattr(
            service, "_acquire_personal_library_item",
            lambda *_a, **_kw: pytest.fail("implicit library backup forbidden"),
        )
        first = service.mirror_personal_libraries()
        second = service.mirror_personal_libraries()
        assert first == second == {"numilog:numilog-private": 3}
        assert calls == ["library", "library"]
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == 3
        assert len({right["item_id"] for right in rights}) == 3
        assert {right["identity_id"] for right in rights} == {"default"}
        assert {right["account_id"] for right in rights} == {account.unique_id}
        assert all(right["entitlement_type"] == "owned"
                   and right["state"] == "active" for right in rights)
        assert all(right["metadata"]["source"] == "numilog.authenticated_library"
                   for right in rights)
        assert len(service.storage.catalog_entries(state="remote_owned")) == 3
        assert service.library_entries(identity_id="alex") == []
        assert not list((tmp_path / "EBooks").rglob("*.epub"))
        assert not list((tmp_path / "EBooks").rglob("*.acsm"))
    finally:
        service.close()
    reopened = _service(tmp_path)
    try:
        assert len(reopened.storage.entitlements(provider_id="numilog")) == 3
        assert len(reopened.library_entries(identity_id="default")) == 3
        assert reopened.library_entries(identity_id="alex") == []
    finally:
        reopened.close()


def test_public_owned_flag_without_private_right_never_grants_purchase(tmp_path):
    service = _service(tmp_path)
    try:
        row = _row("vault-no-right", 1)
        # Public SearchResult flags cannot prove ownership, even if a plugin
        # incorrectly labels a catalogue row as from the personal library.
        assert service._mirror_remote_results([row], identity_id="default") == 1
        assert service.storage.entitlements(provider_id="numilog") == []
        assert len(service.storage.catalog_entries(state="remote_owned")) == 0
        assert len(service.storage.catalog_entries(state="remote_catalog")) == 1

        _account, _session, _calls = _connect(
            service, credential="vault-no-right", number=2,
        )
        service.mirror_personal_libraries()
        assert len(service.storage.entitlements(provider_id="numilog")) == 2
        # A previously displayed item becomes owned only after the exact
        # private record ID is confirmed. Other identities remain isolated.
        service._mirror_remote_results([row], identity_id="default")
        assert len(service.storage.catalog_entries(state="remote_owned")) == 2
        service._mirror_remote_results([row], identity_id="alex")
        assert len([x for x in service.storage.catalog_entries(state="remote_owned")
                    if x["identity_id"] == "alex"]) == 0
    finally:
        service.close()


def test_homonymous_variants_across_two_numilog_accounts_and_three_identities(
    tmp_path,
):
    cfg = Config(tmp_path / "config.json")
    cfg.set("library.directory", str(tmp_path / "EBooks"))
    cfg.set("identities", [
        {"id": identity, "label": identity, "directory_name": identity}
        for identity in ("alice", "bob", "carol")
    ])
    cfg.set("providers.accounts", [])
    service = LibraryService(cfg, Storage(tmp_path / "db.sqlite3"), Diagnostics())
    try:
        _first, session_a, calls_a = _connect(
            service, identity="alice", credential="vault-a", number=2,
        )
        _second, session_b, calls_b = _connect(
            service, identity="bob", credential="vault-b", number=2,
        )
        def two_variants(credential):
            rows = [
                replace(_row(credential, 0), title="Titre commun"),
                replace(_row(credential, 1), title="Titre commun"),
            ]
            return rows
        session_a.library = lambda: (calls_a.append("distinct") or two_variants("vault-a"))
        session_b.library = lambda: (calls_b.append("distinct") or two_variants("vault-b"))
        # Some provider pages may repeat the same purchase. It is one right.
        service.mirror_personal_libraries()
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == 4
        assert len({r["entitlement_id"] for r in rights}) == 4
        assert {r["identity_id"] for r in rights} == {"alice", "bob"}
        assert len([r for r in rights if r["identity_id"] == "alice"]) == 2
        assert len([r for r in rights if r["identity_id"] == "bob"]) == 2
        assert service.library_entries(identity_id="carol") == []
        assert all(r["metadata"]["title"] == "Titre commun" for r in rights)
        assert calls_a == calls_b == ["distinct"]
        assert not list((tmp_path / "EBooks").rglob("*.epub"))
    finally:
        service.close()


@pytest.mark.parametrize("invalid", [
    "missing_account", "rebound", "disabled_connection",
    "ambiguous_account", "unknown_identity",
])
def test_detached_numilog_binding_never_reads_private_inventory_or_mints_right(
    tmp_path, invalid,
):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=1)
        if invalid == "missing_account":
            service.accounts.clear()
        elif invalid == "rebound":
            service.storage.provider_connection_put({
                "identity_id": "alex", "provider_id": "numilog",
                "credential_id": account.credential_id, "enabled": True,
            })
        elif invalid == "disabled_connection":
            service.storage.provider_connection_put({
                "identity_id": "default", "provider_id": "numilog",
                "credential_id": account.credential_id, "enabled": False,
            })
        elif invalid == "ambiguous_account":
            service.accounts.append(replace(account, unique_id="different-purchase"))
        elif invalid == "unknown_identity":
            service.accounts[0] = replace(account, identity_id="unconfigured")
            service.storage.provider_connection_put({
                "identity_id": "unconfigured", "provider_id": "numilog",
                "credential_id": account.credential_id, "enabled": True,
            })
        result = service.mirror_personal_libraries()
        assert result == {} and result.blocked_sources == 1
        assert calls == []
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.catalog_entries() == []
    finally:
        service.close()


def test_scoped_manual_refresh_rejects_rebound_numilog_before_network(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=1)
        broker = SynchronizationBrokerImpl(service)
        sid = service.sync_source_id(account)
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "numilog",
            "credential_id": account.credential_id, "enabled": True,
        })
        receipt = broker.synchronize_source(sid)
        assert not receipt.ok and receipt.facts == {"reason": "source_unavailable"}
        assert calls == []
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.catalog_entries() == []
    finally:
        service.close()


def test_manual_numilog_source_sync_persists_exact_owned_rights_only(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=2)
        broker = SynchronizationBrokerImpl(service)
        receipt = broker.synchronize_source(service.sync_source_id(account))
        assert receipt.ok and receipt.facts["library"] == 2
        assert receipt.facts["owned_rights"] == 2
        assert len(service.storage.entitlements(provider_id="numilog")) == 2
        assert calls == ["library"]
        assert receipt.changed == 4
    finally:
        service.close()


def test_malformed_cross_source_rows_cannot_create_purchase_or_acquire(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        genuine = _row(account.credential_id, 1)
        mismatched_credential = replace(genuine, source_credential="other-secret")
        invalid_id = replace(genuine, record_id="numilog:evil:path:abc")
        false_owner = replace(genuine, owned=False)
        public_catalog = replace(genuine, scenario="NUMILOG_PUBLIC")
        unexpected_collection = replace(genuine, source_collection="catalog")
        session.library = lambda: (
            calls.append("library") or [
                mismatched_credential, invalid_id, false_owner,
                public_catalog, unexpected_collection,
            ]
        )
        monkeypatch.setattr(
            service, "_acquire_personal_library_item",
            lambda *_a, **_kw: pytest.fail("acquisition during inventory"),
        )
        service.mirror_personal_libraries()
        assert calls == ["library"]
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.catalog_entries(state="remote_owned") == []
    finally:
        service.close()


def test_absent_purchases_do_not_revoke_previous_rights(tmp_path):
    service = _service(tmp_path)
    try:
        account, session, _calls = _connect(service, number=1)
        service.mirror_personal_libraries()
        assert len(service.storage.entitlements(provider_id="numilog")) == 1
        session.library = lambda: []
        service.mirror_personal_libraries()
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == 1 and rights[0]["state"] == "active"
    finally:
        service.close()


def test_signed_numilog_urls_and_private_metadata_not_persisted(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        _account, session, _calls = _connect(service, number=0)
        record = replace(
            _row("numilog-private", 0),
            title="Livre de test",
            author="Auteur",
            friendly_url="https://www.numilog.com/123/Test.ebook?auth=SECRET_SIGNATURE",
            thumbnail_url="https://storage.googleapis.com/covers/a.png?token=SECRET_SIGNATURE",
            metadata={"rights": "https://private.invalid/?auth=SECRET_SIGNATURE",
                      "isbn": "9780000000001"},
        )
        session.library = lambda: [record]
        summary = service.mirror_personal_libraries()
        assert summary["numilog:numilog-private"] == 1
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == 1
        assert "SECRET_SIGNATURE" not in json.dumps(rights)
        assert "rights" not in rights[0]["metadata"]
        catalogue = service.storage.catalog_entries()
        assert len(catalogue) == 1
        assert "SECRET_SIGNATURE" not in json.dumps(catalogue)
        assert catalogue[0]["metadata"]["product_url"] is None
        assert catalogue[0]["metadata"]["cover_url"] is None
        assert catalogue[0]["metadata"]["isbn"] == "9780000000001"
    finally:
        service.close()

@pytest.mark.parametrize("mode", ["opening", "manual"])
def test_numilog_rebound_while_fetching_never_persists_stale_rights_or_catalogue(
    tmp_path, monkeypatch, mode,
):
    from cifereca.providers import ProviderConfigurationError

    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)

        def changed_owner_library():
            calls.append("library")
            service.storage.provider_connection_put({
                "identity_id": "alex", "provider_id": "numilog",
                "credential_id": account.credential_id, "enabled": True,
            })
            return [_row(account.credential_id, 0)]

        session.library = changed_owner_library
        if mode == "opening":
            monkeypatch.setattr(service, "_loans_for_account",
                                lambda *_args, **_kwargs: [])
            summary = service.mirror_personal_libraries()
            assert summary == {}
            assert summary.blocked_sources == 1
        else:
            with pytest.raises(ProviderConfigurationError):
                service._synchronize_account_source_impl(account)
        assert calls == ["library"]
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.catalog_entries() == []
    finally:
        service.close()


def test_numilog_entitlement_helper_is_inert_without_authenticated_owner(tmp_path):
    service = _service(tmp_path)
    try:
        assert mirror_numilog_manifest_rights(
            service, [_row("not-connected", 0)], identity_id="default",
            credential_id="not-connected", account_id="unbound",
        ) == 0
        assert service.storage.entitlements(provider_id="numilog") == []
    finally:
        service.close()
