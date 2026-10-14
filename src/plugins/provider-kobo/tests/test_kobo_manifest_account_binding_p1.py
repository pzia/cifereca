"""#1346: a Kobo manifest row claiming a foreign account never reaches Core.

No live Vault requests, local books, DRM, download, or storage writes.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from cifereca.plugin_api import Account
from cifereca_kobo import DESCRIPTOR, KoboSession


class FakeVault:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.last_manifest_rejections = []

    def library_manifest(self):
        self.calls += 1
        return list(self.rows)

    def acquisition(self, *_args, **_kwargs):
        raise AssertionError("Never resolve an acquisition during manifest validation")

    def close(self):
        pass


def _session(vault, *, account="alice", credential="PRIVATE_CREDENTIAL"):
    session = KoboSession(
        descriptor=DESCRIPTOR, credential_id=credential,
        label="PRIVATE_READER", credentials={"api_token": "PRIVATE_TOKEN"},
        options={"base_url": "https://vault.example"},
        config={}, storage={}, diagnostics=NS(add=lambda *_a, **_kw: None),
        runtime_capabilities=NS(acquisition=lambda *_a: None),
    )
    session.client.close()
    session.client = vault
    session.catalog = NS(cover_url=lambda _item: None)
    session.connected = True
    session._accounts = [
        Account(
            unique_id=account, display_name="Private reader",
            provider_id="kobo", credential_id=credential,
        )
    ]
    return session


def test_foreign_account_rows_are_quarantined_before_materialization_and_counted():
    upstream = [
        {"product_id": "own-1", "account_id": "alice"},
        {"product_id": "legacy-2"},  # no explicit account in old Vault
        {"product_id": "PRIVATE_FOREIGN", "account_id": "bob"},
        {"product_id": "PRIVATE_NULL", "account_id": None},
        {"product_id": "PRIVATE_FALSE", "account_id": False},
        {"product_id": "PRIVATE_DICT", "account_id": {"id": "alice"}},
    ]
    vault = FakeVault(upstream)
    session = _session(vault)
    try:
        preview = session.backup_manifest()
        assert [row["product_id"] for row in preview] == ["own-1", "legacy-2"]
        assert session.backup_manifest_rejection_count == 4
        policy = session.backup_manifest_policy
        assert policy["status"] == "incomplete"
        assert policy["manifest_authoritative"] is False
        assert policy["counts"]["different_account"] == 4
        assert policy["counts"]["candidate_unverified"] == 2
        assert policy["manifest_rejections"] == 4
        assert policy["acquisition_authorized"] is False
        assert vault.calls == 1  # no hidden refresh or account lookup
        public = json.dumps(session.diagnostic_state()["manifest_materialization_policy"])
        for secret in ("PRIVATE_FOREIGN", "PRIVATE_NULL", "PRIVATE_DICT", "bob", "vault.example"):
            assert secret not in public
    finally:
        session.close()


def test_rejected_account_rows_do_not_poison_later_authenticated_snapshot():
    vault = FakeVault([
        {"product_id": "PRIVATE_OTHER", "account_id": "other"},
        {"product_id": "own", "account_id": "alice"},
    ])
    session = _session(vault)
    try:
        assert [r["product_id"] for r in session.backup_manifest()] == ["own"]
        assert session.backup_manifest_rejection_count == 1
        assert not session.backup_manifest_policy["manifest_authoritative"]
        vault.rows = [{"product_id": "own", "account_id": "alice"}]
        assert [r["product_id"] for r in session.backup_manifest()] == ["own"]
        assert session.backup_manifest_rejection_count == 0
        assert session.backup_manifest_policy["manifest_rejections"] == 0
        assert session.backup_manifest_policy["manifest_authoritative"] is True
        assert session.backup_manifest_policy["acquisition_authorized"] is False
        assert vault.calls == 2
    finally:
        session.close()


def test_three_accounts_9_522_5_honor_selected_source_and_refuse_foreign_rows():
    sizes = [9, 522, 5]
    vaults = []
    sessions = []
    for index, count in enumerate(sizes, start=1):
        identity = f"reader-{index}"
        rows = [{"product_id": f"book-{number}", "account_id": identity}
                for number in range(count)]
        if index == 2:
            rows[117]["account_id"] = "reader-1"  # foreign book at resume checkpoint
        vault = FakeVault(rows)
        vaults.append(vault)
        sessions.append(_session(
            vault, account=identity, credential=f"credential-{index}",
        ))
    try:
        selected = sessions[1].backup_manifest()
        assert len(selected) == 521
        assert [v.calls for v in vaults] == [0, 1, 0]
        assert sessions[1].backup_manifest_rejection_count == 1
        assert sessions[1].backup_manifest_policy["counts"]["different_account"] == 1
        assert sessions[1].backup_manifest_policy["manifest_authoritative"] is False
        assert [v.calls for v in vaults] == [0, 1, 0]
        assert len(sessions[0].backup_manifest()) == 9
        assert len(sessions[2].backup_manifest()) == 5
        assert [v.calls for v in vaults] == [1, 1, 1]
        assert sessions[0].backup_manifest_policy["manifest_authoritative"] is True
        assert sessions[2].backup_manifest_policy["manifest_authoritative"] is True
        assert all(
            session.backup_manifest_policy["acquisition_authorized"] is False
            for session in sessions
        )
    finally:
        for session in sessions:
            session.close()


def test_upstream_rejection_count_and_foreign_account_count_accumulate_safely():
    vault = FakeVault([
        {"product_id": "own", "account_id": "alice"},
        {"product_id": "foreign", "account_id": "different-account"},
    ])
    vault.last_manifest_rejections = [
        {"type": "invalid_shape", "reason": "item_not_object"},
        {"type": "invalid_shape", "reason": "unverifiable_product_id"},
    ]
    session = _session(vault)
    try:
        assert [row["product_id"] for row in session.backup_manifest()] == ["own"]
        assert session.backup_manifest_rejection_count == 3
        assert session.backup_manifest_policy["manifest_rejections"] == 3
        assert session.backup_manifest_policy["status"] == "incomplete"
        assert "different-account" not in repr(session.backup_manifest_policy)
    finally:
        session.close()


def test_foreign_product_collision_quarantines_both_versions_before_core():
    """A foreign duplicate must not disappear and make the local row 'unique'."""
    vault = FakeVault([
        {"product_id": "same-book", "account_id": "alice", "revision_id": "r1"},
        {"id": "same-book", "account_id": "bob", "revision_id": "r2"},
        {"product_id": "independent", "account_id": "alice"},
    ])
    session = _session(vault)
    try:
        assert [r["product_id"] for r in session.backup_manifest()] == ["independent"]
        policy = session.backup_manifest_policy
        assert policy["counts"]["duplicate_product_id"] == 2
        assert policy["manifest_authoritative"] is False
        assert session.backup_manifest_rejection_count == 2
        assert policy["manifest_rejections"] == 2
        assert policy["acquisition_authorized"] is False
        assert vault.calls == 1
        assert "same-book" not in json.dumps(policy)
        assert "bob" not in json.dumps(policy)
    finally:
        session.close()
