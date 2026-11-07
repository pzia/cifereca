"""#4858 W2: scoped Kobo resume reaches the same modern A->B Sync.

Real LibraryService/Storage/bound Kobo session; provider offers and the
canonical executor boundary are synthetic to prove single-dispatch behavior.
Full A->B placement and GET validation are covered by canonical Sync suites.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core import kobo_backup, kobo_backup_canonical_dispatch as dispatch
from cifereca.core.source_sync_consent import Policy
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _service


_EXPECTED = (
    "downloaded", "recovered", "present_attested", "pending", "blocked",
    "failed", "cancelled", "skipped_archived", "inactive",
)


def _counts(**values):
    rows = {name: 0 for name in _EXPECTED}
    rows.update(values)
    return rows


@pytest.fixture
def rig(tmp_path, monkeypatch):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    account = session.accounts[0]
    service.accounts = [account]
    service.current_account = account
    monkeypatch.setattr(service, "sync_source_enabled", lambda row: row is account)
    grant = [Policy("on", "a" * 32)]
    monkeypatch.setattr(dispatch, "read_policy", lambda *_a: grant[0])
    for method in (
        "downloadable_resource_snapshot",
        "downloadable_selected_offer_snapshot",
        "downloadable_selected_delivery_candidate_runtime_guarded",
    ):
        monkeypatch.setattr(
            session, method,
            lambda *_a, **_kw: pytest.fail("Only canonical Sync may call provider"),
            raising=False,
        )
    calls = []
    def modern(selected, *, control=None):
        assert selected is account and control is not None
        control.check_cancelled()
        calls.append(("canonical", selected))
        return {
            "provider": "kobo", "account_id": "account",
            "canonical_execution": _counts(downloaded=1, present_attested=1),
        }
    monkeypatch.setattr(service, "synchronize_account_source", modern)
    monkeypatch.setattr(
        kobo_backup, "sync_kobo_backup",
        lambda *_a, **_kw: pytest.fail("0.7 backup executor is forbidden"),
    )
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("legacy provider GET forbidden"),
    )
    monkeypatch.setattr(
        service.storage, "backup_put",
        lambda *_a, **_kw: pytest.fail("legacy backup_items writer forbidden"),
    )
    try:
        yield service, session, account, grant, calls
    finally:
        service.close()


def test_scoped_resume_dispatches_one_real_canonical_sync_without_w2(rig):
    service, _session, _account, _grant, calls = rig
    before = (service.storage.backup_list(), service.storage.placements())
    progress = []
    result = service.sync_kobo_backup_account("vault-1", progress.append)
    assert calls == [("canonical", _account)]
    assert result["scope"] == "account"
    assert result["execution_path"] == "canonical"
    assert result["status"] == "complete"
    assert result["downloaded"] == 1
    assert result["unchanged"] == 1
    assert result["completed"] == 2
    assert result["legacy_backup_mutations"] == 0
    assert progress == [{
        "event": "done", "status": "complete",
        "downloaded": 1, "unchanged": 1,
        "attention_required": 0, "errors": 0,
    }]
    assert "vault-1" not in str(result)
    assert (service.storage.backup_list(), service.storage.placements()) == before


def test_canonical_replay_is_observed_as_present_not_reacquired(rig, monkeypatch):
    service, _session, account, _grant, calls = rig
    def replay(selected, *, control=None):
        assert selected is account
        calls.append(("canonical", selected))
        return {
            "provider": "kobo", "account_id": "account",
            "status": "success", "canonical_execution": _counts(present_attested=2),
        }
    monkeypatch.setattr(service, "synchronize_account_source", replay)
    result = service.sync_kobo_backup_account("vault-1")
    assert result["status"] == "complete"
    assert result["downloaded"] == 0
    assert result["unchanged"] == 2
    assert service.storage.backup_list() == []


@pytest.mark.parametrize("report", [
    {"provider": "kobo", "account_id": "account", "status": "success"},
    {"provider": "kobo", "account_id": "account", "canonical_execution": {"downloaded": 1}},
    {"provider": "kobo", "account_id": "other", "canonical_execution": _counts()},
])
def test_missing_or_foreign_canonical_execution_never_claims_success_or_falls_back(
    rig, monkeypatch, report,
):
    service, _session, _account, _grant, _calls = rig
    monkeypatch.setattr(
        service, "synchronize_account_source", lambda *_a, **_kw: report,
    )
    if report.get("account_id") == "other":
        with pytest.raises(ProviderConfigurationError,
                           match="kobo_canonical_sync_unverified"):
            service.sync_kobo_backup_account("vault-1")
    else:
        result = service.sync_kobo_backup_account("vault-1")
        assert result["status"] == "partial"
        assert result["downloaded"] == 0


@pytest.mark.parametrize("status,key", [
    ("partial", "pending"), ("partial", "failed"),
    ("partial", "blocked"), ("partial", "inactive"),
    ("cancelled", "cancelled"),
])
def test_canonical_outcome_is_not_converted_into_success(rig, monkeypatch, status, key):
    service, _session, _account, _grant, calls = rig
    monkeypatch.setattr(
        service, "synchronize_account_source", lambda *_a, **_kw: {
            "provider": "kobo", "account_id": "account",
            "status": "success", "canonical_execution": _counts(**{key: 1}),
        },
    )
    result = service.sync_kobo_backup_account("vault-1")
    assert result["status"] == status
    assert result["downloaded"] == 0
    assert not calls


def test_legacy_enabled_is_not_a_canonical_cas_consent(rig):
    service, _session, _account, grant, calls = rig
    grant[0] = Policy("unset", None)
    result = service.sync_kobo_backup_account("vault-1")
    assert result["status"] == "attention_required"
    assert result["downloaded"] == 0
    assert result["legacy_backup_mutations"] == 0
    assert calls == []


def test_revoked_canonical_cas_consent_after_sync_refuses_completion(rig, monkeypatch):
    service, _session, account, grant, _calls = rig
    def change_policy(selected, *, control=None):
        assert selected is account
        grant[0] = Policy("off", "b" * 32)
        return {
            "provider": "kobo", "account_id": "account",
            "canonical_execution": _counts(downloaded=1),
        }
    monkeypatch.setattr(service, "synchronize_account_source", change_policy)
    with pytest.raises(ProviderConfigurationError,
                       match="kobo_canonical_source_changed"):
        service.sync_kobo_backup_account("vault-1")
    assert service.storage.backup_list() == []


@pytest.mark.parametrize("tamper", ["account_rebound", "session_closed", "epoch_changed"])
def test_rebind_after_sync_refuses_complete(rig, monkeypatch, tamper):
    service, session, account, grant, _calls = rig
    def mutate(selected, *, control=None):
        assert selected is account
        if tamper == "account_rebound":
            account.unique_id = "other"
        elif tamper == "session_closed":
            session.connected = False
        else:
            service._source_context_epoch += 1
        return {
            "provider": "kobo", "account_id": "account",
            "canonical_execution": _counts(downloaded=1),
        }
    monkeypatch.setattr(service, "synchronize_account_source", mutate)
    with pytest.raises(ProviderConfigurationError,
                       match="kobo_canonical_source_changed"):
        service.sync_kobo_backup_account("vault-1")


def test_cancelled_before_canonical_sync_is_no_op(rig):
    service, _session, _account, _grant, calls = rig
    result = service.sync_kobo_backup_account("vault-1", cancelled=lambda: True)
    assert result["status"] == "cancelled"
    assert result["downloaded"] == 0
    assert calls == []


def test_legacy_retry_switch_does_not_authorize_canonical_executor(rig):
    service, _session, _account, _grant, calls = rig
    with pytest.raises(ProviderConfigurationError,
                       match="kobo_canonical_legacy_scope_retry_unavailable"):
        service.sync_kobo_backup_account("vault-1", retry_scope_blocked=True)
    assert calls == []


@pytest.mark.parametrize("tamper", ["duplicate_account", "foreign_borrower", "disabled"])
def test_cross_owner_ambiguous_or_disabled_source_rejected_before_provider(rig, tamper):
    service, session, account, grant, calls = rig
    if tamper == "duplicate_account":
        service.accounts.append(replace(account))
    elif tamper == "foreign_borrower":
        session._accounts = [replace(account, unique_id="other-borrower")]
    else:
        service.sync_source_enabled = lambda _row: False
    with pytest.raises(ProviderConfigurationError, match="kobo_canonical_"):
        service.sync_kobo_backup_account("vault-1")
    assert calls == []


def test_partial_abi_retains_only_preexisting_guarded_legacy_compatibility(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    # No invented rights or implicit canonical conversion on incomplete ABI.
    calls = []
    def legacy(_service, progress=None, **kwargs):
        calls.append((_service, kwargs["credential_id"]))
        return {"status": "legacy-only"}
    monkeypatch.setattr(kobo_backup, "sync_kobo_backup", legacy)
    try:
        result = service.sync_kobo_backup_account("vault-1")
        assert result["status"] == "legacy-only"
        assert calls == [(service, "vault-1")]
    finally:
        service.close()
