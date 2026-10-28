"""P1 #1346: provider-owned Kobo candidate classification is advisory only.

All fixtures are synthetic, with no Kobo account, Vault HTTP or DRM runtime.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import Account
from cifereca_kobo import KoboSession, DESCRIPTOR
from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_policy import classify_personal_manifest


class QuietDiagnostics:
    def add(self, *args, **kwargs):
        raise AssertionError("No diagnostics need to be written by policy")


class FakeVault:
    base_url = "https://vault.example"
    def __init__(self, manifest, *, rejected=()):
        self.manifest = manifest
        self.last_manifest_rejections = list(rejected)
        self.manifest_calls = 0
        self.acquisition_calls = 0

    def library_manifest(self):
        self.manifest_calls += 1
        if isinstance(self.manifest, BaseException):
            raise self.manifest
        return self.manifest

    def acquisition(self, *args, **kwargs):
        self.acquisition_calls += 1
        raise AssertionError("A manifest policy may NEVER resolve an acquisition")

    def close(self):
        pass


def session_with_vault(vault, *, account_id="alice", connected=True):
    session = KoboSession(
        descriptor=DESCRIPTOR,
        credential_id="PRIVATE_VAULT_2", label="PRIVATE READER",
        credentials={"api_token": "Bearer PRIVATE_TOKEN"},
        options={"base_url": "https://vault.example"},
        config={}, storage={}, diagnostics=QuietDiagnostics(),
        runtime_capabilities=SimpleNamespace(acquisition=lambda _: None),
    )
    session.client.close()
    session.client = vault
    session.catalog = KoboCatalogService(
        vault, credential_id=session.credential_id,
        provider_id=session.provider_id,
    )
    session.connected = connected
    session._accounts = [Account(
        display_name="Private Reader", unique_id=account_id,
        provider_id="kobo", credential_id=session.credential_id,
    )]
    session.acquisition_access = "configured"
    return session


def test_personal_manifest_policy_is_opaque_and_never_authorizes_any_item():
    raw = [
        {"product_id": "owned-secret", "title": "PRIVATE_BOOK", "owned": True,
         "downloadable": True, "source_url": "https://PRIVATE/?token=PRIVATE"},
        {"product_id": "archived-secret", "archived": True},
        {"product_id": "not-downloadable", "downloadable": False},
        {"product_id": "invalid-boolean", "downloadable": "false"},
        {"product_id": "different-account", "account_id": "bob"},
        {"product_id": "ambiguous-product", "revision_id": "r1"},
        {"product_id": "ambiguous-product", "revision_id": "r2"},
        {"title": "no identity"},
        {"product_id": "not-owned", "owned": False},
        {"title": "bad-object", "account_id": "alice"},
        "PRIVATE_NON_OBJECT",
    ]
    result = classify_personal_manifest(raw, account_id="alice")
    assert result["classified_items"] == len(raw)
    assert result["counts"] == {
        "invalid_shape": 1,
        "missing_product_id": 2,
        "invalid_permission_flags": 2,
        "different_account": 1,
        "duplicate_product_id": 2,
        "archived_book": 1,
        "not_downloadable": 1,
        "blocked_scope": 0,
        "candidate_unverified": 1,
    }
    assert result["status"] == "incomplete"
    assert result["manifest_authoritative"] is False
    for key in ("acquisition_authorized", "downloads_authorized", "deletion_authorized"):
        assert result[key] is False
    for secret in (
        "owned-secret", "archived-secret", "ambiguous-product",
        "PRIVATE", "bob", "alice", "source_url", "r1", "Bearer",
    ):
        assert secret not in json.dumps(result)


@pytest.mark.parametrize("item,category", [
    ({"product_id": "p", "owned": "false"}, "invalid_permission_flags"),
    ({"product_id": "p", "archived": 1}, "invalid_permission_flags"),
    ({"product_id": "p", "downloadable": []}, "invalid_permission_flags"),
    ({"product_id": "p", "owned": False}, "invalid_permission_flags"),
    ({"product_id": "p", "account_id": {}}, "different_account"),
    ({"product_id": "p", "account_id": "other"}, "different_account"),
    ({"product_id": "p", "archived": True, "downloadable": True}, "archived_book"),
    ({"product_id": "p", "downloadable": False}, "not_downloadable"),
    ({"product_id": "p"}, "candidate_unverified"),
])
def test_manifest_item_flags_are_strict(item, category):
    data = classify_personal_manifest([item], account_id="alice")
    assert data["counts"][category] == 1
    assert sum(data["counts"].values()) == 1
    assert data["acquisition_authorized"] is False


@pytest.mark.parametrize("access", [
    "denied", "forbidden", "requires_scope", "disabled", "unavailable",
])
def test_delegation_denial_blocks_unverified_candidate_without_new_request(access):
    vault = FakeVault([{"product_id": "owned", "title": "PRIVATE"}])
    session = session_with_vault(vault)
    try:
        session.acquisition_access = access
        assert len(session.backup_manifest()) == 1
        data = session.backup_manifest_policy
        assert data["status"] == "blocked_scope"
        assert data["counts"]["blocked_scope"] == 1
        assert data["counts"]["candidate_unverified"] == 0
        assert vault.manifest_calls == 1
        assert vault.acquisition_calls == 0
    finally:
        session.close()


def test_persisted_403_denial_precedes_configured_vault_access():
    vault = FakeVault([{"product_id": "owned"}])
    session = session_with_vault(vault)
    try:
        session._cifereca_delegation_forbidden = True
        session.backup_manifest()
        assert session.backup_manifest_policy["status"] == "blocked_scope"
        assert session.backup_manifest_policy["counts"]["blocked_scope"] == 1
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0
    finally:
        session.close()


def test_manifest_rejections_make_even_the_valid_rows_non_authoritative():
    vault = FakeVault(
        [{"product_id": "owned", "downloadable": True}],
        rejected=[{"private": "PRIVATE_SECRET", "reason": "invalid_shape"}],
    )
    session = session_with_vault(vault)
    try:
        result = session.backup_manifest()
        assert len(result) == 1
        assert session.backup_manifest_rejection_count == 1
        policy = session.backup_manifest_policy
        assert policy["manifest_rejections"] == 1
        assert not policy["manifest_authoritative"]
        assert policy["status"] == "incomplete"
        diag = session.diagnostic_state()["manifest_materialization_policy"]
        assert diag["counts"]["candidate_unverified"] == 1
        assert diag["acquisition_authorized"] is False
        for private in ("PRIVATE", "vault.example", "owned", "Bearer"):
            assert private not in json.dumps(diag)
    finally:
        session.close()


def test_no_connected_account_never_publishes_an_authoritative_phase():
    session = session_with_vault(FakeVault([{"product_id": "x"}]), connected=False)
    try:
        assert session.backup_manifest() == []
        assert session.client.manifest_calls == 0
        data = session.backup_manifest_policy
        assert data["status"] == "invalid_scope"
        assert data["manifest_authoritative"] is False
        assert data["classified_items"] == 0
    finally:
        session.close()


def test_failed_followup_manifest_invalidates_previous_projection():
    vault = FakeVault([{"product_id": "x"}])
    session = session_with_vault(vault)
    try:
        session.backup_manifest()
        assert session.backup_manifest_policy["status"] == "classified"
        vault.manifest = RuntimeError("PRIVATE_TOKEN https://secret.example/book")
        with pytest.raises(RuntimeError):
            session.backup_manifest()
        assert session.backup_manifest_policy["status"] == "invalid_scope"
        assert session.backup_manifest_policy["classified_items"] == 0
        assert "PRIVATE" not in json.dumps(session.diagnostic_state()["manifest_materialization_policy"])
    finally:
        session.close()


def test_truncated_or_corrupted_manifest_never_implies_full_coverage():
    rows = [{"product_id": f"item-{i}"} for i in range(4100)]
    report = classify_personal_manifest(rows, account_id="alice")
    assert report["classified_items"] == 4096
    assert report["truncated"] is True
    assert report["status"] == "incomplete"
    assert not report["manifest_authoritative"]
    assert report["acquisition_authorized"] is False
    invalid = classify_personal_manifest("PRIVATE_NOT_LIST", account_id="alice")
    assert invalid["status"] == "invalid_manifest"
    assert invalid["classified_items"] == 0
    assert "PRIVATE" not in json.dumps(invalid)


def test_account_scope_and_plugin_code_has_no_core_private_imports():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    policy = (root / "cifereca_kobo_policy.py").read_text("utf-8")
    provider = (root / "cifereca_kobo.py").read_text("utf-8")
    assert "from cifereca.services import" not in policy + provider
    assert "from cifereca.storage import" not in policy + provider
    assert "from cifereca.core." not in policy + provider
    assert "classify_personal_manifest(" in provider
    # The real Vault pages now require a scoped callback before/after
    # every private GET; injected/legacy Vaults retain the zero-arg API.
    assert "vault.library_manifest(page_guard=check)" in provider
    assert "return vault.library_manifest()" in provider
    assert "self._scoped_personal_manifest(" in provider


def test_three_accounts_9_522_5_are_isolated_and_403_never_fans_out():
    """P1 #1346: a provider policy never reads or retries another account."""
    sizes = (9, 522, 5)
    vaults = [
        FakeVault([{"product_id": f"item-{i}", "owned": True} for i in range(size)])
        for size in sizes
    ]
    sessions = [
        session_with_vault(vault, account_id=f"reader-{i}")
        for i, vault in enumerate(vaults, start=1)
    ]
    try:
        # Preview/account 2 only; no implicit batch or delegated GET on 1/3.
        sessions[1]._cifereca_delegation_forbidden = True
        result = sessions[1].backup_manifest()
        assert len(result) == 522
        assert [v.manifest_calls for v in vaults] == [0, 1, 0]
        assert [v.acquisition_calls for v in vaults] == [0, 0, 0]
        assert sessions[1].backup_manifest_policy["counts"]["blocked_scope"] == 522
        assert sessions[1].backup_manifest_policy["status"] == "blocked_scope"
        # Access to the second source cannot publish a policy for the others.
        assert sessions[0].backup_manifest_policy["status"] == "invalid_scope"
        assert sessions[2].backup_manifest_policy["status"] == "invalid_scope"

        # An explicit subsequent manifest request to each other account is
        # independent; it cannot clear account 2's persisted scope denial.
        assert len(sessions[0].backup_manifest()) == 9
        assert len(sessions[2].backup_manifest()) == 5
        assert [v.manifest_calls for v in vaults] == [1, 1, 1]
        assert sessions[0].backup_manifest_policy["counts"]["candidate_unverified"] == 9
        assert sessions[2].backup_manifest_policy["counts"]["candidate_unverified"] == 5
        assert sessions[1].backup_manifest_policy["counts"]["blocked_scope"] == 522
        assert all(
            not session.backup_manifest_policy["acquisition_authorized"]
            for session in sessions
        )
        public = json.dumps([session.backup_manifest_policy for session in sessions])
        for private in ("PRIVATE", "reader-1", "reader-2", "reader-3"):
            assert private not in public
    finally:
        for session in sessions:
            session.close()


