"""P0 #2714: named Numilog purchases without fictitious catalogue joins.

The local entitlement already stores title/author/format; only explicitly
bounded descriptions reach private CoreUI. Never manufacture proof of EPUB
ownership, receipt, account scope, acquisition or a download.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_core_p0_2657_inspection_cases import six_numilog
from tests.test_library_reconciliation import filesystem


def _session(fixture):
    root, database = fixture
    before = filesystem(root.parent)
    audit = audit_library(database, {"alice": (root,)})
    session = LocalInspection(audit, lambda: True, "alice")
    assert filesystem(root.parent) == before
    return audit, session


def _right(session, number=5):
    cases = session.page(provider="numilog", state="rights_without_verified_file")
    assert cases.total == 3
    return next(case for case in cases.cases if f"item-{number}" in case.details)


def _metadata(database, number, metadata):
    with sqlite3.connect(database) as db:
        db.execute("UPDATE entitlements SET metadata_json=? WHERE entitlement_id=?",
                   (json.dumps(metadata), f"right-{number}"))


def test_entitlement_fallback_when_catalogue_missing_keeps_title_author_and_format(
    six_numilog,
):
    root, database = six_numilog
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM catalog_entries WHERE item_id=?", ("item-5",))
    _metadata(database, 5, {
        "source": "numilog.authenticated_library",
        "title": "Roman issu des achats Numilog",
        "author": "Autrice de la bibliothèque",
        "media_format": "epub",
        "SECRET_URL": "PRIVATE_SIGNED_LINK_2714",
        "access_token": "PRIVATE_TOKEN_2714",
    })
    audit, session = _session(six_numilog)
    case = _right(session)
    assert "Roman issu des achats Numilog" in case.label
    assert "Autrice de la bibliothèque" in case.label
    assert "• Titre : Roman issu des achats Numilog" in case.details
    assert "• Auteur(s) : Autrice de la bibliothèque" in case.details
    assert "• Format déclaré : epub" in case.details
    assert "• Édition fournisseur : item-5" in case.details
    assert "Aucune notice locale de cette édition exacte" in case.details
    assert "bibliothèque authentifiée" in case.details
    assert "Aucune acquisition liée" in case.details
    assert "PRIVATE_TOKEN" not in case.details
    assert "PRIVATE_SIGNED" not in case.details
    assert audit.redacted()["reconciliation"]["counts"]["rights_without_verified_file"] == 3
    assert "Roman issu" not in json.dumps(audit.redacted())
    assert "Autrice de la bibliothèque" not in json.dumps(audit.redacted())
    assert "PRIVATE" not in json.dumps(session.summary())


def test_one_exact_catalogue_notice_remains_preferred_over_entitlement_description(
    six_numilog,
):
    _root, database = six_numilog
    _metadata(database, 5, {
        "source": "numilog.authenticated_library",
        "title": "Titre de bibliothèque contradictoire",
        "author": "Autre autrice",
        "media_format": "pdf",
    })
    _audit, session = _session(six_numilog)
    case = _right(session)
    assert "Titre notice 5" in case.label
    assert "Autrice Exemple 5" in case.label
    assert "Titre de bibliothèque contradictoire" not in case.details
    assert "• Format déclaré : pdf" in case.details
    assert "Origine du titre : notice de catalogue locale" in case.details
    assert "ne prouve pas le compte ni un fichier" in case.details
    assert session.page(query="titre de bibliothèque contradictoire").total == 0


def test_ambiguous_catalogue_does_not_choose_arbitrary_notice_for_purchase(
    six_numilog,
):
    _root, database = six_numilog
    _metadata(database, 5, {
        "source": "numilog.authenticated_library",
        "title": "Titre garanti par la bibliothèque",
        "author": "Auteure univoque",
    })
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO catalog_entries (catalog_entry_id,identity_id,provider_id,item_id,metadata_json) "
            "VALUES (?,?,?,?,?)",
            ("duplicated-notice", "alice", "numilog", "item-5",
             json.dumps({"title": "PRIVATE_AMBIGUOUS_CATALOGUE_TITLE",
                         "authors": ["PRIVATE_OTHER_AUTHOR"]})),
        )
    audit, session = _session(six_numilog)
    case = _right(session)
    assert "Titre garanti par la bibliothèque" in case.label
    assert "Auteure univoque" in case.details
    assert "Plusieurs notices locales" in case.details
    assert "PRIVATE_AMBIGUOUS" not in case.details
    assert "PRIVATE_OTHER_AUTHOR" not in case.details
    assert "PRIVATE_AMBIGUOUS" not in json.dumps(audit.redacted())


def test_absent_or_malformed_descriptive_fields_have_explained_fallback(
    six_numilog,
):
    _root, database = six_numilog
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM catalog_entries WHERE item_id=?", ("item-5",))
    _metadata(database, 5, {
        "source": "numilog.authenticated_library",
        "title": ["PRIVATE_BAD_TITLE"],
        "author": {"name": "PRIVATE_BAD_AUTHOR"},
        "media_format": "PRIVATE_UNSUPPORTED",
    })
    _audit, session = _session(six_numilog)
    case = _right(session)
    assert "Droit sans fichier vérifié · item-5" in case.label
    assert "• Titre : Non renseigné" in case.details
    assert "• Auteur(s) : Non renseigné" in case.details
    assert "• Format déclaré : Non renseigné" in case.details
    assert "Indisponible dans les données locales" in case.details
    assert "PRIVATE_BAD" not in case.details
    assert "PRIVATE_UNSUPPORTED" not in case.details


def test_foreign_metadata_can_never_name_an_alice_right(six_numilog):
    root, database = six_numilog
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO entitlements "
            "(entitlement_id,provider_id,credential_id,account_id,item_id,"
            "identity_id,state,entitlement_type,metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("FOREIGN_ENTITLEMENT", "numilog", "bob-cred", "bob-account",
             "item-5", "bob", "active", "owned",
             json.dumps({"source": "numilog.authenticated_library",
                         "title": "PRIVATE_BOB_TITLE",
                         "author": "PRIVATE_BOB_AUTHOR"})),
        )
    _audit, session = _session(six_numilog)
    all_cases = session.page().cases
    assert not any("PRIVATE_BOB" in case.label + case.details
                   for case in all_cases)
    assert "PRIVATE_BOB" not in str(session.summary())


def test_entitlement_text_is_not_a_verified_link_even_if_it_matches_a_local_epub(
    six_numilog,
):
    _root, database = six_numilog
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM catalog_entries WHERE item_id=?", ("item-5",))
    _metadata(database, 5, {
        "source": "numilog.authenticated_library",
        "title": "Titre caché 5",
        "author": "PRIVATE_LOCAL_AUTHOR",
        "media_format": "epub",
    })
    audit, session = _session(six_numilog)
    assert "Titre caché 5" in _right(session).label
    assert session.candidate_files(_right(session).key) == ()
    assert session.overview()["confirmed_files"] == 3
    assert session.overview()["unverified_rights"] == 3
    assert audit.redacted()["repair_authorized"] is False
    assert audit.redacted()["acquisition_authorized"] is False
