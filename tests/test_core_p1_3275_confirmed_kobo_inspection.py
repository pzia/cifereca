"""#4859 W4/D5: a historical Kobo manifest label cannot publish a Core B.

The GUI's old confirmed W4 operation used historical v0.7 entitlement and
SHA clues. Only independently attested A→B may create a confirmed Placement.
This synthetic fixture has no canonical provider SourceReference/OriginLink.
"""
from __future__ import annotations

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_core_1345_confirmed_local_reconciliation import case as kobo_case  # noqa: F401
from tests.test_core_p0_2797_kobo_shared_link import selected


def test_historical_kobo_manifest_label_cannot_publish_confirmed_gui_link(selected):
    case, inspection, right, file = selected
    original_bytes = case.target.read_bytes()

    rights = case.storage.entitlements(provider_id="kobo")
    assert len(rights) == 1
    old_right = rights[0]
    # Even this old positive-looking source field is not a canonical
    # SourceReference, OriginLink or CompletedDelivery.
    case.storage.entitlement_put({
        **old_right,
        "metadata": {
            **(old_right.get("metadata") or {}),
            "source": "kobo.personal_manifest",
        },
    })
    assert not any(
        row.key.startswith("file:")
        for row in inspection.page(state="confirmed_link").cases
    )
    preview = inspection.preview_manual_attachment(right.key, file.key)
    assert preview == {
        "status": "blocked", "read_only": True,
        "reason_code": "kobo_canonical_evidence_required",
    }
    assert inspection.confirm_manual_attachment(
        right.key, file.key, user_confirmed=True,
    )["status"] == "unavailable"
    assert case.target.read_bytes() == original_bytes

    # No positive 0.7 placement can appear simply by reopening the audit.
    post = audit_library(case.storage.path, {"default": (case.target.parent,)})
    assert post.redacted()["status"] == "complete", post.redacted()["reasons"]
    reopened = LocalInspection(
        post, lambda: True, "default",
        roots=(case.target.parent,), service=case.service,
    )
    try:
        assert not any(
            row.key.startswith("file:")
            for row in reopened.page(state="confirmed_link").cases
        )
        assert reopened.overview()["confirmed_files"] == 0
        assert case.target.read_bytes() == original_bytes
    finally:
        reopened.close()