def test_manifest_with_an_explicit_foreign_account_never_becomes_a_candidate():
    vault = FakeVault([{
        "product_id": "private-book", "account_id": "reader-other",
        "owned": True, "downloadable": True,
    }])
    session = session_with_vault(vault, account_id="reader-selected")
    try:
        session.backup_manifest()
        report = session.backup_manifest_policy
        assert report["counts"]["different_account"] == 1
        assert report["counts"]["candidate_unverified"] == 0
        assert not report["manifest_authoritative"]
        assert not report["acquisition_authorized"]
        assert "reader-other" not in json.dumps(report)
    finally:
        session.close()


@pytest.mark.parametrize("ambiguous_row", [
    {"product_id": "real-edition", "id": "other-edition"},
    {"product_id": 0, "id": "real-edition"},
    {"product_id": False, "id": "real-edition"},
    {"product_id": [], "id": "real-edition"},
    {"product_id": {}, "id": "real-edition"},
    {"product_id": "edition-a", "id": False},
    {"id": True},
    {"id": "edition-1\nBearer PRIVATE_TOKEN"},
    {"id": "edition-1\rPRIVATE_TOKEN"},
    {"product_id": "edition-1\u202ePRIVATE_BIDI"},
    {"product_id": "edition-1\u2066PRIVATE_ISOLATE"},
    {"product_id": " edition-1"},
    {"product_id": "edition-1 "},
    {"product_id": "edition 1"},
    {"product_id": "e" * 257},
])
def test_conflicting_or_untrusted_product_identity_is_not_a_candidate(ambiguous_row):
    result = classify_personal_manifest(
        [{**ambiguous_row, "owned": True, "downloadable": True}],
        account_id="alice",
    )
    assert result["counts"]["missing_product_id"] == 1
    assert result["counts"]["candidate_unverified"] == 0
    assert result["classified_items"] == 1
    assert result["status"] == "incomplete"
    assert result["manifest_authoritative"] is False
    assert result["acquisition_authorized"] is False
    for private in ("PRIVATE", "Bearer", "edition", "alice"):
        assert private not in json.dumps(result)


