"""#4858 W2/D3: confirmed CLI Kobo connection binds exact existing SQL borrower.

A provider-only connect does NOT register a Core Account. Without an explicit
scoped Core activation, the new canonical GET CLI routes could never admit A.
This suite uses real LibraryService, real v14 SQLite, and a synthetic Kobo
session. No remote calls, new entitlements, backup writes or source creations.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core import provider_sessions
from cifereca.core.rights_snapshot_read import read_account_rights_snapshot
from cifereca.core.canonical_delivery_abi import has_complete_delivery_abi
from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _service

STAMP = "2026-11-07T08:00:00+00:00"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    service = _service(tmp_path)
    service.config.set("providers.accounts", [{
        "provider": "kobo", "id": "vault-1", "identity_id": "alice",
        "label": "Synthetic Kobo", "enabled": True, "credentials": {},
    }])
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session._accounts[0].identity_id = "default"
    # V1 complete callable ABI without invoking any of these methods on
    # a merely selected, not yet admitted, Kobo account.
    for method in (
        "rights_observation_snapshot",
        "rights_selected_delivery_offer_snapshot",
        "rights_selected_delivery_candidate",
    ):
        monkeypatch.setattr(
            session, method, lambda *_a, **_kw: pytest.fail(
                "targeted bind cannot query remote A or acquire content",
            ), raising=False,
        )
    assert has_complete_delivery_abi(session)
    monkeypatch.setattr(
        provider_sessions, "connect_kobo_preview",
        lambda got, credential: (
            session if got is service and credential == "vault-1"
            else pytest.fail("only exact Kobo credential can be connected")
        ),
    )
    try:
        yield service, session
    finally:
        service.close()


def test_targeted_bind_existing_sql_borrower_is_in_memory_only(rig):
    service, session = rig
    assert not any(a.provider_id == "kobo" for a in service.accounts)
    assert session.accounts[0].identity_id == "default"
    before = (
        service.storage.provider_connections(identity_id="alice"),
        service.storage.provider_accounts(),
        service.storage.backup_list(),
        service.storage.placements(),
        service.storage.entitlements(),
    )
    previous_epoch = service._source_context_epoch
    assert service.connect_kobo_canonical("vault-1") is session
    assert session.accounts[0].identity_id == "alice"
    assert service.accounts[-1] is session.accounts[0]
    assert service.accounts[-1].unique_id == "account"
    assert service._source_context_epoch == previous_epoch + 1
    assert (
        service.storage.provider_connections(identity_id="alice"),
        service.storage.provider_accounts(),
        service.storage.backup_list(),
        service.storage.placements(),
        service.storage.entitlements(),
    ) == before
    assert service.connect_kobo_canonical("vault-1") is session
    assert len([a for a in service.accounts if a.provider_id == "kobo"]) == 1


def test_targeted_bound_account_passes_generic_canonical_A_read(rig, monkeypatch):
    service, session = rig
    selected = []
    def read(account):
        selected.append(account)
        scope = ProviderRightsScope(
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )
        return ProviderRightsSnapshot(
            scope, (
                ProviderRightObservation(
                    "edition-1", "right-1", "purchase", "active",
                    "provider_response", "synthetic-account-proof",
                    STAMP, "unknown",
                ),
            ), status="partial", coverage="partial", branch="purchases",
        )
    monkeypatch.setattr(session, "rights_observation_snapshot", read)
    monkeypatch.setattr(
        service, "sync_source_enabled", lambda row: (
            row is service.accounts[-1] and row.provider_id == "kobo"
        ),
    )
    service.connect_kobo_canonical("vault-1")
    result = read_account_rights_snapshot(service, service.accounts[-1])
    assert result.status == "partial"
    assert len(result.rights) == 1
    assert result.scope.identity_id == "alice"
    assert selected == [service.accounts[-1]]
    assert service.storage.backup_list() == []
    assert service.storage.placements() == []


@pytest.mark.parametrize("tamper", [
    "disabled_or_rebound_sql", "foreign_live_profile", "foreign_borrower",
    "duplicate_registered_account", "changed_session_during_admission",
    "inplace_borrower_change_during_sql", "config_owner_reassigned",
])
def test_targeted_bind_refuses_ambiguous_or_unowned_source_without_core_mutation(
    rig, monkeypatch, tamper,
):
    service, session = rig
    original_epoch = service._source_context_epoch
    if tamper == "disabled_or_rebound_sql":
        monkeypatch.setattr(
            service.storage, "provider_scope_account_bound",
            lambda **_kw: False,
        )
    elif tamper == "foreign_live_profile":
        session._accounts[0].identity_id = "bob"
    elif tamper == "foreign_borrower":
        session._accounts[0].unique_id = "other"
    elif tamper == "duplicate_registered_account":
        service.accounts += [
            replace(session.accounts[0], identity_id="alice"),
            replace(session.accounts[0], identity_id="alice"),
        ]
    elif tamper == "config_owner_reassigned":
        # The provider is still on the same credential, but the profile
        # configuration now explicitly points to B while SQL still points A.
        # Neither owner may inherit the other's old source-wide consent.
        service.config.set("providers.accounts", [{
            "provider": "kobo", "id": "vault-1", "identity_id": "bob",
            "label": "Synthetic Kobo", "enabled": True, "credentials": {},
        }])
    else:
        original = service.storage.provider_scope_account_bound
        def switched(**kw):
            valid = original(**kw)
            if tamper == "changed_session_during_admission":
                session._accounts = [replace(session.accounts[0])]
            else:
                # Same object, but different borrower in-place after the
                # checked SQL snapshot: its earlier value must not authorize
                # a new Core registration or provider GET.
                session._accounts[0].unique_id = "rebound"
            return valid
        monkeypatch.setattr(service.storage, "provider_scope_account_bound", switched)
    before = tuple(service.accounts)
    with pytest.raises(ProviderConfigurationError, match="kobo_canonical_account_"):
        service.connect_kobo_canonical("vault-1")
    assert tuple(service.accounts) == before
    assert service._source_context_epoch == original_epoch
    assert session.accounts[0].identity_id != "alice" or tamper == "duplicate_registered_account"
    assert service.storage.backup_list() == []


def test_partial_abi_connect_preserves_existing_kobo_legacy_behavior(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    assert not has_complete_delivery_abi(session)
    monkeypatch.setattr(
        provider_sessions, "connect_kobo_preview",
        lambda _service, _credential: session,
    )
    original = tuple(service.accounts)
    epoch = service._source_context_epoch
    try:
        assert service.connect_kobo_canonical("vault-1") is session
        assert tuple(service.accounts) == original
        assert service._source_context_epoch == epoch
    finally:
        service.close()


def test_confirmed_resume_cli_from_local_only_core_reaches_canonical_dispatch(
    rig, monkeypatch, capsys,
):
    """Real v14 Core registry/dispatcher, mocked B executor; no provider GET."""
    import json
    from types import SimpleNamespace
    from cifereca import cli
    from cifereca.core.source_sync_consent import policy_key, scope_for_account

    service, session = rig
    # Only the local account is registered before the confirmed CLI call.
    assert not any(row.provider_id == "kobo" for row in service.accounts)
    assert session.accounts[0].identity_id == "default"
    owner_before = service.primary
    initial_accounts = tuple(service.accounts)
    calls = []

    def sync(account, control=None):
        calls.append(account)
        assert account is service.accounts[-1]
        assert account.identity_id == "alice"
        return {
            "provider": "kobo", "account_id": account.unique_id,
            "status": "success",
            "canonical_execution": {
                "downloaded": 0, "recovered": 0, "present_attested": 1,
                "pending": 0, "blocked": 0, "failed": 0,
                "cancelled": 0, "skipped_archived": 0, "inactive": 0,
            },
        }

    monkeypatch.setattr(service, "sync_source_enabled", lambda row: row.provider_id == "kobo")
    monkeypatch.setattr(service, "synchronize_account_source", sync)
    # Configure an explicit, revocable source-wide ON for the exact borrower.
    account = session.accounts[0]
    account_scope = ProviderRightsScope("alice", "kobo", "vault-1", account.unique_id)
    service.storage.metadata_set(
        policy_key(account_scope),
        json.dumps({
            "schema": "cifereca.source-sync-consent/1",
            "scope": ["alice", "kobo", "vault-1", account.unique_id],
            "enabled": True, "revision": "a" * 32,
        }),
    )
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_kw: SimpleNamespace(library=service, close=lambda: None),
    )
    result = cli.main([
        "kobo-resume", "--credential", "vault-1", "--confirm-downloads",
    ])
    parsed = json.loads(capsys.readouterr().out)
    assert result == 0 and parsed["status"] == "complete"
    assert parsed["downloaded"] == 0
    assert calls == [service.accounts[-1]]
    assert tuple(service.accounts[:len(initial_accounts)]) == initial_accounts
    assert service.primary is owner_before
    assert service.current_account is owner_before
    assert service.storage.backup_list() == []
    assert service.storage.placements() == []


def test_cli_refreshed_preview_from_local_only_core_reads_typed_A_without_B_get(
    rig, monkeypatch, capsys,
):
    """Real v14 Core, complete Kobo ABI and scoped inventory; no consent to GET."""
    import json
    from types import SimpleNamespace
    from cifereca import cli

    service, session = rig
    local = service.primary
    before = (
        tuple(service.accounts), service.storage.provider_accounts(),
        service.storage.provider_connections(identity_id="alice"),
        service.storage.placements(), service.storage.entitlements(),
    )
    reads = []
    def inventory(account):
        reads.append(account)
        assert account.identity_id == "alice"
        return ProviderRightsSnapshot(
            ProviderRightsScope("alice", "kobo", "vault-1", account.unique_id),
            (), status="partial", coverage="partial", branch="purchases",
        )

    monkeypatch.setattr(session, "rights_observation_snapshot", inventory)
    monkeypatch.setattr(service, "sync_source_enabled", lambda row: row.provider_id == "kobo")
    monkeypatch.setattr(
        service, "synchronize_account_source",
        lambda *_a, **_kw: pytest.fail("preview cannot execute canonical B"),
    )
    monkeypatch.setattr(
        service.storage, "backup_list",
        lambda *_a, **_kw: pytest.fail("modern D1 preview cannot read v0.7 backup"),
    )
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_kw: SimpleNamespace(library=service, close=lambda: None),
    )
    rc = cli.main([
        "kobo-resume-preview", "--credential", "vault-1",
        "--refresh-manifest",
    ])
    public = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert public["canonical_source"] is True
    assert public["manifest_refreshed"] is True
    assert public["preview_only"] is True
    assert public["acquisition_authorized"] is False
    assert public["manifest_items"] == 0
    assert len(reads) == 1 and reads[0] is service.accounts[-1]
    assert service.primary is local
    assert service.current_account is local
    assert tuple(service.accounts[:len(before[0])]) == before[0]
    assert service.storage.provider_accounts() == before[1]
    assert service.storage.provider_connections(identity_id="alice") == before[2]
    assert service.storage.placements() == before[3]
    assert service.storage.entitlements() == before[4]


@pytest.mark.parametrize("state", ("wrong_config_owner", "unbound_sql_borrower"))
def test_cli_refreshed_preview_refuses_foreign_owner_before_typed_A(
    rig, monkeypatch, capsys, state,
):
    import json
    from types import SimpleNamespace
    from cifereca import cli

    service, session = rig
    if state == "wrong_config_owner":
        service.config.set("providers.accounts", [{
            "provider": "kobo", "id": "vault-1", "identity_id": "bob",
            "enabled": True, "credentials": {},
        }])
    else:
        monkeypatch.setattr(
            service.storage, "provider_scope_account_bound",
            lambda **_kw: False,
        )
    monkeypatch.setattr(
        session, "rights_observation_snapshot",
        lambda *_a: pytest.fail("foreign scope cannot read A"),
    )
    before = tuple(service.accounts)
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_kw: SimpleNamespace(library=service, close=lambda: None),
    )
    rc = cli.main([
        "kobo-resume-preview", "--credential", "vault-1",
        "--refresh-manifest",
    ])
    report = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert report["status"] == "unavailable"
    assert report["preview_only"] is True
    assert report["acquisition_authorized"] is False
    assert tuple(service.accounts) == before
    assert service.storage.placements() == []
