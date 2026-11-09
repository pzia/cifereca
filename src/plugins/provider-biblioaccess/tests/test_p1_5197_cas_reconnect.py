"""#5197: expired CAS MyLoans, exactly one opted-in reconnect, exact source.

Synthetic clients and Core coordination; no real IdP, CAS, login, ACSM or
loan data. Expiration signal is closed; no account/secret in diagnostics.
"""
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

import biblioaccess_ajax_cas
from cifereca.core import provider_sessions, sync_coordination
from cifereca.plugin_api import read_loan_inventory
from cifereca.providers import ProviderConfigurationError
from tests.test_biblioaccess_sso import _synced_session, mod


def _expired_fixture(
    monkeypatch, *, optin="on", second_credential=False, fresh_rows=None,
):
    first, original_client, calls = _synced_session(credential="cred-a")
    account, = first.connect()
    account.identity_id = "identity-a"

    def expired():
        raise biblioaccess_ajax_cas.AjaxCasError("cas_loans_unverified")
    original_client.read_authenticated_loans = expired
    result = read_loan_inventory(first, account)
    assert result.status == "failed" and result.loans == ()
    assert not first.connected and original_client.closed
    assert first.sync_reconnect_reason == "session_expired"
    row = {
        "provider": mod.PROVIDER_ID, "id": "cred-a",
        "identity_id": "identity-a", "label": "SYNTHETIC-READER",
        "enabled": True,
        "credentials": {"username": "SYNTHETIC-USER",
                        "password": "SYNTHETIC-SECRET"},
        "options": {"authenticated_sync": optin, "experimental_http_probe": "off"},
    }
    rows = [row]
    other = None
    if second_credential:
        other = NS(identity_id="identity-b", provider_id=mod.PROVIDER_ID,
                   credential_id="cred-b", unique_id="primary", is_primary=True)
        rows.append({
            **row, "identity_id": "identity-b", "id": "cred-b",
        })
    # Prepare synthetic clients BEFORE monkeypatching the plugin's factory:
    # the shared fixture itself calls PLUGIN.create_session().
    ready = [
        _synced_session(credential="cred-a", rows=fresh_rows)
        for _ in range(2)
    ]
    new_sessions = []
    fresh_calls = []
    def factory(**kwargs):
        assert kwargs["credential_id"] == "cred-a"
        fresh, http, events = ready[len(new_sessions)]
        fresh.options = kwargs["options"]
        new_sessions.append((fresh, http, events))
        fresh_calls.append(kwargs["credential_id"])
        return fresh
    monkeypatch.setattr(mod.PLUGIN, "create_session", factory)
    key = lambda provider, credential: f"{provider.lower()}::{credential}"
    sessions = {key(mod.PROVIDER_ID, "cred-a"): first}
    if other is not None:
        sessions[key(mod.PROVIDER_ID, "cred-b")] = NS(
            connected=True, provider_id=mod.PROVIDER_ID, credential_id="cred-b",
        )
    notes = []
    host = NS(
        accounts=[account] + ([other] if other is not None else []),
        current_account=account, primary=account,
        current_identity=NS(identity_id="identity-a"),
        _sessions=sessions, _session_errors={},
        _source_context_epoch=2, _account_context_epoch=7,
        config=NS(provider_accounts=lambda enabled_only=True: rows),
        registry=NS(maybe_get=lambda p: mod.PLUGIN if p == mod.PROVIDER_ID else None),
        storage=NS(provider_connections=lambda identity_id: [
            {"identity_id": r["identity_id"],
             "provider_id": r["provider"], "credential_id": r["id"],
             "enabled": True} for r in rows if r["identity_id"] == identity_id
        ]),
        _provider_plugin_status=lambda _p: NS(enabled=True, state="ready"),
        _session_key=key,
        diagnostics=NS(add=lambda *a, **kw: notes.append((a, kw)),
                       add_core_source_sync=lambda *a, **kw: None),
    )
    host._synthetic_fresh_candidates = ready
    return host, first, account, calls, new_sessions, fresh_calls, notes


