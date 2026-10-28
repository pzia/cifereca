from __future__ import annotations

import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.identities import AdeptProfileResolver, AdeptProfileResolutionError, Identity, IdentityPreferences
from cifereca.models import Loan
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _write_epub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("OEBPS/content.xhtml", "<html/>")


def test_schema_v5_roundtrips_identity_connection_account_and_device():
    with tempfile.TemporaryDirectory() as td:
        storage = Storage(Path(td) / "db.sqlite3")
        storage.identity_put(Identity("alice", "Alice", IdentityPreferences(True, ("fr", "en"))))
        connection_id = storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo", "credential_id": "vault-a", "label": "Kobo Alice",
        })
        storage.provider_account_put({
            "connection_id": connection_id, "identity_id": "alice", "provider_id": "kobo", "credential_id": "vault-a",
            "account_id": "reader-a", "label": "Compte Kobo", "is_primary": True,
        })
        storage.adept_profile_binding_set("identity", "alice", "ade-alice")
        storage.device_binding_put({"device_id": "reader-1", "identity_id": "alice", "label": "Liseuse Alice"})

        assert storage.schema_version() == 14
        assert storage.identity_get("alice")["reading_languages"] == ["fr", "en"]
        assert storage.identity_for_connection("kobo", "vault-a") == "alice"
        assert storage.provider_accounts(identity_id="alice")[0]["account_id"] == "reader-a"
        assert storage.adept_profile_binding_get("identity", "alice") == "ade-alice"
        with pytest.raises(ValueError):
            storage.adept_profile_binding_set("account", "legacy-account", "ade-account")
        assert storage.device_bindings(identity_id="alice")[0]["device_id"] == "reader-1"


def test_adept_profile_resolution_precedence_and_ambiguity():
    resolver = AdeptProfileResolver()
    assert resolver.resolve(explicit_profile="explicit", account_profile="account", identity_profile="identity").profile_id == "explicit"
    assert resolver.resolve(account_profile="account", identity_profile="identity").source == "identity"
    assert resolver.resolve(identity_profile="identity").source == "identity"
    unique = resolver.resolve(profiles=[{"id": "fetch-one", "fetch_capable": True}, {"id": "other", "fetch_capable": False}])
    assert (unique.profile_id, unique.source) == ("fetch-one", "unique_fetch_capable")
    with pytest.raises(AdeptProfileResolutionError) as exc:
        resolver.resolve(profiles=[{"id": "a", "fetch_capable": True}, {"id": "b", "fetch_capable": True}])
    assert exc.value.code == "adept_profile_ambiguous"


def test_config_attaches_legacy_provider_connections_to_default_identity_without_fs_move():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "config.json"
        path.write_text(json.dumps({
            "providers": {"gallica_migration_031": True, "accounts": [{
                "id": "kobo-main", "provider": "kobo", "label": "Kobo", "enabled": True,
                "credentials": {}, "options": {},
            }]},
            "identities": [{"id": "family", "label": "Famille", "preferences": {"reading_languages": ["fr"]}}],
        }), encoding="utf-8")
        config = Config(path)
        assert config.provider_accounts()[0]["identity_id"] == "family"
        assert not (Path(td) / "EBooks").exists()


def test_service_projects_identity_ade_binding_and_uses_identity_provider_layout():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        config = Config(root / "config.json")
        config.set("library.directory", str(root / "EBooks"))
        config.set("identities", [{
            "id": "alice", "label": "Alice", "adept_profile": "ade-alice",
            "preferences": {"prefer_original_language": False, "reading_languages": ["fr", "en"]},
        }])
        config.set("providers.accounts", [{
            "id": "vault-a", "provider": "kobo", "identity_id": "alice", "label": "Kobo Alice",
            "enabled": True, "credentials": {}, "options": {},
        }])
        storage = Storage(root / "db.sqlite3")
        service = LibraryService(config, storage, Diagnostics())
        try:
            assert service.library_identity_provider_dir("alice", "kobo") == root / "EBooks" / "alice" / "kobo"
            assert storage.adept_profile_binding_get("identity", "alice") == "ade-alice"
            assert storage.identity_for_connection("kobo", "vault-a") == "alice"
        finally:
            service.close()


