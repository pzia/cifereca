"""P0 #2712/#2797: 18 EPUB, nine Kobo rights, five same-ISBN hints.

Only synthetic books and SQLite records. The 5 hints are bibliographic,
never evidence of Kobo ownership or a restore capability. No writes.
"""
from __future__ import annotations

import json
import sqlite3

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_library_reconciliation import epub, filesystem, fixture_db


def test_small_kobo_identity_nine_rights_five_hints_never_authorizes_placement(tmp_path):
    root = tmp_path / "kobo-identity"
    root.mkdir()
    catalogue_isbn = "9780306406157"
    unrelated_isbn = "9780140328721"
    for index in range(18):
        epub(
            root / f"synthetic-{index:02d}.epub",
            title=f"LOCAL-SYNTHETIC-{index:02d}",
            identifier=catalogue_isbn if index < 5 else unrelated_isbn,
        )
    database = tmp_path / "private-test.sqlite"
    fixture_db(database)
    with sqlite3.connect(database) as db:
        for index in range(9):
            db.execute(
                "INSERT INTO entitlements("
                "entitlement_id, identity_id, provider_id, credential_id,"
                "account_id, item_id, state, entitlement_type"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"kobo-right-{index}", "reader", "kobo", "vault-fixture",
                    "account-fixture", f"kobo-edition-{index}",
                    "active", "owned",
                ),
            )
            db.execute(
                "INSERT INTO catalog_entries("
                "catalog_entry_id, identity_id, provider_id, item_id,"
                "metadata_json"
                ") VALUES(?,?,?,?,?)",
                (
                    f"catalogue-{index}", "reader", "kobo",
                    f"kobo-edition-{index}",
                    json.dumps({
                        "title": f"TITLE-KOBO-{index}",
                        "identifiers": (
                            [{"scheme": "ISBN", "value": catalogue_isbn}]
                            if index == 0 else []
                        ),
                    }),
                ),
            )

    before = filesystem(tmp_path)
    audit = audit_library(database, {"reader": (root,)})
    assert audit.redacted()["status"] == "complete", audit.redacted()["reasons"]
    session = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    rights = session.page(
        provider="kobo", state="rights_without_verified_file", limit=50,
    )
    assert rights.total == 9
    assert session.unconfirmed_files(limit=25).total == 18
    candidates = session.candidate_files(rights.cases[0].key)
    assert len(candidates) == 5
    assert all(case.key.startswith("file:") for case in candidates)
    assert all(
        not session.candidate_files(case.key)
        for case in rights.cases[1:]
    )

    # ISBN matching is an exploration hint, never a Kobo restoration right:
    # no attested backup SHA/revision/account evidence exists in this fixture.
    for file in candidates:
        reviewed = session.preflight_manual_link(rights.cases[0].key, file.key)
        assert reviewed.status == "blocked"
        assert reviewed.read_only and reviewed.attachment_authorized is False
        assert "kobo_separate_recovery" in reviewed.codes
        assert session.preview_manual_attachment(
            rights.cases[0].key, file.key,
        ) == {"status": "unavailable", "read_only": True}
        assert session.confirm_manual_attachment(
            rights.cases[0].key, file.key, user_confirmed=True,
        ) == {"status": "unavailable", "read_only": True}
    public = json.dumps(audit.redacted())
    assert "TITLE-KOBO-" not in public
    assert "account-fixture" not in public
    assert catalogue_isbn not in public
    assert filesystem(tmp_path) == before
    session.close()
    assert not session.valid() and not session.candidate_files(rights.cases[0].key)
