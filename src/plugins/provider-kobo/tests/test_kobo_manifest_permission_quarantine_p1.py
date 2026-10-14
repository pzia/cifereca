"""#1346: untrusted Kobo permission flags never reach Core backup materialization.

Provider-only fixtures, no Kobo account, HTTP, download or mutation.
"""
from __future__ import annotations

import json

import pytest

from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


@pytest.mark.parametrize("bad_flag", [
    {"owned": False},
    {"owned": "false"},
    {"owned": 0},
    {"owned": {}},
    {"downloadable": "false"},
    {"downloadable": 0},
    {"downloadable": []},
    {"archived": "false"},
    {"archived": 0},
    {"archived": []},
])
def test_invalid_personal_right_is_never_handed_to_legacy_core(bad_flag):
    vault = FakeVault([
        {"product_id": "PRIVATE_BAD", "title": "PRIVATE_TITLE", **bad_flag},
        {"product_id": "good-owned", "owned": True, "downloadable": True},
        # Optional flags omitted/null are a legacy inventory fact only.
        {"product_id": "good-legacy", "owned": None},
        {"product_id": "good-archived", "archived": True},
        {"product_id": "good-unavailable", "downloadable": False},
    ])
    session = session_with_vault(vault)
    try:
        rows = session.backup_manifest()
        assert [row["product_id"] for row in rows] == [
            "good-owned", "good-legacy", "good-archived", "good-unavailable",
        ]
        assert session.backup_manifest_rejection_count == 1
        policy = session.backup_manifest_policy
        assert policy["counts"]["invalid_permission_flags"] == 1
        assert policy["manifest_rejections"] == 1
        assert policy["manifest_authoritative"] is False
        assert policy["status"] == "incomplete"
        assert policy["acquisition_authorized"] is False
        assert policy["downloads_authorized"] is False
        assert vault.manifest_calls == 1
        assert vault.acquisition_calls == 0
        diagnostic = json.dumps(session.diagnostic_state())
        for secret in ("PRIVATE_BAD", "PRIVATE_TITLE", "PRIVATE_TOKEN"):
            assert secret not in diagnostic
    finally:
        session.close()


def test_bad_permission_quarantines_same_product_even_if_other_copy_looks_valid():
    vault = FakeVault([
        {"product_id": "PRIVATE_DUPLICATE", "owned": False},
        {"product_id": "PRIVATE_DUPLICATE", "owned": True,
         "downloadable": True, "revision_id": "PRIVATE_REVISION"},
        {"product_id": "safe", "owned": True, "downloadable": True},
    ])
    session = session_with_vault(vault)
    try:
        assert session.backup_manifest() == [
            {"product_id": "safe", "owned": True, "downloadable": True},
        ]
        assert session.backup_manifest_rejection_count == 2
        policy = session.backup_manifest_policy
        assert policy["counts"]["invalid_permission_flags"] == 1
        assert policy["counts"]["duplicate_product_id"] == 1
        assert policy["manifest_rejections"] == 2
        assert policy["manifest_authoritative"] is False
        assert policy["acquisition_authorized"] is False
        assert vault.acquisition_calls == 0
        assert "PRIVATE" not in json.dumps(
            session.diagnostic_state()["manifest_materialization_policy"]
        )
    finally:
        session.close()


def test_clean_followup_resets_quarantine_and_does_not_authorize_download():
    vault = FakeVault([
        {"product_id": "first", "downloadable": "false"},
        {"product_id": "unchanged", "downloadable": False},
    ])
    session = session_with_vault(vault)
    try:
        assert [row["product_id"] for row in session.backup_manifest()] == ["unchanged"]
        assert session.backup_manifest_rejection_count == 1
        assert session.backup_manifest_policy["status"] == "incomplete"
        vault.manifest = [
            {"product_id": "first", "downloadable": False},
            {"product_id": "unchanged", "downloadable": False},
        ]
        assert len(session.backup_manifest()) == 2
        assert session.backup_manifest_rejection_count == 0
        assert session.backup_manifest_policy["manifest_rejections"] == 0
        assert session.backup_manifest_policy["status"] == "classified"
        assert session.backup_manifest_policy["acquisition_authorized"] is False
        assert vault.manifest_calls == 2
        assert vault.acquisition_calls == 0
    finally:
        session.close()


def test_quarantine_scope_is_per_account_and_403_cannot_affect_other_accounts():
    vaults = [
        FakeVault([{"product_id": "good-1", "owned": True}]),
        FakeVault([{"product_id": "bad-2", "downloadable": "false"}]),
        FakeVault([{"product_id": "good-3", "owned": True}]),
    ]
    sessions = [
        session_with_vault(vault, account_id=f"account-{i}")
        for i, vault in enumerate(vaults, start=1)
    ]
    try:
        sessions[1]._cifereca_delegation_forbidden = True
        assert sessions[1].backup_manifest() == []
        assert [v.manifest_calls for v in vaults] == [0, 1, 0]
        assert sessions[1].backup_manifest_rejection_count == 1
        assert sessions[1].backup_manifest_policy["status"] == "incomplete"
        assert sessions[1].backup_manifest_policy["acquisition_authorized"] is False
        assert len(sessions[0].backup_manifest()) == 1
        assert len(sessions[2].backup_manifest()) == 1
        assert [v.manifest_calls for v in vaults] == [1, 1, 1]
        assert [v.acquisition_calls for v in vaults] == [0, 0, 0]
        assert sessions[0].backup_manifest_rejection_count == 0
        assert sessions[2].backup_manifest_rejection_count == 0
        assert sessions[1]._cifereca_delegation_forbidden is True
    finally:
        for session in sessions:
            session.close()
