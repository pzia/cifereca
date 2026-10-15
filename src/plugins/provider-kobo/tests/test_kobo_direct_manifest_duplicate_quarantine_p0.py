"""#1346/#1345: provider handoff rejects duplicates even with an unfiltered Vault.

All fixtures are synthetic: no Kobo account, network, DRM, or local download.
"""
from __future__ import annotations

import json

import pytest

from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


@pytest.mark.parametrize("duplicates", (
    (
        {"product_id": "PRIVATE_EDITION", "revision_id": "revision-1",
         "owned": True, "downloadable": True},
        {"product_id": "PRIVATE_EDITION", "revision_id": "revision-2",
         "owned": True, "downloadable": True},
    ),
    (
        {"product_id": "PRIVATE_EDITION", "owned": True},
        {"id": "PRIVATE_EDITION", "owned": True},
    ),
    (
        {"product_id": "PRIVATE_EDITION", "id": "PRIVATE_EDITION"},
        {"id": "PRIVATE_EDITION"},
    ),
    (
        {"product_id": "PRIVATE_EDITION", "owned": True},
        {"product_id": "PRIVATE_EDITION", "owned": True},
    ),
))
def test_syntactically_valid_duplicate_editions_never_reach_core_handoff(duplicates):
    vault = FakeVault([
        {"product_id": "before", "owned": True, "downloadable": True},
        *duplicates,
        {"product_id": "after", "owned": True, "downloadable": True},
    ])
    session = session_with_vault(vault)
    try:
        rows = session.backup_manifest()
        assert [row["product_id"] for row in rows] == ["before", "after"]
        assert session.backup_manifest_rejection_count == 2
        policy = session.backup_manifest_policy
        assert policy["counts"]["duplicate_product_id"] == 2
        assert policy["manifest_rejections"] == 2
        assert policy["status"] == "incomplete"
        assert policy["manifest_authoritative"] is False
        assert policy["acquisition_authorized"] is False
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0
        for private in ("PRIVATE", "revision-1", "revision-2", "Bearer"):
            assert private not in json.dumps(
                session.diagnostic_state()["manifest_materialization_policy"]
            )
    finally:
        session.close()


@pytest.mark.parametrize("poisoned_alias,expected", (
    ({"product_id": "PRIVATE_A", "id": "PRIVATE_B"}, ["safe"]),
    ({"product_id": False, "id": "PRIVATE_B"}, ["PRIVATE_A", "safe"]),
    ({"product_id": "PRIVATE_A", "id": []}, ["PRIVATE_B", "safe"]),
    ({"product_id": "PRIVATE_A", "id": "PRIVATE_A\\nBearer PRIVATE"}, ["PRIVATE_B", "safe"]),
    ({"product_id": [], "id": "PRIVATE_B"}, ["PRIVATE_A", "safe"]),
))
def test_conflicting_or_corrupt_legacy_aliases_poison_valid_siblings(poisoned_alias, expected):
    vault = FakeVault([
        {"product_id": "PRIVATE_A", "owned": True, "downloadable": True},
        {"product_id": "PRIVATE_B", "owned": True, "downloadable": True},
        poisoned_alias,
        {"product_id": "safe", "owned": True, "downloadable": True},
    ])
    session = session_with_vault(vault)
    try:
        rows = session.backup_manifest()
        assert [row["product_id"] for row in rows] == expected
        assert session.backup_manifest_rejection_count == 4 - len(expected)
        assert not session.backup_manifest_policy["manifest_authoritative"]
        assert session.backup_manifest_policy["acquisition_authorized"] is False
        assert "PRIVATE" not in json.dumps(
            session.diagnostic_state()["manifest_materialization_policy"]
        )
        assert vault.acquisition_calls == 0
    finally:
        session.close()


def test_one_accounts_duplicate_snapshot_does_not_quarantine_other_accounts():
    vaults = [
        FakeVault([{"product_id": "edition", "owned": True}]),
        FakeVault([
            {"product_id": "edition", "owned": True},
            {"id": "edition", "owned": True},
        ]),
        FakeVault([{"product_id": "edition", "owned": True}]),
    ]
    sessions = [
        session_with_vault(v, account_id=f"reader-{i}")
        for i, v in enumerate(vaults)
    ]
    try:
        assert sessions[1].backup_manifest() == []
        assert sessions[1].backup_manifest_rejection_count == 2
        assert [v.manifest_calls for v in vaults] == [0, 1, 0]
        assert len(sessions[0].backup_manifest()) == 1
        assert len(sessions[2].backup_manifest()) == 1
        assert [v.manifest_calls for v in vaults] == [1, 1, 1]
        assert all(v.acquisition_calls == 0 for v in vaults)
    finally:
        for session in sessions:
            session.close()


def test_clean_repeat_resets_duplicate_quarantine_without_authorizing_download():
    vault = FakeVault([
        {"product_id": "edition", "revision_id": "r1"},
        {"product_id": "edition", "revision_id": "r2"},
    ])
    session = session_with_vault(vault)
    try:
        assert session.backup_manifest() == []
        assert session.backup_manifest_rejection_count == 2
        vault.manifest = [{"product_id": "edition", "revision_id": "r2"}]
        assert session.backup_manifest() == vault.manifest
        assert session.backup_manifest_rejection_count == 0
        assert session.backup_manifest_policy["status"] == "classified"
        assert session.backup_manifest_policy["acquisition_authorized"] is False
    finally:
        session.close()
