"""P0 #2797: source-aware shared inspection remains read-only outside Numilog.

Mixed, synthetic active rights; an ISBN suggestion is never a transaction token.
"""
import sqlite3

import pytest

from cifereca.core.library_manual_link_preflight import review_source_read_only
from tests.test_core_p0_2714_manual_pair_preflight import session_for
from tests.test_core_p0_2657_inspection_cases import six_numilog
from tests.test_library_reconciliation import filesystem


@pytest.mark.parametrize("provider,kind,reason", [
    ("kobo", "owned", "kobo_separate_recovery"),
    ("paris", "loan", "paris_read_only"),
    ("unrecognized", "owned", "source_not_supported"),
])
def test_shared_pair_inspection_does_not_route_other_sources_to_numilog(
    six_numilog, provider, kind, reason,
):
    root, database = six_numilog
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO entitlements "
            "(entitlement_id,identity_id,provider_id,account_id,item_id,"
            "credential_id,state,entitlement_type) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("synthetic-other-right", "alice", provider, "acct", "edition",
             "cred", "active", kind),
        )
    before = filesystem(root)
    _audit, session = session_for(root, database)
    cases = session.page(
        provider=provider if provider != "unrecognized" else "other",
        state="rights_without_verified_file",
    ).cases
    right = next(case for case in cases if "edition" in case.details)
    files = session.unconfirmed_files(query="code-5", limit=5)
    assert files.total == 1
    file = files.cases[0]

    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "blocked"
    assert report.codes == (reason,)
    assert report.read_only and not report.attachment_authorized
    assert reason in report.codes and "Numilog" not in report.explanation
    assert "attribution Numilog" not in right.details
    # Even if a caller supplies a service later, the shared read-only
    # preflight cannot lead to the Numilog writer.
    session._manual_service = object()
    assert session._transaction_inputs(right.key, file.key) is None
    assert session.preview_manual_attachment(right.key, file.key) == {
        "status": "unavailable", "read_only": True,
    }
    assert filesystem(root) == before
    session.close()
    assert session.preflight_manual_link(right.key, file.key).status == "unavailable"


@pytest.mark.parametrize("provider,reason", [
    ("kobo", "kobo_separate_recovery"),
    ("paris", "paris_read_only"),
    ("unexpected", "source_not_supported"),
    (None, "source_not_supported"),
])
def test_read_only_provider_reason_is_closed_even_with_incomplete_index(
    provider, reason,
):
    report = review_source_read_only(
        provider, audit_complete=False, bibliographic_hint=True,
    )
    assert report.status == "blocked"
    assert report.codes == tuple(sorted({
        reason, "audit_incomplete", "bibliographic_hint_only",
    }))
    assert report.read_only and not report.attachment_authorized
    assert report.requires_fresh_validation
    assert "secret" not in report.explanation.lower()
