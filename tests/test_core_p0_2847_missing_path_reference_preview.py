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
        assert "Emplacements candidats déjà observés comme fichiers réguliers" in details
        assert str(existing) in details
        assert "aucune attestation de compte, licence ni octets" in details
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


def test_same_hash_different_provider_or_edition_is_not_a_relink_candidate(tmp_path):
    root = tmp_path / "reader"
    root.mkdir()
    old = root / "missing.epub"
    other_provider = root / "another-provider.epub"
    other_edition = root / "another-edition.epub"
    phantom = root / "sqlite-says-present.epub"
    epub(other_provider, title="Same title")
    epub(other_edition, title="Same title")
    database = tmp_path / "synthetic.sqlite"
    fixture_db(database)
    artifact = "sha256:identical-bytes-not-ownership"
    with sqlite3.connect(database) as db:
        db.executemany(
            "INSERT INTO placements(placement_id,artifact_id,path,identity_id,"
            "provider_id,source_item_id,state) VALUES(?,?,?,?,?,?,?)",
            [
                ("old", artifact, str(old), "reader", "one-provider",
                 "edition-1", "missing"),
                ("wrong-provider", artifact, str(other_provider), "reader",
                 "another-provider", "edition-1", "present"),
                ("wrong-edition", artifact, str(other_edition), "reader",
                 "one-provider", "edition-2", "present"),
                ("phantom", artifact, str(phantom), "reader",
                 "one-provider", "edition-1", "present"),
            ],
        )
    before = filesystem(tmp_path)
    audit = audit_library(database, {"reader": (root,)})
    session = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    try:
        assert audit.stored_paths.states[0] == "stored_path_missing"
        cases = session.page(state="stored_path_missing").cases
        details = next(case.details for case in cases if str(old) in case.details)
        assert "Aucun autre chemin de même portée observé présent" in details
        assert str(other_provider) not in details
        assert str(other_edition) not in details
        assert str(phantom) not in details
        assert filesystem(tmp_path) == before
    finally:
        session.close()


def test_same_scope_candidate_preview_bounded_to_five_paths(tmp_path):
    root = tmp_path / "reader"
    root.mkdir()
    old = root / "missing.epub"
    paths = [root / f"candidate-{index}.epub" for index in range(6)]
    for path in paths:
        epub(path, title="Shared synthetic book")
    database = tmp_path / "synthetic.sqlite"
    fixture_db(database)
    artifact = "sha256:shared-artifact"
    rows = [
        ("old", artifact, str(old), "reader", "one-provider",
         "edition-1", "missing"),
    ]
    rows.extend(
        (f"copy-{index}", artifact, str(path), "reader", "one-provider",
         "edition-1", "present")
        for index, path in enumerate(paths)
    )
    with sqlite3.connect(database) as db:
        db.executemany(
            "INSERT INTO placements(placement_id,artifact_id,path,identity_id,"
            "provider_id,source_item_id,state) VALUES(?,?,?,?,?,?,?)",
            rows,
        )
    before = filesystem(tmp_path)
    audit = audit_library(database, {"reader": (root,)})
    session = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    try:
        row, = session.page(state="stored_path_missing").cases
        for path in paths[:5]:
            assert str(path) in row.details
        assert str(paths[5]) not in row.details
        assert "Autres candidats non affichés (limite 5)" in row.details
        assert filesystem(tmp_path) == before
    finally:
        session.close()