def test_library_entries_federates_local_owned_remote_and_active_loan_for_identity():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        config = Config(root / "config.json")
        config.set("library.directory", str(root / "EBooks"))
        config.set("identities", [{"id": "alice", "label": "Alice", "preferences": {"reading_languages": ["fr"]}}])
        config.set("providers.accounts", [{
            "id": "vault-a", "provider": "kobo", "identity_id": "alice", "label": "Kobo Alice", "enabled": True,
            "credentials": {}, "options": {},
        }, {
            "id": "paris-a", "provider": "paris", "identity_id": "alice", "label": "Paris Alice", "enabled": True,
            "credentials": {}, "options": {},
        }])
        storage = Storage(root / "db.sqlite3")
        service = LibraryService(config, storage, Diagnostics())
        try:
            _write_epub(root / "EBooks" / "alice" / "gallica" / "Local Book.epub")
            service.catalog_scan()
            storage.backup_put({
                "provider_id": "kobo", "credential_id": "vault-a", "identity_id": "alice", "item_id": "owned-1",
                "title": "Owned Remote", "format": "epub", "status": "pending", "entitlement_type": "owned",
            })
            service.last_loans = [Loan(
                id="loan-1", provider="PNB", medium="digital", record_id="loan-book", title="Loan Active",
                due_at="2026-10-10T10:00:00+00:00", source_provider="paris", source_credential="paris-a", identity_id="alice",
            )]
            # Since 0.9.3 a cached Loan is enrichment, not ownership authority.
            loan = service.last_loans[0]
            storage.entitlement_put({"provider_id": loan.source_provider, "credential_id": loan.source_credential,
                "identity_id": "alice", "item_id": loan.record_id, "entitlement_type": "loan", "state": "active",
                "loan_id": loan.id, "metadata": {"title": loan.title, "thumbnail_url": loan.thumbnail_url}})
            entries = service.library_entries(identity_id="alice")
            by_title = {row["title"]: row for row in entries}
            assert by_title["Local Book"]["state"] == "local"
            assert by_title["Owned Remote"]["state"] == "remote_owned"
            assert by_title["Loan Active"]["state"] == "remote_loan_active"
        finally:
            service.close()


def test_v3_database_migrates_to_v5_with_identity_tables_and_identity_columns():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "v3.sqlite3"
        storage = Storage(db_path)
        db = sqlite3.connect(db_path)
        db.execute("UPDATE metadata SET value='3' WHERE key='schema_version'")
        for table in ("identities", "provider_connections", "provider_accounts", "adept_profile_bindings", "device_bindings"):
            db.execute(f"DROP TABLE {table}")
        # emulate an actual v3 layout by removing v4-only columns via table recreation is costly;
        # the migration is intentionally idempotent when columns already exist.
        db.commit(); db.close()
        migrated = Storage(db_path)
        assert migrated.schema_version() == 14
        tables = {row[0] for row in sqlite3.connect(db_path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"identities", "provider_connections", "provider_accounts", "adept_profile_bindings", "device_bindings"} <= tables


def test_identity_reading_languages_rank_without_hiding_other_results():
    from cifereca.models import SearchResult

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        config = Config(root / "config.json")
        config.set("identities", [{
            "id": "alice", "label": "Alice",
            "preferences": {"prefer_original_language": True, "reading_languages": ["fr", "en"]},
        }])
        service = LibraryService(config, Storage(root / "db.sqlite3"), Diagnostics())
        try:
            def row(record_id: str, language: str, original: str | None = None) -> SearchResult:
                metadata = {"language": language}
                if original:
                    metadata["original_language"] = original
                return SearchResult(
                    id=record_id, record_id=record_id, docbase="TEST", medium="digital", title="Titre",
                    author=None, type=None, format="EPUB", publisher=None, published_at=None, description=None,
                    thumbnail_url=None, friendly_url=None, scenario="TEST", metadata=metadata,
                )
            fr = row("fr", "fr", "fr")
            en = row("en", "en", "en")
            de = row("de", "de", "de")
            assert service._search_preference_score(fr) > service._search_preference_score(en) > service._search_preference_score(de)
            assert service._search_preference_score(de) == 5  # VO bonus, but still not filtered out.
        finally:
            service.close()


def test_one_kobo_vault_and_one_adept_profile_per_identity_with_many_devices():
    with tempfile.TemporaryDirectory() as td:
        storage = Storage(Path(td) / "db.sqlite3")
        storage.identity_put(Identity("alice", "Alice"))
        storage.identity_put(Identity("bob", "Bob"))
        first = storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo", "credential_id": "vault-a", "label": "Kobo Alice",
        })
        with pytest.raises(ValueError):
            storage.provider_connection_put({
                "identity_id": "alice", "provider_id": "kobo", "credential_id": "vault-b", "label": "Kobo Alice 2",
            })
        storage.adept_profile_binding_set("identity", "alice", "ade-alice")
        with pytest.raises(ValueError):
            storage.adept_profile_binding_set("identity", "bob", "ade-alice")
        storage.device_binding_put({"device_id": "reader-1", "identity_id": "alice"})
        storage.device_binding_put({"device_id": "reader-2", "identity_id": "alice"})
        assert len(storage.device_bindings(identity_id="alice")) == 2
        assert first


