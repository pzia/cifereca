"""#3196/#2797: a Kobo receipt for another borrower is not local possession.

SQLite v14 and disposable synthetic bytes only; no manifest, provider GET or
permanent entitlement changes. Legacy callers retain the old optional API.
"""
from __future__ import annotations

import hashlib

import pytest

from tests.test_owned_library_download import case


def _old_completed_receipt(case, *, account: str):
    # One valid physical Placement associated with a historical purchase of
    # the same provider/item/credential, but potentially a different borrower.
    payload = b"%PDF-1.7\nSynthetic historical other-account file\n"
    digest = hashlib.sha256(payload).hexdigest()
    physical = case.root / "EBooks" / "hugues" / "old-purchase.pdf"
    physical.parent.mkdir(parents=True, exist_ok=True)
    physical.write_bytes(payload)
    case.service.storage.artifact_put({
        "artifact_id": "sha256:" + digest, "sha256": digest,
        "size": len(payload), "media_format": "pdf",
    })
    case.service.storage.acquisition_put({
        "acquisition_id": "completed-other-account",
        "identity_id": "default",
        "provider_id": "kobo", "credential_id": "vault-1",
        "account_id": account, "item_id": "book-1",
        "status": "complete", "artifact_id": "sha256:" + digest,
        "metadata": {"path": str(physical)},
    })
    case.service.storage.placement_put({
        "identity_id": "default", "path": str(physical),
        "artifact_id": "sha256:" + digest, "state": "present",
    })
    return physical


def _lookup(case, **options):
    from cifereca.core.library_projection import local_placement_for_source
    return local_placement_for_source(
        case.service, provider_id="kobo", credential_id="vault-1",
        item_id="book-1", identity_id="default", **options,
    )


def test_completed_other_borrower_receipt_does_not_veto_exact_owned_kobo_right(
    case,
):
    path = _old_completed_receipt(case, account="other-borrower")
    before = case.service.storage.entitlements()
    # Historical consumers intentionally retain their old four-key lookup.
    assert _lookup(case) is not None
    # The public LibraryService facade preserves its exact legacy signature.
    assert case.service.local_placement_for_source(
        provider_id="kobo", credential_id="vault-1", item_id="book-1",
        identity_id="default",
    ) is not None
    assert _lookup(case, account_id="account") is None
    assert _lookup(case, account_id="other-borrower") is not None
    target = case.service.owned_library_download_target(case.entry)
    assert target is not None
    assert target.identity_id == "default"
    assert case.service.storage.entitlements() == before
    assert path.is_file()
    assert case.calls == []
    assert case.session.resolve_calls == []


def test_completed_current_borrower_receipt_blocks_repeat_get(case):
    path = _old_completed_receipt(case, account="account")
    assert _lookup(case, account_id="account") is not None
    assert case.service.owned_library_download_target(case.entry) is None
    assert path.is_file()
    assert case.calls == []
    assert case.session.resolve_calls == []


@pytest.mark.parametrize("stale_account", ["", "different", "account-copy"])
def test_ambiguous_or_missing_historical_account_never_vetoes_current_right(
    case, stale_account,
):
    _old_completed_receipt(case, account=stale_account)
    assert _lookup(case, account_id="account") is None
    assert case.service.owned_library_download_target(case.entry) is not None
    assert case.calls == []
    assert case.session.resolve_calls == []


def test_other_active_borrower_right_does_not_disable_current_selected_right(
    case,
):
    case.service.storage.entitlement_put({
        **case.right,
        "entitlement_id": "other-borrower-independent-owned-right",
        "account_id": "other-borrower",
        "revision_id": "other-borrower-revision",
    })
    before = case.service.storage.entitlements()
    assert len(before) == 2
    target = case.service.owned_library_download_target(case.entry)
    assert target is not None
    assert target.entitlement_id == "right-1"
    assert case.service.storage.entitlements() == before
    assert case.calls == []


def test_competing_active_right_on_same_borrower_still_blocks_implicit_choice(
    case,
):
    case.service.storage.entitlement_put({
        **case.right,
        "entitlement_id": "second-independent-right-same-borrower",
        "revision_id": "separate-revision",
    })
    assert case.service.owned_library_download_target(case.entry) is None
    assert case.calls == []
