"""P1 #1063: the explicit Paris borrower cannot ignore the normal login.

Real Core/SQLite and mock Paris HTTP. The selected primary source is checked
before the UI click and again during supplier descriptive preflight. Neither
a pending claim nor a borrow button activates Adobe or infers entitlement.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_library_reconciliation import epub
from tests.test_paris_manual_borrow_dispatch import setup, attempt


def add_secondary(service, *, identity="default", credential="card-B"):
    service.storage.provider_connection_put({
        "identity_id": identity, "provider_id": "paris",
        "credential_id": credential, "label": "Other Paris", "enabled": True,
        "metadata": {},
    })


def select(service, credential):
    saved = dict(service.config.identity("default"))
    if credential is None:
        saved.pop("primary_paris_connection_id", None)
    else:
        saved["primary_paris_connection_id"] = credential
    service.config.upsert_identity(saved)
    service.config.set("providers.accounts", [
        {"id": name, "provider": "paris", "identity_id": "default",
         "enabled": True}
        for name in ("card-A", "card-B")
    ])


def test_single_paris_connection_legacy_unselected_still_works(tmp_path):
    service, _session, account, item, state = setup(tmp_path)
    try:
        assert service.can_borrow_paris(item)
        assert attempt(service, account, item)["status"] == "confirmed"
        assert state["mutations"] == 1
    finally:
        service.close()


def test_two_sources_need_selection_and_selected_one_is_only_borrower(tmp_path):
    service, _session, account, item, state = setup(tmp_path)
    try:
        add_secondary(service)
        assert not service.can_borrow_paris(item)
        outcome = attempt(service, account, item)
        assert (outcome["status"], outcome["reason_code"]) == (
            "blocked", "borrow_scope_changed",
        )
        assert not state["calls"]
        assert service.storage.metadata_get(borrow_claim_key(account, item)) is None

        select(service, "card-B")
        assert not service.can_borrow_paris(item)
        assert attempt(service, account, item)["status"] == "blocked"
        assert not state["calls"]

        select(service, "card-A")
        assert service.can_borrow_paris(item)
        assert attempt(service, account, item)["status"] == "confirmed"
        assert state["mutations"] == 1
    finally:
        service.close()


def test_selected_primary_loan_reuses_acsm_under_its_bound_profile(tmp_path, monkeypatch):
    service, session, account, item, state = setup(tmp_path)
    try:
        add_secondary(service)
        select(service, "card-A")
        secondary = replace(account, credential_id="card-B")
        service.accounts.append(secondary)
        service.storage.adept_profile_binding_set("identity", "default", "primary-profile")
        service.storage.adept_profile_binding_set("identity", "alex", "other-profile")
        monkeypatch.setattr(service, "kobo_profiles", lambda: [
            {"profile_id": "primary-profile", "name": "primary-cli", "fetch_capable": True},
            {"profile_id": "other-profile", "name": "other-cli", "fetch_capable": True},
        ])

        observed_loans = []
        provider_borrow = session.borrow_explicit_with_claim

        def observe_borrow(*args, **kwargs):
            loan = provider_borrow(*args, **kwargs)
            observed_loans.append(loan)
            return loan

        monkeypatch.setattr(session, "borrow_explicit_with_claim", observe_borrow)
        assert attempt(service, account, item)["status"] == "confirmed"
        observed, = observed_loans
        assert (observed.id, observed.record_id, observed.source_credential,
                observed.source_account_id) == ("loan-exact", "edition", "card-A", "primary")
        assert state["mutations"] == 1
        assert state["calls"].count("/numerique/synthetic.acsm") == 1
        calls_after_borrow = state["calls"].copy()

        # The refreshed inventory can lack the signed first-offer URL. A
        # foreground identity switch must not affect the retained token owner.
        loan = replace(observed, ebook_url=None)
        service._persist_active_loans("default", [loan])
        service.current_identity = service.identity_by_id("alex")
        runtime_requests = []

        def fulfill(request):
            runtime_requests.append(request)
            source = Path(request["source"]["path"])
            assert b"application/epub+zip" in source.read_bytes()
            destination = Path(request["destination"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            epub(destination, title="Synthetic Paris")
            return {
                "ok": True, "profile": "primary-cli", "loan_id": "synthetic-adept-loan",
                "artifact": {"path": str(destination), "format": "epub", "drm": "none"},
            }

        monkeypatch.setattr(service, "execute_acquisition_request", fulfill)
        monkeypatch.setattr(service, "runtime_diagnostic", lambda *_: {})
        assert service._acquire_active_loan_automatically(loan)["status"] == "complete"
        request, = runtime_requests
        assert request["profile"] == "primary-cli"
        assert state["calls"] == calls_after_borrow
        row, = service.storage.acquisitions(status="complete", provider_id="paris")
        assert (row["identity_id"], row["credential_id"], row["account_id"]) == (
            "default", "card-A", "primary",
        )
        binding = row["metadata"]["adept_binding"]
        assert binding["adept_profile_id"] == "primary-profile"
        assert binding["adept_loan_id"] == "synthetic-adept-loan"
    finally:
        service.close()


@pytest.mark.parametrize("change", [
    "disabled_config", "disabled_sqlite", "foreign", "unknown", "bad_type",
])
def test_invalid_saved_primary_never_admits_action(tmp_path, change):
    service, _session, account, item, state = setup(tmp_path)
    try:
        select(service, "card-A")
        if change == "disabled_config":
            service.config.set("providers.accounts", [{
                "id": "card-A", "provider": "paris",
                "identity_id": "default", "enabled": False,
            }])
        elif change == "disabled_sqlite":
            service.storage.provider_connection_put({
                "identity_id": "default", "provider_id": "paris",
                "credential_id": "card-A", "label": "Disabled",
                "enabled": False, "metadata": {},
            })
        elif change == "foreign":
            service.config.set("providers.accounts", [{
                "id": "card-A", "provider": "paris",
                "identity_id": "foreign", "enabled": True,
            }])
        elif change == "unknown":
            select(service, "card-absent")
        else:
            select(service, True)
        assert not service.can_borrow_paris(item)
        result = attempt(service, account, item)
        assert result["status"] == "blocked"
        assert not state["calls"]
        assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
    finally:
        service.close()


@pytest.mark.parametrize("change", ["reselect", "disable", "second_implicit"])
def test_rebind_during_holdings_never_reaches_mutating_redirect(tmp_path, change):
    service, _session, account, item, state = setup(tmp_path)
    try:
        add_secondary(service)
        select(service, "card-A")

        def revoke():
            if change == "reselect":
                select(service, "card-B")
            elif change == "disable":
                service.config.set("providers.accounts", [{
                    "id": "card-A", "provider": "paris",
                    "identity_id": "default", "enabled": False,
                }])
            else:
                select(service, None)
        state["before_holdings"] = revoke
        outcome = attempt(service, account, item)
        assert outcome["status"] != "confirmed"
        assert state["mutations"] == 0
        assert state["calls"].count("/numerique/Portal/ASSARedirect.ashx") == 0
        # A checkpoint may be released only if the trusted provider proves
        # no mutating request was sent. No upstream claim is assumed.
        assert service.storage.entitlements(provider_id="paris") == []
    finally:
        service.close()


def test_no_configured_primary_does_not_grant_foreign_identity(tmp_path):
    service, _session, account, item, state = setup(tmp_path)
    try:
        assert attempt(service, replace(account, identity_id="foreign"), item)["status"] == "blocked"
        assert state["mutations"] == 0
    finally:
        service.close()
