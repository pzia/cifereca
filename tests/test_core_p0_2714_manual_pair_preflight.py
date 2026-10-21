"""P0 #2714: no-write, account-scoped Numilog right / local EPUB preflight.

Every fixture is synthetic. No output authorizes a placement or imports a
transaction writer, and file lookup must use the already scanned evidence.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_inventory import InventoryLimits
from cifereca.core.library_local_inspection import LocalInspection
from cifereca.core.library_manual_link_preflight import review_local_pair
from tests.test_core_p0_2657_inspection_cases import six_numilog
from tests.test_library_reconciliation import filesystem


def session_for(root, database, *, limits=None):
    audit = audit_library(database, {"alice": (root,)}, limits=limits)
    return audit, LocalInspection(audit, lambda: True, "alice", roots=(root,))


def select_pair(session, number=5):
    right = next(
        item for item in session.page(
            provider="numilog", state="rights_without_verified_file",
        ).cases if f"item-{number}" in item.details
    )
    files = session.unconfirmed_files(query=f"code-{number}", limit=5)
    assert files.total == 1
    return right, files.cases[0]


def add_acquisition(root, database, *, right_id="right-5",
                    file_number=5, ledger_owner="alice", count=1):
    file = root / f"code-{file_number}.epub"
    raw = file.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    artifact = f"sha256:{digest}"
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT OR IGNORE INTO artifacts (artifact_id,sha256,size,media_format) VALUES (?,?,?,?)",
            (artifact, digest, len(raw), "epub"),
        )
        for index in range(count):
            db.execute(
                "INSERT INTO acquisition_ledger "
                "(acquisition_id,entitlement_id,provider_id,credential_id,"
                "account_id,item_id,identity_id,status,artifact_id)"
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (f"manual-fixture-{index}-{ledger_owner}",
                 right_id, "numilog", "cred-alice", "account-alice",
                 "item-5", ledger_owner, "complete", artifact),
            )


def test_right_without_attested_receipt_never_becomes_link_even_if_file_is_good(
    six_numilog, tmp_path,
):
    root, db = six_numilog
    before = filesystem(tmp_path)
    audit, session = session_for(root, db)
    right, file = select_pair(session)
    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "insufficient_evidence"
    assert report.read_only is True and report.attachment_authorized is False
    assert report.requires_fresh_validation is True
    assert "acquisition_missing" in report.codes
    assert "transaction_unavailable" in report.codes
    assert "bibliographic_hint_only" not in report.codes
    assert "reçu d’acquisition" in report.explanation
    assert "PRIVATE" not in report.explanation
    assert audit.redacted()["repair_authorized"] is False
    assert filesystem(tmp_path) == before
    session.close()
    assert not session._manual_right_proofs and not session._manual_file_proofs


def test_same_scope_receipt_and_verified_same_bytes_is_reviewable_not_writable(
    six_numilog, tmp_path,
):
    root, db = six_numilog
    add_acquisition(root, db)
    before = filesystem(tmp_path)
    _audit, session = session_for(root, db)
    right, file = select_pair(session)
    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "eligible_for_fresh_validation"
    assert report.codes == ("transaction_unavailable",)
    assert not report.attachment_authorized and report.read_only
    assert filesystem(tmp_path) == before


@pytest.mark.parametrize("file_number,missing", [
    (4, "artifact_mismatch"),
    (5, "acquisition_missing"),
])
def test_receipt_mismatch_and_no_receipt_are_not_ownership_evidence(
    six_numilog, file_number, missing,
):
    root, db = six_numilog
    if file_number == 4:
        add_acquisition(root, db)
    _audit, session = session_for(root, db)
    right, file = select_pair(session, number=file_number)
    # For the 4th EPUB use right-5, to exercise mismatched artifacts.
    if file_number == 4:
        right, _ = select_pair(session, number=5)
    result = session.preflight_manual_link(right.key, file.key)
    assert result.status in ("blocked", "insufficient_evidence")
    assert missing in result.codes
    assert result.attachment_authorized is False


def test_partial_scan_or_unverified_sha_blocks_even_if_a_receipt_exists(
    six_numilog,
):
    root, db = six_numilog
    add_acquisition(root, db)
    audit, session = session_for(root, db, limits=InventoryLimits(hash_bytes=1))
    assert audit.redacted()["status"] == "incomplete_scan"
    right, file = select_pair(session)
    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "blocked"
    assert {"audit_incomplete", "file_unverified"} <= set(report.codes)
    assert not report.attachment_authorized


def test_cross_identity_duplicate_ledger_never_joins_right_as_positive_proof(
    six_numilog,
):
    root, db = six_numilog
    add_acquisition(root, db, ledger_owner="bob")
    _audit, session = session_for(root, db)
    right, file = select_pair(session)
    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "blocked"
    assert "acquisition_conflict" in report.codes
    assert "acquisition_missing" in report.codes
    assert "PRIVATE" not in json.dumps({
        "status": report.status, "codes": report.codes,
    })


def test_same_edition_multiple_scoped_rights_are_ambiguous(six_numilog):
    root, db = six_numilog
    with sqlite3.connect(db) as sql:
        sql.execute(
            "INSERT INTO entitlements "
            "(entitlement_id,provider_id,credential_id,account_id,item_id,"
            "identity_id,state,entitlement_type,metadata_json)"
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("right-5-duplicate", "numilog", "cred-alice", "account-alice",
             "item-5", "alice", "active", "owned",
             json.dumps({"source": "numilog.authenticated_library"})),
        )
    _audit, session = session_for(root, db)
    right, file = select_pair(session)
    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "blocked"
    assert "right_scope_ambiguous" in report.codes


def test_invalid_or_confirmed_or_foreign_selection_and_revocation_return_no_authority(
    six_numilog,
):
    root, db = six_numilog
    _audit, session = session_for(root, db)
    right, file = select_pair(session)
    confirmed = session.page(state="confirmed_link").cases[0]
    for rights, files in (
        (right.key, confirmed.key),
        ("right:PRIVATE_FOREIGN", file.key),
        (file.key, right.key),
        (right.key, "file:999999"),
        (None, file.key),
    ):
        report = session.preflight_manual_link(rights, files)
        assert report.status == "unavailable"
        assert report.codes == ("selection_unavailable",)
        assert not report.attachment_authorized
    session.close()
    stale = session.preflight_manual_link(right.key, file.key)
    assert stale.status == "unavailable"
    assert not stale.attachment_authorized


def test_hostile_preflight_inputs_do_not_echo_values_or_raise():
    secret = "PRIVATE_PATH /home/secret 9780306406157"
    result = review_local_pair(
        {"attested": True, "ledger_count": True, "signature": [secret, secret]},
        {"verified": True, "signature": (secret, True)},
        audit_complete=True, bibliographic_hint=True,
    )
    assert result.status == "blocked"
    assert result.attachment_authorized is False
    assert secret not in result.explanation + repr(result)
