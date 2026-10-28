from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from pathlib import Path

from cifereca.acquisition_engine import (
    AcquisitionCandidate,
    AcquisitionLedger,
    AcquisitionOffer,
    AcquisitionPlanner,
    AcquisitionPolicy,
    Entitlement,
    SourceIdentity,
)
from cifereca.catalog import LocalCatalog
from cifereca.storage import Storage


def _candidate(name: str, handling: str, fmt: str = "epub") -> AcquisitionCandidate:
    return AcquisitionCandidate(
        candidate_id=name,
        kind="acsm" if handling == "adobe_adept" else "http",
        handling=handling,  # type: ignore[arg-type]
        media_format="acsm" if handling == "adobe_adept" else fmt,
        source_format=fmt,
        url="https://example.test/book",
    )


def test_generic_policy_preserves_project_priority_acsm_plain_kdrm():
    entitlement = Entitlement(SourceIdentity("kobo", "vault-a"), "book-a")
    offer = AcquisitionOffer(
        entitlement=entitlement,
        candidates=(
            _candidate("kdrm", "kobo_kdrm"),
            _candidate("plain", "http_plain"),
            _candidate("acsm", "adobe_adept"),
        ),
    )
    plan = AcquisitionPlanner(AcquisitionPolicy()).plan(offer, preferred_format="epub")
    assert plan is not None
    assert plan.candidate.candidate_id == "acsm"
    assert plan.strategy == "adobe_adept"


def test_retry_replaces_persisted_plan_without_leaving_acquiring(tmp_path: Path):
    storage = Storage(tmp_path / "ledger.sqlite3")
    ledger = AcquisitionLedger(storage)
    entitlement = Entitlement(SourceIdentity("kobo", "vault-a"), "book-a")
    initial = AcquisitionOffer(entitlement, (_candidate("first", "http_plain"),))
    first_plan = AcquisitionPlanner().plan(initial, preferred_format="epub")
    assert first_plan is not None

    acquisition_id = ledger.discover(initial, title="Book A")
    ledger.set_plan(acquisition_id, first_plan)
    ledger.begin(acquisition_id)

    refreshed = AcquisitionOffer(entitlement, (_candidate("second", "kobo_kdrm"),))
    refreshed_plan = AcquisitionPlanner().plan(refreshed, preferred_format="epub")
    assert refreshed_plan is not None
    ledger.begin(
        acquisition_id,
        plan=refreshed_plan,
        metadata={"acquisition_mode": "delegated-kobo"},
    )

    row = storage.acquisition_get(acquisition_id)
    assert row is not None
    assert row["status"] == "acquiring"
    assert row["attempt_count"] == 2
    assert row["strategy"] == "kobo_kdrm"
    assert row["executor"] == "provider"
    assert row["candidate_id"] == "second"
    assert row["metadata"]["candidate_shape"] == refreshed_plan.candidate.safe_shape()
    assert row["metadata"]["requested_format"] == "epub"
    assert row["metadata"]["acquisition_mode"] == "delegated-kobo"


def test_schema_v3_mirrors_legacy_backup_into_generic_ledger():
    with tempfile.TemporaryDirectory() as td:
        storage = Storage(Path(td) / "db.sqlite3")
        assert storage.schema_version() == 14
        storage.backup_put({
            "provider_id": "kobo",
            "credential_id": "identity-a",
            "account_id": "account-a",
            "item_id": "book-a",
            "title": "Book A",
            "status": "pending_acquisition",
            "entitlement_type": "owned",
            "acquisition_strategy": "acsm-descriptor",
            "executor": "kobo-adept",
            "attempt_count": 2,
        })
        entitlements = storage.entitlements(provider_id="kobo")
        ledger = storage.acquisitions(provider_id="kobo")
        assert len(entitlements) == 1
        assert entitlements[0]["account_id"] == "account-a"
        assert entitlements[0]["item_id"] == "book-a"
        assert len(ledger) == 1
        assert ledger[0]["status"] == "pending"
        assert ledger[0]["strategy"] == "acsm-descriptor"
        assert ledger[0]["executor"] == "kobo-adept"
        assert ledger[0]["attempt_count"] == 2


def test_v2_database_migrates_forward_without_losing_backup_rows():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "legacy.sqlite3"
        db = sqlite3.connect(db_path)
        db.executescript("""
        CREATE TABLE cache(namespace TEXT NOT NULL,cache_key TEXT NOT NULL,payload TEXT NOT NULL,stored_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,PRIMARY KEY(namespace,cache_key));
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT INTO metadata(key,value) VALUES('schema_version','2');
        CREATE TABLE backup_items(
          provider_id TEXT NOT NULL, credential_id TEXT NOT NULL, item_id TEXT NOT NULL,
          revision_id TEXT, title TEXT NOT NULL, author TEXT, format TEXT, local_path TEXT,
          status TEXT NOT NULL, size INTEGER, sha256 TEXT, updated_at INTEGER NOT NULL,
          error TEXT, job_id TEXT, account_id TEXT, entitlement_type TEXT NOT NULL DEFAULT 'owned',
          loan_id TEXT, expires_at TEXT, acquisition_strategy TEXT, executor TEXT, adept_profile TEXT,
          attempt_count INTEGER NOT NULL DEFAULT 0, last_attempt_at INTEGER, attention_code TEXT,
          PRIMARY KEY(provider_id,credential_id,item_id)
        );
        INSERT INTO backup_items(provider_id,credential_id,item_id,title,status,updated_at,entitlement_type,attempt_count)
        VALUES('kobo','vault','book','Legacy Book','complete',1700000000,'owned',1);
        """)
        db.commit(); db.close()
        storage = Storage(db_path)
        assert storage.schema_version() == 14
        assert storage.backup_get("kobo", "vault", "book")["title"] == "Legacy Book"
        assert storage.acquisitions(provider_id="kobo")[0]["status"] == "complete"
        assert storage.entitlements(provider_id="kobo")[0]["item_id"] == "book"


