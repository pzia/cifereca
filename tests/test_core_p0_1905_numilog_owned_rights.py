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



def test_authenticated_numilog_inventory_persists_scoped_notices_without_rights(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        account, _session, calls = _connect(service, number=3)
        assert not hasattr(service, "_acquire_personal_library_item")
        first = service.mirror_personal_libraries()
        second = service.mirror_personal_libraries()
        assert first == second == {"numilog:numilog-private": 3}
        assert calls == ["library", "library"]
        assert service.storage.entitlements(provider_id="numilog") == []
        notices = service.storage.catalog_entries()
        assert len(notices) == 3
        assert len({row["record_id"] for row in notices}) == 3
        assert {row["identity_id"] for row in notices} == {"default"}
        assert all(row["local_state"] == "remote_catalog" for row in notices)
        assert service.library_entries(identity_id="alex") == []
        assert not list((tmp_path / "EBooks").rglob("*.epub"))
        assert not list((tmp_path / "EBooks").rglob("*.acsm"))
    finally:
        service.close()
    reopened = _service(tmp_path)
    try:
        assert reopened.storage.entitlements(provider_id="numilog") == []
        assert len(reopened.storage.catalog_entries()) == 3
        assert reopened.library_entries(identity_id="alex") == []
    finally:
        reopened.close()



def test_public_owned_flag_and_private_library_never_mint_pseudo_rights(tmp_path):
    service = _service(tmp_path)
    try:
        row = _row("vault-no-right", 1)
        assert service._mirror_remote_results([row], identity_id="default") == 1
        assert service.storage.entitlements(provider_id="numilog") == []
        assert len(service.storage.catalog_entries(state="remote_catalog")) == 1
        _account, _session, _calls = _connect(
            service, credential="vault-no-right", number=2,
        )
        service.mirror_personal_libraries()
        assert service.storage.entitlements(provider_id="numilog") == []
        assert len(service.storage.catalog_entries()) == 2
        service._mirror_remote_results([row], identity_id="default")
        service._mirror_remote_results([row], identity_id="alex")
        assert service.storage.entitlements(provider_id="numilog") == []
        assert not service.storage.catalog_entries(state="remote_owned")
        assert not [x for x in service.storage.catalog_entries()
                    if x["identity_id"] == "alex" and x["local_state"] == "remote_owned"]
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
            return [
                replace(_row(credential, 0), title="Titre commun"),
                replace(_row(credential, 1), title="Titre commun"),
            ]
        session_a.library = lambda: (calls_a.append("distinct") or two_variants("vault-a"))
        session_b.library = lambda: (calls_b.append("distinct") or two_variants("vault-b"))
        service.mirror_personal_libraries()
        notices = service.storage.catalog_entries()
        assert len(notices) == 4
        assert len({(r["identity_id"], r["record_id"]) for r in notices}) == 4
        assert {r["identity_id"] for r in notices} == {"alice", "bob"}
        assert len([r for r in notices if r["identity_id"] == "alice"]) == 2
        assert len([r for r in notices if r["identity_id"] == "bob"]) == 2
        assert all(r["title"] == "Titre commun" for r in notices)
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.library_entries(identity_id="carol") == []
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
        assert not hasattr(service, "_acquire_personal_library_item")
        service.mirror_personal_libraries()
        assert calls == ["library"]
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.catalog_entries(state="remote_owned") == []
    finally:
        service.close()



def test_empty_private_inventory_does_not_create_or_revoke_local_artifacts(tmp_path):
    service = _service(tmp_path)
    try:
        _account, session, calls = _connect(service, number=1)
        service.mirror_personal_libraries()
        assert len(service.storage.catalog_entries()) == 1
        assert service.storage.entitlements(provider_id="numilog") == []
        session.library = lambda: (calls.append("empty") or [])
        service.mirror_personal_libraries()
        assert service.storage.entitlements(provider_id="numilog") == []
        assert service.storage.acquisitions(provider_id="numilog") == []
        assert service.storage.placements() == []
        assert not list((tmp_path / "EBooks").rglob("*.epub"))
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
            title="Livre de test", author="Auteur",
            friendly_url="https://www.numilog.com/123/Test.ebook?auth=SECRET_SIGNATURE",
            thumbnail_url="https://storage.googleapis.com/covers/a.png?token=SECRET_SIGNATURE",
            metadata={"rights": "https://private.invalid/?auth=SECRET_SIGNATURE",
                      "isbn": "9780000000001"},
        )
        session.library = lambda: [record]
        summary = service.mirror_personal_libraries()
        assert summary["numilog:numilog-private"] == 1
        assert service.storage.entitlements(provider_id="numilog") == []
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



def test_numilog_digital_catalogue_covers_without_local_file_or_right(tmp_path):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        covers = [
            f"https://couverture.numilog.com/978207524202{i}_GRANDE.jpg"
            for i in range(3)
        ]
        session.library = lambda: (
            calls.append("library") or [
                replace(_row(account.credential_id, i), thumbnail_url=covers[i])
                for i in range(3)
            ]
        )
        assert service.mirror_personal_libraries() == {"numilog:numilog-private": 3}
        assert calls == ["library"]
        notices = service.storage.catalog_entries()
        assert len(notices) == 3
        assert {row["metadata"]["cover_url"] for row in notices} == set(covers)
        assert all(row["local_state"] == "remote_catalog" for row in notices)
        assert service.storage.entitlements(provider_id="numilog") == []
        assert not list((tmp_path / "EBooks").rglob("*.epub"))
        assert not list((tmp_path / "EBooks").rglob("*.acsm"))
    finally:
        service.close()

@pytest.mark.parametrize("untrusted_cover", [
    "https://couverture.numilog.com/9782075242028_GRANDE.jpg?auth=SECRET_SIGNATURE",
    "https://couverture.numilog.com/9782075242028_GRANDE.jpg#private",
    "https://user:secret@couverture.numilog.com/9782075242028_GRANDE.jpg",
    "https://couverture.numilog.com:8443/9782075242028_GRANDE.jpg",
    "http://couverture.numilog.com/9782075242028_GRANDE.jpg",
    "https://evil.example/9782075242028_GRANDE.jpg",
])

def test_numilog_untrusted_cover_never_persists_in_catalogue(
    tmp_path, untrusted_cover,
):
    service = _service(tmp_path)
    try:
        account, session, _calls = _connect(service, number=0)
        session.library = lambda: [
            replace(_row(account.credential_id, 0), thumbnail_url=untrusted_cover)
        ]
        assert service.mirror_personal_libraries() == {"numilog:numilog-private": 1}
        assert service.storage.entitlements(provider_id="numilog") == []
        catalogue = service.storage.catalog_entries()
        assert len(catalogue) == 1
        assert catalogue[0]["metadata"]["cover_url"] is None
        assert untrusted_cover not in json.dumps(catalogue)
    finally:
        service.close()

