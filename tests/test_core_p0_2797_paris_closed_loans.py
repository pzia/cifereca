"""P0 #2797: inactive/returned Paris loans remain browseable, never writable.

The graph intentionally counts only ACTIVE unverified entitlements. A
historically returned loan is NOT a missing active purchase and its displayed
ISBN can never confer a new placement or ADEPT authority.
"""
from __future__ import annotations

import json
import sqlite3

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_library_reconciliation import epub, filesystem, fixture_db


def test_closed_paris_loans_are_visible_without_inflating_active_rights(tmp_path):
    root = tmp_path / "synthetic-local"
    root.mkdir()
    database = tmp_path / "synthetic.sqlite"
    fixture_db(database)
    records = (
        ("active", "9780306406157", "2099-01-01T00:00:00Z"),
        ("expired", "9780140328721", "2020-01-01T00:00:00Z"),
        ("returned", "9781402894626", "2025-01-01T00:00:00Z"),
    )
    for i, (_state, isbn, _date) in enumerate(records):
        epub(
            root / f"book-{i}.epub",
            xml=(
                "<package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
                f"<dc:title>Synthetic {i}</dc:title>"
                f"<dc:identifier scheme='ISBN'>{isbn}</dc:identifier>"
                "</metadata></package>"
            ),
        )
    with sqlite3.connect(database) as db:
        for i, (state, isbn, expires_at) in enumerate(records):
            db.execute(
                "INSERT INTO entitlements("
                "entitlement_id,identity_id,provider_id,credential_id,account_id,"
                "item_id,entitlement_type,state,loan_id,expires_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (f"loan-{i}", "reader", "paris", "cred", "PRIVATE-account",
                 f"edition-{i}", "loan", state, f"loan-{i}", expires_at),
            )
            db.execute(
                "INSERT INTO catalog_entries("
                "catalog_entry_id,identity_id,provider_id,item_id,metadata_json"
                ") VALUES(?,?,?,?,?)",
                (f"notice-{i}", "reader", "paris", f"edition-{i}",
                 json.dumps({"identifiers": [{"scheme": "ISBN", "value": isbn}]})),
            )
        db.execute(
            "INSERT INTO entitlements("
            "entitlement_id,identity_id,provider_id,item_id,entitlement_type,state"
            ") VALUES(?,?,?,?,?,?)",
            ("foreign-loan", "other-reader", "paris", "PRIVATE-other",
             "loan", "returned"),
        )
    before = filesystem(tmp_path)
    audit = audit_library(database, {"reader": (root,)})
    assert audit.redacted()["status"] == "complete", audit.redacted()["reasons"]
    # The public reconciliation graph makes no claim of missing books for
    # these two historically inactive loans.
    assert audit.reconciliation.redacted()["counts"]["rights_without_verified_file"] == 1
    session = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    summary = session.overview()
    assert summary["rights"] == 3
    assert summary["rights_active"] == 1
    assert summary["rights_unverified_total"] == 1
    assert summary["unverified_rights"] == 1
    assert summary["paris_inactive_loans"] == 2
    assert summary["inactive_loans"] == 2
    assert session.page(provider="paris", state="rights_without_verified_file").total == 1
    closed = session.page(provider="paris", state="loan_inactive", limit=50)
    assert closed.total == 2
    assert all("lecture seule" in case.label.lower() for case in closed.cases)
    assert all(case.provider == "paris" for case in closed.cases)
    assert all("PRIVATE-other" not in case.details for case in closed.cases)
    for case in closed.cases:
        candidates = session.candidate_files(case.key)
        assert len(candidates) == 1
        file = candidates[0]
        report = session.preflight_manual_link(case.key, file.key)
        assert report.status == "blocked"
        assert report.read_only and not report.attachment_authorized
        assert {"paris_loan_inactive", "paris_read_only"} <= set(report.codes)
        assert "Numilog" not in report.explanation
        assert session.preview_manual_attachment(
            case.key, file.key,
        ) == {"status": "unavailable", "read_only": True}
        assert session.confirm_manual_attachment(
            case.key, file.key, user_confirmed=True,
        ) == {"status": "unavailable", "read_only": True}
    assert filesystem(tmp_path) == before
    assert "PRIVATE-account" not in json.dumps(audit.redacted())
    session.close()
    assert session.page(state="loan_inactive").total == 0


def test_future_provider_closed_loan_is_visible_and_read_only(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    database = tmp_path / "private.sqlite"
    fixture_db(database)
    epub(root / "fictional.epub", xml=(
        "<package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
        "<dc:title>Fictional loan</dc:title>"
        "<dc:identifier scheme='ISBN'>9780306406157</dc:identifier>"
        "</metadata></package>"
    ))
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO entitlements("
            "entitlement_id,identity_id,provider_id,credential_id,account_id,"
            "item_id,entitlement_type,state,loan_id,expires_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("right-future", "reader", "fixture-lender", "fixture-cred",
             "fixture-account", "edition-1", "loan", "returned", "2024-01-01"),
        )
        db.execute(
            "INSERT INTO catalog_entries("
            "catalog_entry_id,identity_id,provider_id,item_id,metadata_json"
            ") VALUES(?,?,?,?,?)",
            ("notice-future", "reader", "fixture-lender", "edition-1",
             json.dumps({"identifiers": [
                 {"scheme": "ISBN", "value": "9780306406157"},
             ]})),
        )
        db.execute(
            "INSERT INTO entitlements("
            "entitlement_id,identity_id,provider_id,item_id,entitlement_type,state"
            ") VALUES(?,?,?,?,?,?)",
            ("foreign", "other-reader", "fixture-lender", "edition-1",
             "loan", "returned"),
        )
    before = filesystem(tmp_path)
    audit = audit_library(database, {"reader": (root,)})
    assert audit.reconciliation.redacted()["counts"]["rights_without_verified_file"] == 0
    session = LocalInspection(audit, lambda: True, "reader", roots=(root,))
    assert session.overview()["unverified_rights"] == 0
    assert session.overview()["inactive_loans"] == 1
    assert session.overview()["paris_inactive_loans"] == 0
    assert session.page(provider="fixture-lender", state="rights_without_verified_file").total == 0
    rows = session.page(provider="fixture-lender", state="loan_inactive").cases
    assert len(rows) == 1
    assert "Prêt non actif" in rows[0].label
    assert "Fictional loan" not in rows[0].label  # no inferred right from ISBN
    assert "PRIVATE" not in repr(rows)
    candidate = session.candidate_files(rows[0].key)
    assert len(candidate) == 1
    review = session.preflight_manual_link(rows[0].key, candidate[0].key)
    assert review.status == "blocked" and review.read_only
    assert review.attachment_authorized is False
    assert "source_not_supported" in review.codes
    assert session.preview_manual_attachment(rows[0].key, candidate[0].key) == {
        "status": "unavailable", "read_only": True,
    }
    assert session.confirm_manual_attachment(
        rows[0].key, candidate[0].key, user_confirmed=True,
    ) == {"status": "unavailable", "read_only": True}
    assert filesystem(tmp_path) == before
    session.close()
