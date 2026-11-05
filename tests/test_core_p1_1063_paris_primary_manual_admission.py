"""P1 #1063: the explicit Paris borrower cannot ignore the normal login.

Real Core/SQLite and mock Paris HTTP. The selected primary source is checked
before the UI click and again during supplier descriptive preflight. Neither
a pending claim nor a borrow button activates Adobe or infers entitlement.
"""
from dataclasses import replace

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
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
            "blocked", "borrow_scope_unverified",
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
