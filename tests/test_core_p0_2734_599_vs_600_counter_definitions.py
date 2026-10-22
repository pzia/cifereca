"""P0 #2734: observed disk files and present SQLite artifacts are not one count.

600 actual synthetic files (599 EPUB + one signature-checked PDF) can coexist
with 599 distinct persisted present artifacts. The numbers alone do not
identify a missing book, a bad PDF, or a lost entitlement.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_library_reconciliation import epub, filesystem, fixture_db


def _fixture(tmp_path):
    directory = tmp_path / "PRIVATE-library"
    directory.mkdir()
    database = tmp_path / "synthetic.sqlite"
    fixture_db(database)
    paths = []
    for i in range(599):
        book = directory / f"PRIVATE-SYNTHETIC-{i:03d}.epub"
        epub(book, title=f"Synthetic {i}")
        paths.append(book)
    pdf = directory / "PRIVATE-PDF.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj <<>> endobj\n%%EOF\n")
    with sqlite3.connect(database) as db:
        for i, book in enumerate(paths):
            raw = book.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            artifact = f"sha256:{sha}"
            # All 599 EPUBs remain distinct because their test titles differ.
            db.execute(
                "INSERT INTO artifacts(artifact_id,sha256,size,media_format) "
                "VALUES(?,?,?,?)",
                (artifact, sha, len(raw), "epub"),
            )
            db.execute(
                "INSERT INTO placements(placement_id,artifact_id,path,"
                "identity_id,provider_id,state) VALUES(?,?,?,?,?,?)",
                (f"PRIVATE-p-{i}", artifact, str(book), "reader",
                 "local", "present"),
            )
        db.execute(
            "INSERT INTO placements(placement_id,artifact_id,path,"
            "identity_id,provider_id,state) VALUES(?,?,?,?,?,?)",
            ("PRIVATE-foreign", "sha256:PRIVATE-foreign", str(pdf),
             "foreign-reader", "paris", "present"),
        )
    return database, directory, pdf


def test_profile_artifacts_599_vs_scan_files_600_are_distinct_observations(tmp_path):
    database, root, pdf = _fixture(tmp_path)
    before = filesystem(tmp_path)
    audit = audit_library(database, {"reader": (root,)})
    session = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    try:
        metrics = session.overview()
        assert metrics["observed_epub_files"] == 599
        assert metrics["observed_pdf_files"] == 1
        assert metrics["files"] == 600
        assert metrics["sqlite_present_placements"] == 599
        assert metrics["sqlite_present_artifact_books"] == 599
        assert audit.inventory.files_started == 600
        assert pdf.is_file()
        # No filesystem or SQLite writes are caused by the inspection.
        assert filesystem(tmp_path) == before
        encoded = json.dumps(audit.redacted())
        assert "PRIVATE-" not in encoded
        assert "sha256:" not in encoded
        assert "599" in json.dumps(metrics)
    finally:
        session.close()
    assert session.overview() == {}


def test_unknown_sqlite_coverage_never_becomes_zero_books(tmp_path):
    database, root, _pdf = _fixture(tmp_path)
    audit = audit_library(database, {"reader": (root,)})
    audit.snapshot.reasons.add("snapshot_row_budget")
    session = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    try:
        metrics = session.overview()
        assert metrics["observed_epub_files"] == 599
        assert metrics["observed_pdf_files"] == 1
        assert metrics["sqlite_present_artifact_books"] is None
        assert metrics["sqlite_present_placements"] is None
        assert "PRIVATE-" not in json.dumps(audit.redacted())
    finally:
        session.close()