def test_expired_redirect_replaces_only_own_session_and_one_post(monkeypatch):
    host, old, account, calls, renewed, created, notes = _expired_fixture(
        monkeypatch, second_credential=True,
    )
    untouched = host._sessions[host._session_key(mod.PROVIDER_ID, "cred-b")]
    new = provider_sessions.reconnect_expired_configured_source_once(
        host, account, old,
    )
    assert new is not None and new is not account
    assert new.identity_id == "identity-a"
    assert new.credential_id == "cred-a"
    assert host._sessions[host._session_key(mod.PROVIDER_ID, "cred-a")] is renewed[0][0]
    assert host._sessions[host._session_key(mod.PROVIDER_ID, "cred-b")] is untouched
    assert host.accounts[0] is new and host.accounts[1].identity_id == "identity-b"
    assert host.current_account is host.primary is new
    assert host._source_context_epoch == 3 and host._account_context_epoch == 8
    assert created == ["cred-a"] and renewed[0][2].count("post") == 1
    assert len([x for x in notes if "session_expired_reconnected" in repr(x)]) == 1
    assert "SYNTHETIC-SECRET" not in repr(notes)


@pytest.mark.parametrize("optin", ["off", "true", "", None])
def test_no_literal_consent_never_reauthenticates(monkeypatch, optin):
    host, old, account, calls, renewed, created, notes = _expired_fixture(
        monkeypatch, optin=optin,
    )
    assert provider_sessions.reconnect_expired_configured_source_once(
        host, account, old,
    ) is None
    assert created == [] and host.accounts[0] is account
    assert host._sessions[host._session_key(mod.PROVIDER_ID, "cred-a")] is old


def test_timeout_generic_error_not_expiry_and_no_second_post(monkeypatch):
    host, old, account, calls, renewed, created, notes = _expired_fixture(monkeypatch)
    old.sync_reconnect_reason = None
    assert provider_sessions.reconnect_expired_configured_source_once(
        host, account, old,
    ) is None
    assert created == []


def test_full_source_sync_retries_once_with_new_cas_and_keeps_positive_row(
    monkeypatch, tmp_path,
):
    host, old, account, calls, renewed, created, notes = _expired_fixture(monkeypatch)
    starts = []
    def simulate_source(candidate, *, deep=False, control=None):
        starts.append(candidate)
        if candidate is account:
            raise ProviderConfigurationError("loan_inventory_read_unavailable")
        loans = read_loan_inventory(
            host._sessions[host._session_key(mod.PROVIDER_ID, "cred-a")],
            candidate,
        )
        assert len(loans.loans) == 1
        assert loans.status == "partial"  # no negative entitlement inference
        return {"status": "partial", "loans": 1}
    host._synchronize_account_source_impl = simulate_source
    host._synchronize_account_source_inventory_impl = simulate_source
    host.sync_source_enabled = lambda _account: True
    host._record_action = lambda *a, **k: None
    monkeypatch.setattr(sync_coordination, "InterProcessLock",
                        lambda *a, **k: nullcontext(NS()))
    monkeypatch.setattr(sync_coordination, "acquisition_lock_file",
                        lambda: tmp_path / "fake.lock")
    from cifereca.core import canonical_sync_stage
    monkeypatch.setattr(canonical_sync_stage, "observe_canonical_rights_in_sync",
                        lambda *a, **k: None)
    monkeypatch.setattr(sync_coordination, "_run_observed_canonical_convergence",
                        lambda *a, **k: None)
    result = sync_coordination.synchronize_account_source(host, account)
    assert result["status"] == "partial" and result["loans"] == 1
    assert len(starts) == 2 and starts[0] is account
    assert starts[1] is host.accounts[0]
    assert created == ["cred-a"] and renewed[0][2].count("post") == 1
    assert renewed[0][2].count("live_loans") == 1
    assert host._sessions[host._session_key(mod.PROVIDER_ID, "cred-a")] is renewed[0][0]


