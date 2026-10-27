"""#2847: private, read-only preview of references on stale SQLite paths.

Synthetic SQLite and EPUB bytes only. This is NOT a deletion/relink permit.
"""
from __future__ import annotations

import sqlite3

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_library_reconciliation import epub, filesystem, fixture_db


def test_missing_path_private_preview_counts_observed_references_without_writing(tmp_path):
    root = tmp_path / "reader"
    root.mkdir()
    old = root / "old-file.epub"  # absent in the fully accessible root
    existing = root / "other-copy.epub"
    epub(existing, title="Synthetic title")
    database = tmp_path / "synthetic.sqlite"
    fixture_db(database)
    artifact = "sha256:synthetic-physical"
    with sqlite3.connect(database) as db:
        db.executemany(
            "INSERT INTO placements(placement_id,artifact_id,path,identity_id,"
            "provider_id,source_item_id,state) VALUES(?,?,?,?,?,?,?)",
            [
                ("old", artifact, str(old), "reader", "synthetic-provider",
                 "edition-1", "missing"),
                ("copy", artifact, str(existing), "reader", "synthetic-provider",
                 "edition-1", "present"),
                ("foreign", artifact, str(root / "foreign.epub"), "other",
                 "synthetic-provider", "edition-1", "missing"),
            ],
        )
        db.execute(
            "INSERT INTO catalog_entries(catalog_entry_id,artifact_id,"
            "placement_id,identity_id,provider_id,item_id,metadata_json)"
            " VALUES(?,?,?,?,?,?,?)",
            ("notice", artifact, "old", "reader", "synthetic-provider",
             "edition-1", "{}"),
        )
        db.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "identity_id,local_path,status) VALUES(?,?,?,?,?,?)",
            ("synthetic-provider", "reader-1", "edition-1", "reader",
             str(old), "complete"),
        )
        db.execute(
            "INSERT INTO acquisition_ledger(acquisition_id,provider_id,"
            "credential_id,item_id,identity_id,status,artifact_id)"
            " VALUES(?,?,?,?,?,?,?)",
            ("receipt", "synthetic-provider", "reader-1", "edition-1",
             "reader", "complete", artifact),
        )
        db.execute(
            "INSERT INTO entitlements(entitlement_id,identity_id,"
            "provider_id,credential_id,item_id,state,entitlement_type)"
            " VALUES(?,?,?,?,?,?,?)",
            ("right", "reader", "synthetic-provider", "reader-1",
             "edition-1", "active", "owned"),
        )
    before = filesystem(tmp_path)
    audit = audit_library(database, {"reader": (root,)})
    session = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    try:
        row, = session.page(state="stored_path_missing").cases
        details = row.details
        assert "Prévisualisation des références enregistrées" in details
        assert "Notices directement liées au placement : 1" in details
        assert "Anciennes sauvegardes portant ce chemin : 1" in details
        assert "Autres emplacements du même artefact dans cette identité : 1" in details
        assert "Acquisitions référençant cet artefact dans cette identité : 1" in details
        assert "Droits enregistrés pour cette source et cette édition : 1" in details
        assert "aucune mutation effectuée" in details
        assert row.provider == "unattributed"
        assert filesystem(tmp_path) == before
    finally:
        session.close()


def test_empty_missing_reference_preview_never_claims_cleanup_is_safe(tmp_path):
    root = tmp_path / "reader"
    root.mkdir()
    database = tmp_path / "synthetic.sqlite"
    fixture_db(database)
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO placements(placement_id,artifact_id,path,identity_id,"
            "provider_id,source_item_id,state) VALUES(?,?,?,?,?,?,?)",
            ("orphan", "sha256:unreferenced", str(root / "missing.epub"),
             "reader", "synthetic-provider", "edition-2", "missing"),
        )
    before = filesystem(tmp_path)
    audit = audit_library(database, {"reader": (root,)})
    session = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    row, = session.page(state="stored_path_missing_historical").cases
    assert "Notices directement liées au placement : 0" in row.details
    assert "Droits enregistrés pour cette source et cette édition : 0" in row.details
    assert "Même zéro référence visible ne prouve pas" in row.details
    assert filesystem(tmp_path) == before
    session.close()