@pytest.mark.parametrize("row", [
    {"product_id": "edition-1", "id": "edition-1"},
    {"product_id": "edition-1"},
    {"id": "edition-1"},
    {"product_id": "", "id": "edition-1"},
    {"product_id": None, "id": "edition-1"},
    {"product_id": "édition-1", "id": "édition-1"},
])
def test_legitimate_single_or_matching_legacy_product_ids_are_retained(row):
    result = classify_personal_manifest([row], account_id="alice")
    assert result["counts"]["candidate_unverified"] == 1
    assert result["counts"]["missing_product_id"] == 0
    assert result["manifest_authoritative"] is True
    assert result["acquisition_authorized"] is False


def test_duplicates_are_detected_across_legacy_id_and_product_id_shapes():
    result = classify_personal_manifest([
        {"id": "one-edition"},
        {"id": "one-edition", "product_id": "one-edition"},
        {"product_id": "another-edition", "id": "OTHER-PRIVATE-EDITION"},
    ], account_id="alice")
    assert result["counts"]["duplicate_product_id"] == 2
    assert result["counts"]["missing_product_id"] == 1
    assert result["counts"]["candidate_unverified"] == 0
    assert result["manifest_authoritative"] is False
    assert result["acquisition_authorized"] is False
    for private in ("one-edition", "another-edition", "OTHER-PRIVATE-EDITION"):
        assert private not in json.dumps(result)


