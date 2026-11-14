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


def test_manual_pair_missing_receipt_is_read_only_for_retired_writer(six_numilog, tmp_path):
    root, db = six_numilog
    before = filesystem(tmp_path)
    audit, session = session_for(root, db)
    right, file = select_pair(session)
    assert not hasattr(session, "_manual_right_proofs")
    assert not hasattr(session, "_manual_file_proofs")
    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "blocked"
    assert "source_not_supported" in report.codes
    assert report.read_only and not report.attachment_authorized
    assert audit.redacted()["repair_authorized"] is False
    assert filesystem(tmp_path) == before
    session.close()
    assert not session._manual_file_observations


def test_complete_numilog_receipt_and_identical_bytes_do_not_revive_manual_link(
    six_numilog, tmp_path,
):
    root, db = six_numilog
    add_acquisition(root, db)
    before = filesystem(tmp_path)
    _audit, session = session_for(root, db)
    right, file = select_pair(session)
    assert not hasattr(session, "_manual_right_proofs")
    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "blocked"
    assert "source_not_supported" in report.codes
    assert report.read_only and not report.attachment_authorized
    # No legacy source-specific proof even after a matching complete receipt.
    assert session.preview_manual_attachment(right.key, file.key) == {
        "status": "unavailable", "read_only": True,
    }
    assert filesystem(tmp_path) == before
    session.close()


@pytest.mark.parametrize("forged_source", [
    "numilog.authenticated_library",
    "future-library.authenticated_library",
    "future-library.personal_manifest",
])
def test_foreign_provider_cannot_spoof_retired_manual_proof(
    six_numilog, forged_source,
):
    root, db = six_numilog
    add_acquisition(root, db)
    with sqlite3.connect(db) as sql:
        sql.execute(
            "INSERT INTO entitlements "
            "(entitlement_id,provider_id,credential_id,account_id,item_id,"
            "identity_id,state,entitlement_type,metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("foreign-right", "future-library", "cred-alice", "account-alice",
             "item-5", "alice", "active", "owned",
             json.dumps({"source": forged_source})),
        )
    _audit, session = session_for(root, db)
    foreign = session.page(provider="future-library").cases
    assert len(foreign) == 1
    _, file = select_pair(session)
    report = session.preflight_manual_link(foreign[0].key, file.key)
    assert report.status == "blocked"
    assert "source_not_supported" in report.codes
    assert report.read_only and not report.attachment_authorized
    assert not hasattr(session, "_manual_right_proofs")
    session.close()


@pytest.mark.parametrize("file_number", [4, 5])
def test_receipt_mismatch_or_absence_stays_non_authorizing(
    six_numilog, file_number,
):
    root, db = six_numilog
    if file_number == 4:
        add_acquisition(root, db)
    _audit, session = session_for(root, db)
    right, file = select_pair(session, number=file_number)
    if file_number == 4:
        right, _ = select_pair(session, number=5)
    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "blocked"
    assert "source_not_supported" in report.codes
    assert not report.attachment_authorized
    session.close()


def test_partial_scan_keeps_coverage_warning_and_refuses_manual_link(six_numilog):
    root, db = six_numilog
    add_acquisition(root, db)
    audit, session = session_for(root, db, limits=InventoryLimits(hash_bytes=1))
    assert audit.redacted()["status"] == "incomplete_scan"
    right, file = select_pair(session)
    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "blocked"
    assert {"audit_incomplete", "source_not_supported"} <= set(report.codes)
    assert not report.attachment_authorized
    session.close()


def test_foreign_ledger_and_duplicate_rights_cannot_revive_manual_link(six_numilog):
    root, db = six_numilog
    add_acquisition(root, db, ledger_owner="bob")
    with sqlite3.connect(db) as sql:
        sql.execute(
            "INSERT INTO entitlements "
            "(entitlement_id,provider_id,credential_id,account_id,item_id,"
            "identity_id,state,entitlement_type,metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("right-5-duplicate", "numilog", "cred-alice", "account-alice",
             "item-5", "alice", "active", "owned",
             json.dumps({"source": "numilog.authenticated_library"})),
        )
    _audit, session = session_for(root, db)
    right, file = select_pair(session)
    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "blocked"
    assert "source_not_supported" in report.codes
    assert not report.attachment_authorized
    assert "PRIVATE" not in json.dumps({
        "status": report.status, "codes": report.codes,
    })
    session.close()


def test_invalid_confirmed_foreign_selection_and_revocation_return_no_authority(
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
    assert session.preflight_manual_link(right.key, file.key).status == "unavailable"


def test_action_guidance_is_current_but_never_authorizes_a_write():
    # A fully attested review is STILL non-authorizing. The Core already
    # implements a separate transaction; telling users to build one is stale.
    signature = ("a" * 64, 123)
    proof = {
        "attested": True, "ambiguous": False,
        "ledger_conflict": False, "ledger_count": 1,
        "ledger_complete": True, "signature": signature,
    }
    selected = {"verified": True, "signature": signature}
    eligible = review_local_pair(proof, selected, audit_complete=True)
    assert eligible.status == "eligible_for_fresh_validation"
    assert "transaction_unavailable" in eligible.codes
    assert "Vérifier les preuves actuelles" in eligible.explanation
    assert "confirmez explicitement" in eligible.explanation
    assert "à construire" not in eligible.explanation
    assert eligible.read_only is True
    assert eligible.attachment_authorized is False

    missing = review_local_pair(
        {**proof, "ledger_count": 0, "ledger_complete": False},
        selected, audit_complete=True,
    )
    assert missing.status == "insufficient_evidence"
    assert "acquisition_missing" in missing.codes
    assert "si la paire est admissible".casefold() in missing.explanation.casefold()
    assert missing.attachment_authorized is False


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


@pytest.mark.parametrize("incomplete", [
    {}, {"ambiguous": None}, {"ambiguous": 0}, {"ambiguous": "false"},
    {"ledger_conflict": None}, {"ledger_conflict": 0},
    {"ledger_conflict": "false"},
])
def test_missing_or_malformed_private_negative_proof_cannot_be_reviewable(incomplete):

    signature = ("a" * 64, 123)
    right = {
        "attested": True, "ambiguous": False,
        "ledger_conflict": False, "ledger_count": 1,
        "ledger_complete": True, "signature": signature,
    }
    file = {"verified": True, "signature": signature}
    if not incomplete:
        right.pop("ambiguous")
        right.pop("ledger_conflict")
    else:
        right.update(incomplete)
    manual = review_local_pair(right, file, audit_complete=True)
    assert manual.status == "blocked"
    assert not manual.attachment_authorized
    if right.get("ambiguous") is not False:
        assert "right_scope_ambiguous" in manual.codes
    if right.get("ledger_conflict") is not False:
        assert "acquisition_conflict" in manual.codes




def test_core_inspection_no_longer_branches_on_numilog_manual_rights():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(LocalInspection))
    initializer = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef)
        and node.name == "__init__"
    )
    assert not any(
        isinstance(node, ast.Constant) and node.value == "numilog"
        for node in ast.walk(initializer)
    )
    assert "_manual_right_proofs" not in inspect.getsource(LocalInspection)
    assert "_manual_file_proofs" not in inspect.getsource(LocalInspection)
