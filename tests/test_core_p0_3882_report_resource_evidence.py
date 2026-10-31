"""#3882: ZIP before/after proves resource staging without leaking origins."""
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

from cifereca.core.sync_report import audit_source_counts, write_sync_report
from cifereca.storage import Storage


ACCOUNT = SimpleNamespace(
    identity_id="SECRET_PROFILE", provider_id="numilog",
    credential_id="SECRET_CREDENTIAL", unique_id="SECRET_ACCOUNT",
)


def test_same_private_run_key_correlates_before_after_and_staging(tmp_path):
    storage = Storage(tmp_path / "library.sqlite")
    try:
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO source_references("
                "source_reference_id,identity_id,provider_id,credential_id,account_id,"
                "branch_id,edition_id,right_id,reference_kind,state,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("SECRET_SOURCE_REFERENCE", ACCOUNT.identity_id,
                 ACCOUNT.provider_id, ACCOUNT.credential_id, ACCOUNT.unique_id,
                 "purchases", "SECRET_EDITION", "right", "purchase", "active", 1, 1),
            )
            db.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,identity_id,provider_id,credential_id,account_id,"
                "item_id,status,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("SECRET_LEDGER_RECEIPT", ACCOUNT.identity_id, ACCOUNT.provider_id,
                 ACCOUNT.credential_id, ACCOUNT.unique_id, "SECRET_EDITION",
                 "canonical_receipt_staged", '{"path":"/private/book.epub"}', 1, 1),
            )
        secret = b"secret-not-persisted-in-report-32B!"
        before = audit_source_counts(storage, ACCOUNT, pseudonym_key=secret)
        after = audit_source_counts(storage, ACCOUNT, pseudonym_key=secret)
        rights = before["resource_evidence"]["rights"]
        receipts = before["resource_evidence"]["receipts"]
        assert len(rights) == len(receipts) == 1
        assert rights[0]["classification"] == "source_unlinked"
        assert receipts[0]["classification"] == "staged_unpublished"
        assert rights[0]["resource_ref"] == after["resource_evidence"]["rights"][0]["resource_ref"]
        assert receipts[0]["receipt_ref"] == after["resource_evidence"]["receipts"][0]["receipt_ref"]
        output = tmp_path / "report.zip"
        write_sync_report(
            output, account=ACCOUNT, run_ref="b" * 32,
            before=before, after=after, result={"status": "partial"},
            started_at="start", exit_code=2,
        )
        with zipfile.ZipFile(output) as z:
            assert json.loads(z.read("acquisition-audit-before.json"))["resource_evidence"]["receipts"][0]["classification"] == "staged_unpublished"
            assert json.loads(z.read("manifest.json"))["evidence_coverage"] == "canonical_sqlite_graph_without_filesystem"
            data = b"".join(z.read(name) for name in z.namelist())
            for secret_raw in (
                b"SECRET_PROFILE", b"SECRET_CREDENTIAL", b"SECRET_ACCOUNT",
                b"SECRET_SOURCE_REFERENCE", b"SECRET_LEDGER_RECEIPT",
                b"/private/book.epub", secret,
            ):
                assert secret_raw not in data
    finally:
        storage.close()