@pytest.mark.parametrize("invalid_account", [
    "", " ", "alice bob", "alice\nPRIVATE", "alice\u202ePRIVATE",
    "alice\u2066PRIVATE", "a" * 257, True, 123, [], {},
])
def test_direct_manifest_policy_requires_an_attested_account_scope(invalid_account):
    result = classify_personal_manifest(
        [{"product_id": "edition-1", "account_id": invalid_account}],
        account_id=invalid_account,
    )
    assert result["status"] == "invalid_scope"
    assert result["classified_items"] == 0
    assert result["manifest_authoritative"] is False
    assert result["acquisition_authorized"] is False
    assert "PRIVATE" not in json.dumps(result)


def test_valid_account_scope_still_supports_uuid_and_provider_namespaces():
    result = classify_personal_manifest([
        {"product_id": "édition-1", "account_id": "urn:uuid:abc-def"},
    ], account_id="urn:uuid:abc-def")
    assert result["counts"]["candidate_unverified"] == 1
    assert result["manifest_authoritative"] is True
    assert result["acquisition_authorized"] is False


@pytest.mark.parametrize("rejections", (
    ["Bearer PRIVATE_TOKEN"],
    [None, "https://private.example/?token=SECRET"],
    {"error": "PRIVATE_TOKEN"},
    "Bearer PRIVATE_TOKEN",
    None,
))
def test_malformed_upstream_rejection_envelope_never_marks_manifest_authoritative(rejections):
    """#1346: partial rights evidence cannot be erased by an invalid envelope."""
    vault = FakeVault([{"product_id": "owned", "downloadable": True}])
    vault.last_manifest_rejections = rejections
    session = session_with_vault(vault)
    try:
        # Read-only inventory is still available; no acquisition is requested.
        assert len(session.backup_manifest()) == 1
        assert vault.manifest_calls == 1 and vault.acquisition_calls == 0
        expected_count = len(rejections) if type(rejections) in (list, tuple) else 1
        assert session.backup_manifest_rejection_count == expected_count
        policy = session.backup_manifest_policy
        assert policy["manifest_rejections"] == expected_count
        assert policy["status"] == "incomplete"
        assert policy["manifest_authoritative"] is False
        assert policy["acquisition_authorized"] is False
        published = json.dumps(session.diagnostic_state()["manifest_materialization_policy"])
        for secret in ("PRIVATE_TOKEN", "SECRET", "Bearer", "private.example", "owned"):
            assert secret not in published
    finally:
        session.close()


def test_clean_empty_rejection_envelope_remains_authoritative():
    vault = FakeVault([{"product_id": "owned", "downloadable": True}])
    session = session_with_vault(vault)
    try:
        assert len(session.backup_manifest()) == 1
        assert session.backup_manifest_rejection_count == 0
        assert session.backup_manifest_policy["manifest_authoritative"] is True
        assert vault.acquisition_calls == 0
    finally:
        session.close()
