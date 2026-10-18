"""P0 #1905: selecting an offline Numilog receipt must not enumerate rights."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.numilog_materialization_checkpoint import (
    NumilogIndexResumeTarget,
)
from tests.test_numilog_owned_library_download import case


def test_numilog_resume_button_queries_only_exact_owned_right(case, monkeypatch):
    acquired = case.service.acquire_direct_item(case.item)
    assert acquired.metadata["numilog_checkpoint_id"]
    storage = case.service.storage
    original = storage.numilog_possession_rights
    observed = []

    def targeted(**kwargs):
        observed.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(
        storage, "entitlements",
        lambda *_a, **_kw: pytest.fail(
            "resume button must not enumerate other accounts' active rights"
        ),
    )
    monkeypatch.setattr(storage, "numilog_possession_rights", targeted)
    target = case.service.owned_library_download_target(case.entry)
    assert type(target) is NumilogIndexResumeTarget
    assert target.item_id == case.item.record_id
    assert observed == [{
        "identity_id": case.account.identity_id,
        "item_id": case.item.record_id,
        "entitlement_id": case.entry["possessions"][0]["entitlement_id"],
    }]
    assert len(case.calls) == 1 and case.inventory == []
    assert "numilog:" not in repr(target)  # private item stays repr=False


def test_numilog_rights_lookup_does_not_load_foreign_entitlement_metadata(
    case, monkeypatch,
):
    acquired = case.service.acquire_direct_item(case.item)
    storage = case.service.storage
    secret = "PRIVATE_FOREIGN_PURCHASE_TOKEN"
    # Simulate a foreign account's huge or malicious stored metadata; it is
    # never included in the exact SQL result nor decoded.
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO entitlements (entitlement_id,provider_id,"
            "credential_id,account_id,item_id,identity_id,entitlement_type,"
            "state,metadata_json,discovered_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("foreign-right-abc", "numilog", "another-credential",
             "another-account", "numilog:foreign:fake",
             "alex", "owned", "active", '{"url":"' + secret + '"}', 1, 1),
        )
    decoded = []
    original_decode = storage._json_load

    def checked(value):
        assert secret not in str(value)
        decoded.append(True)
        return original_decode(value)

    monkeypatch.setattr(storage, "_json_load", checked)
    own = case.entry["possessions"][0]
    rows = storage.numilog_possession_rights(
        identity_id=case.account.identity_id,
        item_id=case.item.record_id,
        entitlement_id=own["entitlement_id"],
    )
    assert len(rows) == 1
    assert rows[0]["metadata"]["source"] == "numilog.authenticated_library"
    target = case.service.owned_library_download_target(case.entry)
    assert type(target) is NumilogIndexResumeTarget
    assert decoded and secret not in repr(rows)
    assert len(case.calls) == 1


@pytest.mark.parametrize("invalid", [
    None, "", True, [], "https://private.invalid?token=SECRET",
    "book\x00secret", "x" * 1200,
])
def test_numilog_resume_scope_rejects_invalid_identity(case, invalid):
    storage = case.service.storage
    with pytest.raises(ValueError, match="invalid_numilog_possession_scope"):
        storage.numilog_possession_rights(
            identity_id=invalid, item_id=case.item.record_id,
            entitlement_id="valid-id",
        )


def test_numilog_resume_right_is_not_cross_identity(case):
    acquired = case.service.acquire_direct_item(case.item)
    storage = case.service.storage
    own = case.entry["possessions"][0]
    foreign = storage.numilog_possession_rights(
        identity_id="alex", item_id=case.item.record_id,
        entitlement_id=own["entitlement_id"],
    )
    wrong_entitlement = storage.numilog_possession_rights(
        identity_id=case.account.identity_id, item_id=case.item.record_id,
        entitlement_id="unrelated-owned-right",
    )
    assert foreign == wrong_entitlement == []
