"""P0 #2714: global inspection filter must find private ISBNs.

Only same-identity notices and observations may be searched. An ISBN is a
human navigation cue, NEVER an entitlement or placement proof.
"""
from __future__ import annotations

import json
import sqlite3

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_core_p0_2657_inspection_cases import six_numilog
from tests.test_library_reconciliation import filesystem


def test_right_search_uses_bibliographic_isbn_not_only_visible_case_label(
    six_numilog, tmp_path,
):
    root, database = six_numilog
    own_isbn = "9780306406157"
    foreign_isbn = "9781861972712"
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE catalog_entries SET metadata_json=? "
            "WHERE identity_id='alice' AND provider_id='numilog' AND item_id='item-5'",
            (json.dumps({
                "title": "Titre notice 5",
                "authors": ["Autrice Exemple 5"],
                "identifiers": [{"scheme": "ISBN", "value": own_isbn}],
            }),),
        )
        db.execute(
            "INSERT INTO catalog_entries("
            "catalog_entry_id,identity_id,provider_id,item_id,metadata_json"
            ") VALUES(?,?,?,?,?)",
            ("PRIVATE-FOREIGN", "bob", "numilog", "item-5", json.dumps({
                "title": "SECRET FOREIGN",
                "identifiers": [{"scheme": "ISBN", "value": foreign_isbn}],
            })),
        )
    before = filesystem(tmp_path)
    audit = audit_library(database, {"alice": (root,)})
    session = LocalInspection(audit, lambda: True, "alice", roots=(root,))
    try:
        with_isbn = session.page(
            provider="numilog", state="rights_without_verified_file",
            query=own_isbn,
        )
        assert with_isbn.total == 1
        right = with_isbn.cases[0]
        assert "item-5" in right.details
        assert own_isbn not in right.label
        assert session.page(query=foreign_isbn).total == 0
        assert session.page(query="SECRET FOREIGN").total == 0
        assert session.page(
            state="rights_without_verified_file", query="titre NOTICE 5",
        ).total == 1
        assert session.page(
            state="local_file_without_attested_right", query="code-5",
        ).total == 1
        assert session.preflight_manual_link(
            right.key, session.unconfirmed_files(query="code-5").cases[0].key,
        ).attachment_authorized is False
        assert all(len(value) <= 3200 for value in session._case_search.values())
        assert "PRIVATE-FOREIGN" not in json.dumps(audit.redacted())
        assert own_isbn not in json.dumps(audit.redacted())
        assert filesystem(tmp_path) == before
    finally:
        session.close()
    assert session._case_search == {}
    assert session.page(query=own_isbn).total == 0
