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
    ("kobo", "owned", "source_not_supported"),
    ("paris", "loan", "source_not_supported"),
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
        provider=provider,
        state="rights_without_verified_file",
    ).cases
    right = next(case for case in cases if "edition" in case.details)
    files = session.unconfirmed_files(query="code-5", limit=5)
    assert files.total == 1
    file = files.cases[0]
    assert not hasattr(session, '_manual_right_proofs')

    # The retired Kobo pairing engine must no longer retain a private
    # backup-derived proof map or offer a provider-specific preflight.
    if provider == "kobo":
        assert not hasattr(session, "_kobo_right_proofs")
    report = session.preflight_manual_link(right.key, file.key)
    assert report.status == "blocked"
    assert report.codes == (reason,)
    assert report.read_only and not report.attachment_authorized
    assert reason in report.codes and "Numilog" not in report.explanation
    assert "attribution Numilog" not in right.details
    # Even with a service object, unrelated providers cannot reach the
    # retired Numilog writer; the public refusal remains read-only.
    session._manual_service = object()
    assert session.preview_manual_attachment(right.key, file.key) == {
        "status": "unavailable", "read_only": True,
    }
    assert filesystem(root) == before
    session.close()
    assert session.preflight_manual_link(right.key, file.key).status == "unavailable"


@pytest.mark.parametrize("loan_inactive", [False, True])
def test_read_only_refusal_has_no_provider_argument_or_authority(loan_inactive):
    import inspect
    assert "provider" not in inspect.signature(review_source_read_only).parameters
    report = review_source_read_only(
        audit_complete=False, bibliographic_hint=True,
        loan_inactive=loan_inactive,
    )
    assert report.status == "blocked"
    assert report.codes == tuple(sorted({
        "source_not_supported", "audit_incomplete", "bibliographic_hint_only",
        *({"loan_inactive_read_only"} if loan_inactive else set()),
    }))
    assert report.read_only and not report.attachment_authorized
    assert report.requires_fresh_validation
    assert "secret" not in report.explanation.lower()


def test_bnf_exact_loan_declaration_guard_does_not_authorize_other_sources(
    six_numilog,
):
    """Freeze the current asymmetric proof boundary until B supplies a typed policy.

    BnF declarations must name one active loan with a native loan ID. A
    provider-neutral rewrite must not silently weaken that BnF guarantee or
    disable existing non-BnF declarations without a migration contract.
    """
    root, database = six_numilog
    fixtures = [
        ("bnf-missing-id", "bnf-pret", "active", "loan", None),
        ("bnf-exact-active", "bnf-pret", "active", "loan", "loan-verified"),
        ("bnf-expired", "bnf-pret", "expired", "loan", "loan-expired"),
        ("bnf-owned", "bnf-pret", "active", "owned", None),
        ("future-owned", "future-library", "active", "owned", None),
        ("future-loan", "future-library", "active", "loan", None),
    ]
    with sqlite3.connect(database) as db:
        for item, provider, state, kind, loan_id in fixtures:
            db.execute(
                "INSERT INTO entitlements "
                "(entitlement_id,identity_id,provider_id,credential_id,"
                "account_id,item_id,state,entitlement_type,loan_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (f"test-{item}", "alice", provider, "cred-alice",
                 "account-alice", item, state, kind, loan_id),
            )

    _audit, session = session_for(root, database)
    declared_items = {
        scope[4] for scope in session._declared_right_scopes.values()
    }
    assert "bnf-exact-active" in declared_items
    assert "bnf-missing-id" not in declared_items
    assert "bnf-expired" not in declared_items
    assert "bnf-owned" not in declared_items

    # Existing other-source declarations are descriptive user statements
    # only, not evidence of a provider grant or an authorized attachment.
    assert {"future-owned", "future-loan"} <= declared_items
    # No retired private receipt proof map exists for any supplier.
    assert not hasattr(session, "_manual_right_proofs")
    session.close()