def test_second_cas_failure_ends_after_one_reconnect(monkeypatch, tmp_path):
    host, old, account, calls, renewed, created, notes = _expired_fixture(monkeypatch)
    fresh, http, events = host._synthetic_fresh_candidates[0]
    def expired_again():
        raise biblioaccess_ajax_cas.AjaxCasError("cas_loans_unverified")
    http.read_authenticated_loans = expired_again
    attempts = []
    def read(account_row, *, deep=False, control=None):
        attempts.append(account_row)
        if account_row is account:
            raise ProviderConfigurationError("loan_inventory_read_unavailable")
        snap = read_loan_inventory(
            host._sessions[host._session_key(mod.PROVIDER_ID, "cred-a")],
            account_row,
        )
        assert snap.status == "failed"
        raise ProviderConfigurationError("loan_inventory_read_unavailable")
    host._synchronize_account_source_impl = read
    host.sync_source_enabled = lambda a: True
    host._record_action = lambda *a, **k: None
    monkeypatch.setattr(sync_coordination, "InterProcessLock",
                        lambda *a, **k: nullcontext(NS()))
    monkeypatch.setattr(sync_coordination, "acquisition_lock_file",
                        lambda: tmp_path / "fake.lock")
    with pytest.raises(ProviderConfigurationError):
        sync_coordination.synchronize_account_source(host, account)
    assert len(attempts) == 2
    assert created == ["cred-a"]
    assert renewed[0][2].count("post") == 1
    assert renewed[0][1].closed


@pytest.mark.parametrize("when", ["before", "during"])
def test_disabling_account_never_installs_new_cas_authority(monkeypatch, when):
    host, old, account, calls, renewed, created, notes = _expired_fixture(monkeypatch)
    row = host.config.provider_accounts()[0]
    if when == "before":
        row["enabled"] = False
    else:
        def revoke_after_request(**kwargs):
            fresh, http, events = host._synthetic_fresh_candidates[0]
            original = http.submit_observed_login_once
            def submit(**details):
                response = original(**details)
                row["enabled"] = False
                return response
            http.submit_observed_login_once = submit
            renewed.append((fresh, http, events))
            created.append(kwargs["credential_id"])
            return fresh
        monkeypatch.setattr(mod.PLUGIN, "create_session", revoke_after_request)
    assert provider_sessions.reconnect_expired_configured_source_once(
        host, account, old,
    ) is None
    assert host.accounts[0] is account
    assert host._sessions[host._session_key(mod.PROVIDER_ID, "cred-a")] is old
    assert created == ([] if when == "before" else ["cred-a"])
    if when == "during":
        assert renewed[0][1].closed


def test_foreign_credential_does_not_become_reconnect_target(monkeypatch):
    host, old, account, calls, renewed, created, notes = _expired_fixture(
        monkeypatch, second_credential=True,
    )
    wrong = host.accounts[1]
    assert provider_sessions.reconnect_expired_configured_source_once(
        host, wrong, old,
    ) is None
    assert created == []
    assert host.accounts[0] is account


def test_fresh_cas_without_acsm_retains_positive_loan_without_download(monkeypatch):
    # Missing signed ACSM offer is neither missing loan nor permission
    # to reborrow. The Core may represent the loan but must not acquire it.
    rows = [NS(
        product_ref="10001", book_id="20002", title="SYNTHETIC",
        due_at_local="09/11/2026 12:30:00",
        acsm_offer_present=False, acsm_url=None,
    )]
    host, old, account, calls, renewed, created, notes = _expired_fixture(
        monkeypatch, fresh_rows=rows,
    )
    replacement = provider_sessions.reconnect_expired_configured_source_once(
        host, account, old,
    )
    assert replacement is not None
    snapshot = read_loan_inventory(
        host._sessions[host._session_key(mod.PROVIDER_ID, "cred-a")],
        replacement,
    )
    assert snapshot.status == "partial" and len(snapshot.loans) == 1
    assert snapshot.loans[0].raw == {} and snapshot.loans[0].ebook_url is None
    assert created == ["cred-a"] and renewed[0][2].count("post") == 1
    assert "SYNTHETIC-SECRET" not in repr(notes)


def test_actual_generic_transport_timeout_does_not_signal_cas_expiry():
    session, http, calls = _synced_session(expired=True)
    try:
        account, = session.connect()
        account.identity_id = "identity-a"
        failed = read_loan_inventory(session, account)
        assert failed.status == "failed"
        assert session.connected is False and http.closed
        assert session.sync_reconnect_reason is None
        assert calls.count("post") == 1
    finally:
        session.close()