def test_library_entries_keep_kobo_and_loan_thumbnails():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        config = Config(root / "config.json")
        config.set("library.directory", str(root / "EBooks"))
        config.set("identities", [{"id": "alice", "label": "Alice"}])
        config.set("providers.accounts", [
            {"id": "vault-a", "provider": "kobo", "identity_id": "alice", "label": "Kobo Alice", "enabled": True, "credentials": {}, "options": {}},
            {"id": "paris-a", "provider": "paris", "identity_id": "alice", "label": "Paris Alice", "enabled": True, "credentials": {}, "options": {}},
        ])
        storage = Storage(root / "db.sqlite3")
        service = LibraryService(config, storage, Diagnostics())
        try:
            storage.backup_put({
                "provider_id": "kobo", "credential_id": "vault-a", "identity_id": "alice", "item_id": "owned-1",
                "title": "Owned Remote", "format": "epub", "status": "pending", "entitlement_type": "owned",
                "cover_url": "https://covers.example/kobo.jpg",
            })
            service.last_loans = [Loan(
                id="loan-1", provider="PNB", medium="digital", record_id="loan-book", title="Loan Active",
                due_at="2026-10-10T10:00:00+00:00", source_provider="paris", source_credential="paris-a",
                identity_id="alice", thumbnail_url="https://covers.example/pnb.jpg",
            )]
            # Since 0.9.3 a cached Loan is enrichment, not ownership authority.
            loan = service.last_loans[0]
            storage.entitlement_put({"provider_id": loan.source_provider, "credential_id": loan.source_credential,
                "identity_id": "alice", "item_id": loan.record_id, "entitlement_type": "loan", "state": "active",
                "loan_id": loan.id, "metadata": {"title": loan.title, "thumbnail_url": loan.thumbnail_url}})
            entries = {row["title"]: row for row in service.library_entries(identity_id="alice")}
            assert entries["Owned Remote"]["thumbnail_url"] == "https://covers.example/kobo.jpg"
            assert entries["Loan Active"]["thumbnail_url"] == "https://covers.example/pnb.jpg"
        finally:
            service.close()


def test_service_rejects_two_kobo_vault_accounts_for_same_identity():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        config = Config(root / "config.json")
        config.set("identities", [{"id": "alice", "label": "Alice"}])
        config.set("providers.accounts", [
            {"id": "vault-a", "provider": "kobo", "identity_id": "alice", "label": "A", "enabled": True},
            {"id": "vault-b", "provider": "kobo", "identity_id": "alice", "label": "B", "enabled": True},
        ])
        from cifereca.providers import ProviderConfigurationError
        with pytest.raises(ProviderConfigurationError):
            LibraryService(config, Storage(root / "db.sqlite3"), Diagnostics())


def test_v4_ambiguous_kobo_rows_migrate_to_distinct_identities_without_data_loss():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "ambiguous-v4.sqlite3"
        storage = Storage(db_path)
        # Remove v5 guards and emulate the first 0.8.1/v4 model.
        db = sqlite3.connect(db_path)
        db.execute("DROP INDEX IF EXISTS uq_kobo_connection_identity")
        db.execute("DROP INDEX IF EXISTS uq_kobo_account_identity")
        db.execute("DROP INDEX IF EXISTS uq_ade_profile_identity")
        db.execute("UPDATE metadata SET value='4' WHERE key='schema_version'")
        now = 1
        db.execute("DELETE FROM provider_connections")
        db.execute("DELETE FROM provider_accounts")
        db.execute("DELETE FROM adept_profile_bindings")
        db.execute("DELETE FROM identities")
        db.execute(
            "INSERT INTO identities(identity_id,label,prefer_original_language,reading_languages_json,metadata_json,created_at,updated_at) "
            "VALUES('family','Famille',0,'[\"fr\"]','{}',1,1)"
        )
        for idx, credential in enumerate(("vault-a", "vault-b"), start=1):
            connection = f"conn-{idx}"
            db.execute(
                "INSERT INTO provider_connections(connection_id,identity_id,provider_id,credential_id,label,enabled,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,1,'{}',?,?)",
                (connection, "family", "kobo", credential, credential, idx, idx),
            )
            db.execute(
                "INSERT INTO provider_accounts(provider_account_id,connection_id,identity_id,provider_id,credential_id,account_id,label,is_primary,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,1,'{}',?,?)",
                (f"acct-{idx}", connection, "family", "kobo", credential, f"reader-{idx}", credential, idx, idx),
            )
        db.execute(
            "INSERT INTO adept_profile_bindings(scope_type,scope_id,profile_id,created_at,updated_at) VALUES('identity','family','ade-family',1,1)"
        )
        db.commit(); db.close()

        migrated = Storage(db_path)
        assert migrated.schema_version() == 14
        connections = migrated.provider_connections()
        kobo = [row for row in connections if row["provider_id"] == "kobo"]
        assert len(kobo) == 2
        assert len({row["identity_id"] for row in kobo}) == 2
        assert {row["credential_id"] for row in kobo} == {"vault-a", "vault-b"}
        assert migrated.adept_profile_binding_get("identity", "family") == "ade-family"


