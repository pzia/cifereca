from __future__ import annotations

import sqlite3

import tempfile
import zipfile
from pathlib import Path

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, Loan, SearchResult
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp: str) -> LibraryService:
    root = Path(tmp)
    cfg = Config(root / "config.json")
    cfg.set("library.directory", str(root / "EBooks"))
    cfg.set("identities", [
        {"id": "default", "label": "Hugues", "directory_name": "hugues", "preferences": {"reading_languages": ["fr"]}},
        {"id": "alex", "label": "Alex", "directory_name": "alex", "preferences": {"reading_languages": ["fr"]}},
    ])
    cfg.set("providers.accounts", [])
    return LibraryService(cfg, Storage(root / "db.sqlite3"), Diagnostics())


def _epub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", "<container/>")
        z.writestr("OEBPS/book.xhtml", "<html/>")


def test_search_results_are_progressively_mirrored():
    with tempfile.TemporaryDirectory() as td:
        service = _service(td)
        try:
            row = SearchResult(
                id="kobo:c:p1", record_id="p1", docbase="KOBO", medium="digital", title="Book", author="A",
                type="ebook", format="EPUB", publisher=None, published_at=None, description=None,
                thumbnail_url="https://example/cover.jpg", friendly_url="https://example/book", scenario="KOBO",
                source_provider="kobo", source_credential="c", owned=False, price="9.99", currency="EUR",
            )
            assert service._mirror_remote_results([row], identity_id="default") == 1
            entries = service.storage.catalog_entries(state="available_to_buy")
            assert len(entries) == 1
            assert entries[0]["item_id"] == "p1"
            assert entries[0]["metadata"]["cover_url"].endswith("cover.jpg")
        finally:
            service.close()


def test_recent_materialization_to_other_identity_keeps_source():
    with tempfile.TemporaryDirectory() as td:
        service = _service(td)
        try:
            src = Path(td) / "EBooks" / "hugues" / "kobo" / "Book.epub"
            _epub(src)
            service.catalog_scan()
            recent = service.recent_acquisitions(identity_id="default")
            assert recent and Path(recent[0]["path"]) == src
            result = service.materialize_to_identity(recent[0]["placement_id"], "alex")
            assert result["status"] == "complete"
            assert src.exists()
            assert Path(result["path"]).exists()
            # A folder named kobo is not proof of a Kobo purchase. Copying an
            # unattested local file to another identity uses the shared area.
            assert Path(result["path"]).parent == Path(td) / "EBooks" / "alex" / "shared"
        finally:
            service.close()


