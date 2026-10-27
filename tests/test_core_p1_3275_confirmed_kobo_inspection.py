"""#3275: a confirmed Kobo file/right link has a different UI evidence level
from the B user declaration without an independently attested Kobo receipt.

Uses the real Core locked reconciliation fixture and a *new* LocalInspection
after the final commit. All data are synthetic; no provider requests or DRM.
"""
from __future__ import annotations

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_core_p0_2797_kobo_shared_link import selected


def test_independently_attested_kobo_receipt_projects_confirmed_link_after_reopen(
    selected,
):
    case, inspection, right, file = selected
    original_bytes = case.target.read_bytes()

    # The previous session has only a candidate, not a confirmed link.
    assert not any(
        row.key.startswith("file:")
        for row in inspection.page(state="confirmed_link").cases
    )
    preview = inspection.preview_manual_attachment(right.key, file.key)
    assert preview == {
        "status": "preview", "read_only": True,
        "requires_explicit_confirmation": True,
    }
    assert inspection.confirm_manual_attachment(
        right.key, file.key, user_confirmed=True,
    )["status"] == "applied"
    assert case.target.read_bytes() == original_bytes

    # Only the subsequent *fresh* read of the persisted, account-scoped
    # receipt may describe the file as a confirmed provider-right link.
    post = audit_library(
        case.storage.path, {"default": (case.target.parent,)},
    )
    assert post.redacted()["status"] == "complete", post.redacted()["reasons"]
    reopened = LocalInspection(
        post, lambda: True, "default",
        roots=(case.target.parent,), service=case.service,
    )
    try:
        verified = [
            row for row in reopened.page(state="confirmed_link").cases
            if row.key.startswith("file:")
        ]
        assert len(verified) == 1
        assert reopened.overview()["confirmed_files"] >= 1
        assert not any(
            row.key.startswith("file:")
            for row in reopened.page(state="declared_link_recorded").cases
        )
        assert reopened.unconfirmed_files().total == 0
        assert case.target.read_bytes() == original_bytes
    finally:
        reopened.close()