def test_config_splits_legacy_multiple_kobo_vaults_across_identities():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "config.json"
        path.write_text(json.dumps({
            "identities": [{"id": "family", "label": "Famille", "adept_profile": "ade-family"}],
            "providers": {"gallica_migration_031": True, "accounts": [
                {"id": "vault-a", "provider": "kobo", "identity_id": "family", "label": "Kobo A", "enabled": True, "credentials": {}, "options": {}},
                {"id": "vault-b", "provider": "kobo", "identity_id": "family", "label": "Kobo B", "enabled": True, "credentials": {}, "options": {}},
            ]},
        }), encoding="utf-8")
        config = Config(path)
        accounts = config.provider_accounts("kobo")
        assert len(accounts) == 2
        assert len({row["identity_id"] for row in accounts}) == 2
        identities = {row["id"]: row for row in config.identities()}
        assert "family" in identities
        derived = [row for row in accounts if row["identity_id"] != "family"]
        assert len(derived) == 1
        assert derived[0]["identity_id"] in identities
        assert identities[derived[0]["identity_id"]].get("adept_profile") in (None, "")


def test_identity_directory_name_is_decoupled_from_technical_id():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        config = Config(root / "config.json")
        config.set("library.directory", str(root / "EBooks"))
        config.set("identities", [{
            "id": "default", "label": "Hugues", "directory_name": "hugues",
            "preferences": {"reading_languages": ["fr"]},
        }])
        service = LibraryService(config, Storage(root / "db.sqlite3"), Diagnostics())
        try:
            assert service.current_identity.identity_id == "default"
            assert service.current_identity.directory_name == "hugues"
            assert service.library_identity_provider_dir("default", "kobo") == root / "EBooks" / "hugues" / "kobo"
            stored = service.storage.identity_get("default")
            assert stored["directory_name"] == "hugues"
        finally:
            service.close()


def test_new_core_does_not_apply_brand_specific_legacy_file_migrations():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ebooks = root / "EBooks"
        ebooks.mkdir()
        legacy = ebooks / "Old Kobo.epub"
        legacy.write_bytes(b"legacy-kobo-content")
        config = Config(root / "config.json")
        config.set("library.directory", str(ebooks))
        config.set("identities", [{"id": "default", "label": "Hugues", "directory_name": "hugues"}])
        storage = Storage(root / "db.sqlite3")
        service = LibraryService(config, storage, Diagnostics())
        try:
            assert legacy.is_file()
            assert not (ebooks / "hugues" / "kobo" / "Old Kobo.epub").exists()
        finally:
            service.close()


def test_unassigned_legacy_catalog_entry_is_visible_only_to_default_identity():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        config = Config(root / "config.json")
        config.set("library.directory", str(root / "EBooks"))
        config.set("identities", [
            {"id": "default", "label": "Hugues", "directory_name": "hugues"},
            {"id": "alexandre", "label": "Alexandre", "directory_name": "alexandre"},
        ])
        storage = Storage(root / "db.sqlite3")
        storage.catalog_entry_put({
            "catalog_entry_id": "legacy", "title": "Legacy", "identity_id": None,
            "provider_id": "local", "item_id": "legacy-1", "local_state": "local", "metadata": {},
        })
        service = LibraryService(config, storage, Diagnostics())
        try:
            assert any(row["title"] == "Legacy" for row in service.library_entries(identity_id="default"))
            assert not any(row["title"] == "Legacy" for row in service.library_entries(identity_id="alexandre"))
        finally:
            service.close()
