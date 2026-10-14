"""#1346: a personal Kobo inventory never arbitrarily promotes duplicate rights.

Offline FakeVault only, no network, acquisition, storage or file changes.
"""
from __future__ import annotations

import json

from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


def test_library_quarantines_all_duplicate_personal_revisions_and_aliases():
    vault = FakeVault([
        {"product_id": "edition-1", "account_id": "alice",
         "owned": True, "revision_id": "PRIVATE_REV_A",
         "title": "PRIVATE_REVISION_A"},
        {"product_id": "edition-1", "account_id": "alice",
         "owned": False, "revision_id": "PRIVATE_REV_B",
         "title": "PRIVATE_REVISION_B"},
        {"id": "edition-2", "account_id": "alice",
         "owned": True, "revision_id": "PRIVATE_REV_C"},
        {"product_id": "edition-2", "id": "edition-2",
         "account_id": "alice", "owned": True,
         "revision_id": "PRIVATE_REV_D"},
        {"product_id": "alias-a", "id": "alias-b",
         "account_id": "alice", "owned": True,
         "revision_id": "PRIVATE_BAD_ALIAS"},
        {"product_id": "alias-a", "account_id": "alice",
         "owned": True, "title": "PRIVATE_ALIAS_A"},
        {"product_id": "alias-b", "account_id": "alice",
         "owned": True, "title": "PRIVATE_ALIAS_B"},
        {"product_id": "safe-edition", "account_id": "alice",
         "owned": True, "title": "Visible title"},
        {"id": "legacy-safe", "owned": True, "title": "Legacy"},
    ])
    session = session_with_vault(vault)
    try:
        first = session.library()
        assert [row.record_id for row in first] == [
            "safe-edition", "legacy-safe",
        ]
        assert all(row.owned is True for row in first)
        assert session.last_library_rejections == [
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
            {"type": "ProviderError", "reason": "missing_product_id"},
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
        ]
        exported = json.dumps(session.diagnostic_state()["library_rejections"])
        for secret in (
            "PRIVATE", "alice", "edition-1", "edition-2",
            "alias-a", "alias-b", "Bearer", "https:",
        ):
            assert secret not in exported
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0

        # A subsequent authoritative manifest resets the previous quarantine:
        # a formerly ambiguous edition may legitimately reappear alone.
        vault.manifest = [
            {"product_id": "edition-1", "account_id": "alice",
             "owned": True, "revision_id": "now-unique"},
        ]
        second = session.library()
        assert len(second) == 1
        assert second[0].record_id == "edition-1"
        assert second[0].revision_id == "now-unique"
        assert session.last_library_rejections == []
        assert vault.manifest_calls == 2 and vault.acquisition_calls == 0
    finally:
        session.close()


def test_library_same_account_identical_duplicate_is_still_ambiguous():
    raw = {"product_id": "same", "account_id": "alice",
           "owned": True, "downloadable": True}
    vault = FakeVault([raw, dict(raw), {"product_id": "independent",
                                      "account_id": "alice", "owned": True}])
    session = session_with_vault(vault)
    try:
        rows = session.library()
        assert [row.record_id for row in rows] == ["independent"]
        assert session.last_library_rejections == [
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
        ]
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0
    finally:
        session.close()