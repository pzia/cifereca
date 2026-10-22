"""P0 #2797: mixed Kobo/Numilog/Paris and two Paris loan horizons.

No right/file attachment or loan action may be inferred from ISBN, from an
'active' SQLite flag, or from a locally stored expiry. Fully offline fixture.
"""
from __future__ import annotations

import json
import sqlite3

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_library_reconciliation import epub, filesystem, fixture_db


def test_mixed_provider_paris_loans_have_explicit_private_expiry_no_write(tmp_path):
    root = tmp_path / "synthetic-reader"
    root.mkdir()
    database = tmp_path / "synthetic.sqlite"
    fixture_db(database)
    rights = (
        ("kobo", "owned", "9780306406157", None, None),
        ("numilog", "owned", "9780140328721", None, None),
        ("paris", "loan", "9781402894626", "loan-one", "2020-01-01T00:00:00Z"),
        ("paris", "loan", "9781861972712", "loan-two", "2099-01-01T00:00:00Z"),
    )
    for index, (_, _, isbn, _, _) in enumerate(rights):
        epub(root / f"LOCAL-fixture-{index}.epub", xml=(
            "<package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
            f"<dc:title>TEST-LOCAL-BOOK-{index}</dc:title>"
            f"<dc:identifier scheme='ISBN'>{isbn}</dc:identifier>"
            "</metadata></package>"
        ))
    with sqlite3.connect(database) as db:
        for index, (provider, right_kind, isbn, loan_id, expires_at) in enumerate(rights):
            db.execute(
                "INSERT INTO entitlements("
                "entitlement_id,identity_id,provider_id,credential_id,"
                "account_id,item_id,state,entitlement_type,loan_id,expires_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"right-{index}", "reader", provider, f"cred-{provider}",
                    "PRIVATE-ACCOUNT", f"edition-{index}",
                    "active", right_kind, loan_id, expires_at,
                ),
            )
            db.execute(
                "INSERT INTO catalog_entries("
                "catalog_entry_id,identity_id,provider_id,item_id,metadata_json"
                ") VALUES (?,?,?,?,?)",
                (
                    f"notice-{index}", "reader", provider, f"edition-{index}",
                    json.dumps({"identifiers": [{"scheme": "ISBN", "value": isbn}]}),
                ),
            )
        # Foreign identity's same provider and ISBN never supplies local proof.
        db.execute(
            "INSERT INTO entitlements(entitlement_id,identity_id,provider_id,"
            "credential_id,account_id,item_id,state,entitlement_type)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("foreign-right", "other-reader", "paris", "cred-paris",
             "PRIVATE-FOREIGN", "edition-3", "active", "loan"),
        )
    before = filesystem(tmp_path)
    audit = audit_library(database, {"reader": (root,)})
    assert audit.redacted()["status"] == "complete", audit.redacted()["reasons"]
    session = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    assert session.page(state="rights_without_verified_file").total == 4
    assert session.unconfirmed_files(limit=25).total == 4
    assert session.page(provider="kobo", state="rights_without_verified_file").total == 1
    assert session.page(provider="numilog", state="rights_without_verified_file").total == 1
    paris_rights = session.page(provider="paris", state="rights_without_verified_file")
    assert paris_rights.total == 2
    assert all("PRIVATE-FOREIGN" not in case.details for case in paris_rights.cases)
    assert all("Nature du droit : Prêt temporaire" in case.details
               for case in paris_rights.cases)
    assert all("Bibliographie du prêt" in case.details
               for case in paris_rights.cases)
    assert any("2020-01-01T00:00:00Z" in case.details for case in paris_rights.cases)
    assert any("2099-01-01T00:00:00Z" in case.details for case in paris_rights.cases)
    assert all("Validité actuelle du prêt / identité ADEPT : non démontrée"
               in case.details for case in paris_rights.cases)

    for provider, expected in (("kobo", 1), ("numilog", 1), ("paris", 2)):
        cases = session.page(provider=provider, state="rights_without_verified_file").cases
        assert len(cases) == expected
        for right in cases:
            matches = session.candidate_files(right.key)
            assert len(matches) == 1
            assert matches[0].key.startswith("file:")
            report = session.preflight_manual_link(right.key, matches[0].key)
            assert report.read_only and not report.attachment_authorized
            assert report.status != "eligible_for_fresh_validation"
            if provider == "paris":
                assert "paris_read_only" in report.codes
            if provider == "kobo":
                assert "kobo_separate_recovery" in report.codes
            assert session.preview_manual_attachment(
                right.key, matches[0].key
            ) == {"status": "unavailable", "read_only": True}
            assert session.confirm_manual_attachment(
                right.key, matches[0].key, user_confirmed=True
            ) == {"status": "unavailable", "read_only": True}
    assert filesystem(tmp_path) == before
    public = json.dumps(audit.redacted())
    for secret in ("PRIVATE-", "9780306406157", "2020-01-01", "2099-01-01"):
        assert secret not in public
    session.close()
    assert not session.valid()
