"""P0 #2975: startup must not render a sibling Paris identity's loans.

Reproduce the field report: the first cold opening sync reads every local
profile, while the Loans tab is scoped to the currently selected profile.
Synthetic provider sessions / real SQLite; no HTTP, ADEPT or acquisition.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, Loan
from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from tests.test_sync_state_contract import _service


def _account(identity: str, credential: str, *, uid: str) -> Account:
    return Account(
        display_name="PRIVATE account", unique_id=uid, provider_id="paris",
        credential_id=credential, identity_id=identity,
    )


def _loan(account: Account, suffix: str, medium: str) -> Loan:
    return Loan(
        id=suffix, provider="SYRACUSE" if medium == "physical" else "DILICOM",
        medium=medium, title="PRIVATE " + suffix, record_id="edition-" + suffix,
        source_provider="paris", source_credential=account.credential_id,
        source_account_id=account.unique_id, identity_id=account.identity_id,
        due_at="2099-01-01T00:00:00Z",
    )


def _setup(service, monkeypatch):
    first = _account("default", "paris-first", uid="reader-one")
    second = _account("alex", "paris-second", uid="reader-two")
    service.accounts = [first, second]
    enable_synthetic_paris_opening(service)
    observations = {
        first.credential_id: [_loan(first, "first-digital", "digital"),
                              _loan(first, "first-physical", "physical")],
        second.credential_id: [_loan(second, "second-digital", "digital"),
                               _loan(second, "second-physical", "physical")],
    }
    calls = []
    def read(account, *, force=False):
        calls.append((account.identity_id, account.credential_id))
        return list(observations[account.credential_id])
    monkeypatch.setattr(service, "_loans_for_account", read)
    monkeypatch.setattr(service, "mirror_personal_libraries", lambda: {})
    monkeypatch.setattr(service, "kobo_backup_enabled", lambda: False)
    return first, second, observations, calls


def test_cold_opening_sync_persists_both_profiles_but_displays_only_current(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        first, second, _, calls = _setup(service, monkeypatch)
        assert service.current_identity.identity_id == "default"
        result = service.opening_sync()
        assert {entry["identity_id"] for entry in result["identities"]} == {
            "default", "alex",
        }
        assert {identity for identity, _ in calls} == {"default", "alex"}
        assert {row.id for row in service.last_loans} == {
            "first-digital", "first-physical",
        }
        assert all(row.identity_id == "default" for row in service.last_loans)
        # The independent rights still exist. UI scope is not a destructive
        # rights filter and never deletes the sibling's observed loan.
        rights = service.storage.entitlements(provider_id="paris")
        assert {(row["identity_id"], row["loan_id"]) for row in rights} == {
            ("default", "first-digital"), ("alex", "second-digital"),
        }
        service.select_identity("alex")
        assert service.last_loans == []  # no old loans during async loading
        service.opening_sync()
        assert {row.id for row in service.last_loans} == {
            "second-digital", "second-physical",
        }
        assert all(row.identity_id == "alex" for row in service.last_loans)
    finally:
        service.close()


def test_account_selection_across_profiles_drops_previous_private_projection(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        first, second, observations, _ = _setup(service, monkeypatch)
        service.current_account = first
        service.last_loans = list(observations[first.credential_id])
        service.select_account(second.unique_id, second.provider_id, second.credential_id)
        assert service.current_identity.identity_id == "alex"
        assert service.last_loans == []
        service.select_account(first.unique_id, first.provider_id, first.credential_id)
        assert service.current_identity.identity_id == "default"
        assert service.last_loans == []
    finally:
        service.close()


@pytest.mark.parametrize("round_trip", [False, True])
def test_inflight_old_identity_refresh_never_publishes_into_new_or_reselected_profile(
    tmp_path, monkeypatch, round_trip,
):
    service = _service(tmp_path)
    try:
        first, second, observations, _ = _setup(service, monkeypatch)
        for account in (first, second):
            service._sessions[service._session_key("paris", account.credential_id)] = NS(
                capabilities=NS(loans=True),
            )
        def switch_while_fetching(account, *, force=False):
            assert account is first
            service.select_identity("alex")
            if round_trip:
                service.select_identity("default")
            return list(observations[first.credential_id])
        monkeypatch.setattr(service, "_loans_for_account", switch_while_fetching)
        result = service.refresh_identity_loans(identity_id="default", force=False)
        assert {row.id for row in result} == {"first-digital", "first-physical"}
        assert service.last_loans == []
        assert service.current_identity.identity_id == (
            "default" if round_trip else "alex"
        )
    finally:
        service.close()


def test_gui_loan_projection_filters_foreign_rows_even_from_stale_caller(tmp_path):
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from cifereca.gui.loans_controller import _populate_loans
    service = _service(tmp_path)
    try:
        first = _account("default", "first", uid="one")
        second = _account("alex", "second", uid="two")
        host = NS(
            service=service,
            _render_loans=lambda: None,
            _render_kobo_loans=lambda: None,
        )
        _populate_loans(host, [_loan(first, "visible", "physical"),
                               _loan(second, "foreign", "digital")])
        assert [row.id for row in service.last_loans] == ["visible"]
        assert len(host.loan_objects) == 1
        assert "foreign" not in str(host.loan_objects)
    finally:
        service.close()