def _write_epub(path: Path, marker: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("OEBPS/content.xhtml", marker)


def test_local_catalog_deduplicates_artifact_but_keeps_two_placements():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "EBooks"
        first = root / "alice" / "kobo" / "Book.epub"
        second = root / "bob" / "shared" / "Book.epub"
        _write_epub(first, b"same")
        second.parent.mkdir(parents=True, exist_ok=True)
        second.write_bytes(first.read_bytes())
        storage = Storage(Path(td) / "db.sqlite3")
        storage.identity_put({"identity_id":"alice","label":"Alice","directory_name":"alice"})
        storage.identity_put({"identity_id":"bob","label":"Bob","directory_name":"bob"})
        result = LocalCatalog(storage, root).scan()
        assert result.cataloged == 2
        assert result.artifacts_new == 1
        assert len(storage.placements(state="present")) == 2
        entries = storage.catalog_entries(state="local")
        assert len(entries) == 2
        assert len({row["artifact_id"] for row in entries}) == 1
        assert {row["identity_id"] for row in entries} == {"alice", "bob"}


def test_local_catalog_marks_missing_without_deleting_history():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "EBooks"
        book = root / "alice" / "kobo" / "Gone.epub"
        _write_epub(book)
        storage = Storage(Path(td) / "db.sqlite3")
        catalog = LocalCatalog(storage, root)
        catalog.scan()
        book.unlink()
        result = catalog.scan()
        assert result.missing_marked == 1
        placements = storage.placements(state="missing")
        assert len(placements) == 1
        assert len(storage.catalog_entries(state="missing")) == 1
        # Artifact remains immutable/history-preserving even when no placement exists.
        assert storage.artifact_get(placements[0]["artifact_id"]) is not None


def test_recover_interrupted_generic_acquisition_is_retryable():
    with tempfile.TemporaryDirectory() as td:
        storage = Storage(Path(td) / "db.sqlite3")
        acquisition_id = storage.acquisition_put({
            "provider_id": "test", "credential_id": "c", "item_id": "i", "status": "acquiring"
        })
        assert storage.acquisition_recover_interrupted() == 1
        row = storage.acquisition_get(acquisition_id)
        assert row is not None
        assert row["status"] == "retryable_error"
        assert row["error_code"] == "interrupted"


def test_v3_migration_rolls_back_atomically_when_data_projection_fails():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "legacy-broken.sqlite3"
        db = sqlite3.connect(db_path)
        db.executescript("""
        CREATE TABLE cache(namespace TEXT NOT NULL,cache_key TEXT NOT NULL,payload TEXT NOT NULL,stored_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,PRIMARY KEY(namespace,cache_key));
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT INTO metadata(key,value) VALUES('schema_version','2');
        CREATE TABLE backup_items(
          provider_id TEXT NOT NULL, credential_id TEXT NOT NULL, item_id TEXT NOT NULL,
          revision_id TEXT, title TEXT NOT NULL, author TEXT, format TEXT, local_path TEXT,
          status TEXT NOT NULL, size INTEGER, sha256 TEXT, updated_at INTEGER NOT NULL,
          error TEXT, job_id TEXT, account_id TEXT, entitlement_type TEXT NOT NULL DEFAULT 'owned',
          loan_id TEXT, expires_at TEXT, acquisition_strategy TEXT, executor TEXT, adept_profile TEXT,
          attempt_count INTEGER NOT NULL DEFAULT 0, last_attempt_at INTEGER, attention_code TEXT,
          PRIMARY KEY(provider_id,credential_id,item_id)
        );
        INSERT INTO backup_items(provider_id,credential_id,item_id,title,status,updated_at,entitlement_type)
        VALUES('kobo','vault','book','Legacy Book','complete',1700000000,'owned');
        """)
        db.commit(); db.close()

        class BrokenStorage(Storage):
            @classmethod
            def _mirror_backup_row(cls, db, row, **kwargs):
                raise RuntimeError("projection failed")

        try:
            BrokenStorage(db_path)
            assert False, "migration should fail"
        except RuntimeError as exc:
            assert "projection failed" in str(exc)

        db = sqlite3.connect(db_path)
        version = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.close()
        assert version == "2"
        assert "entitlements" not in tables
        assert "acquisition_ledger" not in tables