def test_provider_quota_is_retryable_and_does_not_permanently_block_active_loan(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        service = _service(td)
        try:
            service.storage.provider_connection_put({"identity_id": "default", "provider_id": "paris", "credential_id": "p", "label": "Paris"})
            service.accounts.append(Account(unique_id="", is_primary=True, provider_id="paris", credential_id="p", identity_id="default"))
            loan = Loan(id="l", provider="DILICOM", medium="digital", record_id="isbn", title="Quota",
                        source_provider="paris", source_credential="p", source_account_id="primary", identity_id="default",
                        ebook_url="https://example", due_at="2099-01-01T00:00:00Z")
            service._persist_active_loans("default", [loan])
            calls = {"n": 0}
            def fail(_loan):
                calls["n"] += 1
                raise RuntimeError("download digital document: Quota dépassé.")
            monkeypatch.setattr(service, "acquire_digital_loan", fail)
            first = service._acquire_active_loan_automatically(loan)
            second = service._acquire_active_loan_automatically(loan)
            assert first["status"] == "retryable_error"
            assert first["code"] == "provider_quota_exceeded"
            assert second["status"] == "retryable_error"
            assert calls["n"] == 2
            rows = service.storage.acquisitions(provider_id="paris")
            assert len(rows) == 1
            assert rows[0]["account_id"] == "primary"
            assert rows[0]["attention_code"] is None
        finally:
            service.close()


def test_schema_v8_merges_legacy_empty_and_primary_paris_loan_ledger_rows():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "db.sqlite3"
        storage = Storage(db_path)
        now = 1700000000
        db = sqlite3.connect(db_path)
        db.execute("UPDATE metadata SET value='7' WHERE key='schema_version'")
        db.execute("DELETE FROM acquisition_ledger")
        db.execute("DELETE FROM entitlements")
        # Same logical loan as it existed before/after the primary-account key fix.
        for account_id, suffix in (("", "legacy"), ("primary", "canonical")):
            ent_id = f"ent-{suffix}"
            acq_id = f"acq-{suffix}"
            db.execute(
                "INSERT INTO entitlements(entitlement_id,provider_id,credential_id,account_id,item_id,entitlement_type,state,loan_id,metadata_json,discovered_at,updated_at,identity_id) "
                "VALUES(?,?,?,?,?,'loan','active','loan-1','{}',?,?, 'default')",
                (ent_id, "paris", "paris-default", account_id, "9780000000001", now, now),
            )
            db.execute(
                "INSERT INTO acquisition_ledger(acquisition_id,entitlement_id,provider_id,credential_id,account_id,item_id,title,status,strategy,executor,attempt_count,error_message,attention_code,metadata_json,created_at,updated_at,identity_id) "
                "VALUES(?,?,?,?,?,?,?,'attention_required','loan_auto','provider',1,'Quota dépassé.','provider_quota_exceeded','{}',?,?, 'default')",
                (acq_id, ent_id, "paris", "paris-default", account_id, "9780000000001", "Livre", now, now),
            )
        db.commit(); db.close()

        migrated = Storage(db_path)
        assert migrated.schema_version() == 14
        entitlements = migrated.entitlements(provider_id="paris")
        assert len(entitlements) == 1
        assert entitlements[0]["account_id"] == "primary"
        rows = migrated.acquisitions(provider_id="paris")
        assert len(rows) == 1
        assert rows[0]["account_id"] == "primary"


def test_deep_scan_reports_misnamed_pdf_without_mutating_files_or_ledger():
    # Deep Sync is a scan, not authorization to relocate an ADEPT loan.
    with tempfile.TemporaryDirectory() as td:
        service = _service(td)
        try:
            wrong = Path(td) / "EBooks" / "hugues" / "paris" / "Book.epub"
            wrong.parent.mkdir(parents=True, exist_ok=True)
            payload = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
            wrong.write_bytes(payload)
            service.storage.acquisition_put({
                "provider_id": "paris", "credential_id": "p", "account_id": "primary", "identity_id": "default",
                "item_id": "book-pdf", "title": "Book", "status": "complete", "strategy": "loan_auto", "executor": "kobo-adept",
                "metadata": {"path": str(wrong), "post_processed": True},
            })
            old_placement = service.storage.placement_put({
                "artifact_id": "sha256:synthetic-historic-artifact",
                "path": str(wrong), "identity_id": "default",
                "provider_id": "paris", "source_item_id": "book-pdf",
                "state": "present", "metadata": {"historical_evidence": "keep"},
            })
            result = service.catalog_scan(repair_misnamed=True)
            assert result["invalid"] == 1
            assert wrong.read_bytes() == payload
            assert not wrong.with_suffix(".pdf").exists()
            ledger = service.storage.acquisitions(provider_id="paris")[0]
            assert ledger["metadata"] == {"path": str(wrong), "post_processed": True}
            placements = [row for row in service.storage.placements()
                          if row["path"] == str(wrong.resolve())]
            assert len(placements) == 1
            assert placements[0]["state"] == "invalid"
            assert placements[0]["metadata"]["invalid_reason"] == "extension_mismatch"
            assert placements[0]["placement_id"] == old_placement
            assert placements[0]["artifact_id"] == "sha256:synthetic-historic-artifact"
            assert placements[0]["identity_id"] == "default"
            assert placements[0]["provider_id"] == "paris"
            assert placements[0]["source_item_id"] == "book-pdf"
            assert placements[0]["metadata"]["historical_evidence"] == "keep"
        finally:
            service.close()


def test_deep_scan_reuses_hash_when_size_and_mtime_are_unchanged(monkeypatch):
    from cifereca.catalog import LocalCatalog

    with tempfile.TemporaryDirectory() as td:
        service = _service(td)
        try:
            book = Path(td) / "EBooks" / "hugues" / "local" / "Book.epub"
            _epub(book)
            service.catalog_scan()
            calls = {"n": 0}
            original = LocalCatalog.sha256
            def counted(path):
                calls["n"] += 1
                return original(path)
            monkeypatch.setattr(LocalCatalog, "sha256", staticmethod(counted))
            service.catalog_scan()
            assert calls["n"] == 0
        finally:
            service.close()


def test_recent_acquisitions_enriches_cover_from_active_loan():
    with tempfile.TemporaryDirectory() as td:
        service = _service(td)
        try:
            service.storage.provider_connection_put({"identity_id": "default", "provider_id": "paris", "credential_id": "p", "label": "Paris"})
            book = Path(td) / "EBooks" / "hugues" / "paris" / "Book.epub"
            _epub(book)
            service.catalog_index_path(book, title="Book", item_id="isbn-cover", identity_id="default", provider_id="paris")
            service.last_loans = [Loan(
                id="loan-cover", provider="DILICOM", medium="digital", record_id="isbn-cover", title="Book",
                source_provider="paris", source_credential="p", source_account_id="primary", identity_id="default",
                thumbnail_url="https://example.test/cover.jpg", due_at="2099-01-01T00:00:00Z",
            )]
            recent = service.recent_acquisitions(identity_id="default")
            assert recent[0]["thumbnail_url"] == "https://example.test/cover.jpg"
        finally:
            service.close()


def test_recent_acquisitions_reads_cover_sources_once_not_per_placement(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        service = _service(td)
        try:
            for index in range(3):
                book = Path(td) / "EBooks" / "hugues" / "paris" / f"Book {index}.epub"
                _epub(book)
                service.catalog_index_path(
                    book, title=f"Book {index}", item_id=f"isbn-{index}",
                    identity_id="default", provider_id="paris",
                )
            counts = {"backup": 0, "entitlements": 0}
            original_backup = service.storage.backup_list
            original_entitlements = service.storage.entitlements

            def counted_backup(*args, **kwargs):
                counts["backup"] += 1
                return original_backup(*args, **kwargs)

            def counted_entitlements(*args, **kwargs):
                counts["entitlements"] += 1
                return original_entitlements(*args, **kwargs)

            monkeypatch.setattr(service.storage, "backup_list", counted_backup)
            monkeypatch.setattr(service.storage, "entitlements", counted_entitlements)
            rows = service.recent_acquisitions(identity_id="default")
            assert len(rows) >= 3
            assert counts == {"backup": 0, "entitlements": 1}
        finally:
            service.close()



def test_recent_covers_ignore_foreign_or_unscoped_legacy_backup_even_with_matching_item(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        service = _service(td)
        try:
            book = Path(td) / "EBooks" / "hugues" / "manual" / "Kobo.pdf"
            book.parent.mkdir(parents=True, exist_ok=True)
            book.write_bytes(b"%PDF-1.7\\nSynthetic offline fixture\\n")
            indexed = service.catalog_index_path(
                book, title="Same edition", item_id="shared-edition",
                identity_id="default", provider_id="kobo",
            )
            assert indexed["status"] == "indexed"
            # v0.7 can associate the SAME edition with a foreign or absent
            # owner and still expose an apparently authoritative cover URL.
            with service.storage._lock, service.storage._connect() as db:
                db.execute(
                    "INSERT INTO backup_items(provider_id,credential_id,item_id,"
                    "title,status,updated_at,identity_id,account_id,cover_url) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    ("kobo", "foreign-card", "shared-edition", "Same edition",
                     "complete", 1, "alex", "other-account",
                     "https://example.test/foreign-cover.jpg"),
                )
            assert len(service.storage.backup_list("kobo")) == 1
            def forbidden(*args, **kwargs):
                raise AssertionError("recent placements must not read backup_items")
            monkeypatch.setattr(service.storage, "backup_list", forbidden)
            initial = service.recent_acquisitions(identity_id="default")
            selected = next(x for x in initial if x["path"] == str(book))
            assert selected["identity_id"] == "default"
            assert selected["item_id"] == "shared-edition"
            assert selected["thumbnail_url"] is None

            # A canonical entitlement of this profile remains a valid
            # descriptive cover source; no historical backup is consulted.
            service.storage.entitlement_put({
                "provider_id": "kobo", "credential_id": "owned-card",
                "account_id": "owned-account", "identity_id": "default",
                "item_id": "shared-edition", "entitlement_type": "owned",
                "state": "active",
                "metadata": {"cover_url": "https://example.test/owner-cover.jpg"},
            })
            again = service.recent_acquisitions(identity_id="default")
            chosen = next(x for x in again if x["path"] == str(book))
            assert chosen["thumbnail_url"] == "https://example.test/owner-cover.jpg"
        finally:
            service.close()


def test_library_entries_are_independent_from_legacy_device_tables(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        service = _service(td)
        try:
            book = Path(td) / "EBooks" / "hugues" / "paris" / "Book.epub"
            _epub(book)
            indexed = service.catalog_index_path(
                book, title="Book", item_id="isbn-device", identity_id="default", provider_id="paris",
            )
            artifact_id = indexed["artifact_id"]
            def forbidden(*_args, **_kwargs):
                raise AssertionError("generic Library projection must not inspect legacy device placement tables")
            monkeypatch.setattr(service.storage, "device_placement_get", forbidden)
            rows = service.library_entries(identity_id="default", scan=False)
            row = next(item for item in rows if item.get("artifact_id") == artifact_id)
            assert "device_statuses" not in row
        finally:
            service.close()


def test_materialize_to_identity_uses_targeted_catalog_registration(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        service = _service(td)
        try:
            src = Path(td) / "EBooks" / "hugues" / "paris" / "Targeted.epub"
            _epub(src)
            indexed = service.catalog_index_path(
                src, title="Targeted", item_id="targeted-1", identity_id="default", provider_id="paris",
            )
            source_entry = next(
                row for row in service.storage.catalog_entries()
                if row.get("artifact_id") == indexed["artifact_id"] and row.get("identity_id") == "default"
            )

            def forbidden_scan(*_args, **_kwargs):
                raise AssertionError("materialize_to_identity must not scan the whole library")

            monkeypatch.setattr(service, "catalog_scan", forbidden_scan)
            result = service.materialize_to_identity(source_entry["placement_id"], "alex")
            assert result["status"] == "complete"
            target = Path(result["path"])
            assert target.is_file()
            rows = [
                row for row in service.storage.catalog_entries()
                if row.get("identity_id") == "alex" and row.get("artifact_id") == indexed["artifact_id"]
            ]
            assert len(rows) == 1
            assert rows[0]["item_id"] == "targeted-1"
            assert rows[0]["metadata"]["path"] == str(target.resolve())
        finally:
            service.close()
