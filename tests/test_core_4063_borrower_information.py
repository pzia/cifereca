"""Synthetic contract tests; no real provider access."""
from time import time

from cifereca.core.borrower_information import (
    _display_values, prepare_borrower_information,
)


def test_display_projection_is_closed_and_never_admits():
    stamp = int(time())
    assert _display_values({
        "observed_at": stamp, "item_offer": "unavailable",
        "permission": "eligible", "remaining_quota": 4,
        "available_copies": 5, "pass_state": "active", "secret": "private",
    }) == {
        "observed_at": stamp, "item_offer": "unavailable",
        "permission": "unknown", "remaining_quota": None,
        "available_copies": None, "pass_state": "unknown",
    }


def test_invalid_timestamp_or_payload_fails_closed():
    for raw in (None, {}, {"observed_at": True},
                {"observed_at": int(time()) + 60}):
        assert _display_values(raw) is None
    assert _display_values({
        "observed_at": int(time()), "item_offer": "available",
    })["item_offer"] == "unknown"


def test_invalid_notice_does_not_access_service():
    class NoService:
        def __getattr__(self, name):
            raise AssertionError(name)

    assert prepare_borrower_information(
        NoService(), object(), identity_id="reader", account_id="account",
    ) is None


def _synthetic_scope(monkeypatch):
    from types import SimpleNamespace as NS
    from cifereca.models import Account, SearchResult
    from cifereca.core import borrower_information as module

    account = Account(unique_id="account", identity_id="reader",
                      provider_id="synthetic", credential_id="credential")
    item = SearchResult(
        id="row", record_id="edition", docbase="catalogue", medium="digital",
        title="Book", author=None, type=None, format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="", source_provider="synthetic",
        source_credential="credential", source_account_id="account",
    )
    events = []
    state = NS(valid=True, revoke_during_read=False)

    def observe(notice, borrower):
        assert notice is item and borrower is account
        events.append("observe")
        if state.revoke_during_read:
            state.valid = False
        return object()

    def project(observation, notice, borrower):
        assert notice is item and borrower is account
        events.append("project")
        return {"observed_at": int(time()), "item_offer": "unavailable"}

    session = NS(borrower_information_for=observe,
                 borrower_information_display=project)
    service = NS(accounts=[account], _sessions={("synthetic", "credential"): session},
                 _session_key=lambda provider, credential: (provider, credential))
    monkeypatch.setattr(module, "_inert_session_accounts", lambda session: [account])
    monkeypatch.setattr(module, "capture_search_read_scope", lambda *args: object())
    monkeypatch.setattr(module, "capture_borrow_action_scope",
                        lambda *args, **kwargs: NS(current=lambda service: state.valid))
    return service, item, state, events


def test_explicit_read_is_one_shot_and_preparation_is_inert(monkeypatch):
    service, item, state, events = _synthetic_scope(monkeypatch)
    read = prepare_borrower_information(
        service, item, identity_id="reader", account_id="account")
    assert callable(read) and events == []
    assert read()["item_offer"] == "unavailable"
    assert read() is None
    assert events == ["observe", "project"]


def test_revocation_before_or_during_read_prevents_publication(monkeypatch):
    service, item, state, events = _synthetic_scope(monkeypatch)
    read = prepare_borrower_information(
        service, item, identity_id="reader", account_id="account")
    state.valid = False
    assert read() is None and events == []
    state.valid = True
    state.revoke_during_read = True
    read = prepare_borrower_information(
        service, item, identity_id="reader", account_id="account")
    assert read() is None and events == ["observe"]


def test_foreign_account_and_changed_notice_never_read(monkeypatch):
    service, item, state, events = _synthetic_scope(monkeypatch)
    assert prepare_borrower_information(
        service, item, identity_id="reader", account_id="other") is None
    read = prepare_borrower_information(
        service, item, identity_id="reader", account_id="account")
    item.source_account_id = "other"
    assert read() is None and events == []


def test_advisory_property_is_never_evaluated_at_preparation(monkeypatch):
    service, item, state, events = _synthetic_scope(monkeypatch)
    key = ("synthetic", "credential")
    original = service._sessions[key]

    class Hostile:
        @property
        def borrower_information_for(self):
            events.append("unexpected descriptor")
            return original.borrower_information_for

        def borrower_information_display(self, *args):
            return original.borrower_information_display(*args)

    service._sessions[key] = Hostile()
    assert prepare_borrower_information(
        service, item, identity_id="reader", account_id="account") is None
    assert events == []


def test_replaced_provider_method_revokes_prepared_read(monkeypatch):
    service, item, state, events = _synthetic_scope(monkeypatch)
    read = prepare_borrower_information(
        service, item, identity_id="reader", account_id="account")
    assert callable(read)
    session = service._sessions[("synthetic", "credential")]
    session.borrower_information_display = lambda *args: events.append("replacement")
    assert read() is None
    assert events == []
