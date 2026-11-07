"""P0 #4858: exact Kobo CLI connects one persisted in-memory account.

The preview connector intentionally leaves LibraryService.accounts untouched.
This tests only the new selected-edition promotion; no network, credential
serialization, SQLite mutation, HTTP GET or file publication is permitted.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.core import provider_sessions
from cifereca.models import Account
from cifereca.providers import ProviderConfigurationError


class Session:
    provider_id = "kobo"
    credential_id = "kobo-2"
    connected = True

    def __init__(self, rows):
        self._accounts = rows

    # Statically callable complete ABI; binding must not execute provider IO.
    def rights_observation_snapshot(self, *_a, **_kw):
        raise AssertionError("source bind must never read supplier rights")

    def rights_selected_delivery_offer_snapshot(self, *_a, **_kw):
        raise AssertionError("source bind must never inspect offers")

    def rights_selected_delivery_candidate(self, *_a, **_kw):
        raise AssertionError("source bind must never request content")

    @property
    def accounts(self):
        return list(self._accounts)


def _rig(monkeypatch, *, session=None, accepted=True, identity_present=True):
    exact = Account(
        unique_id="borrower-synthetic", provider_id="kobo",
        credential_id="kobo-2", identity_id="default",
    )
    other = Account(
        unique_id="unrelated", provider_id="paris",
        credential_id="paris-1", identity_id="identity-other",
    )
    session = session or Session([exact])
    key = ("kobo", "kobo-2")
    previous = object()
    read_calls = []
    def bound(**kwargs):
        read_calls.append(("scope", kwargs.copy()))
        return accepted and kwargs == dict(
            identity_id="identity-owner", provider_id="kobo",
            credential_id="kobo-2", account_id="borrower-synthetic",
        )
    storage = NS(
        identity_for_connection=lambda provider, credential: (
            read_calls.append(("identity", provider, credential))
            or "identity-owner"
        ),
        provider_scope_account_bound=bound,
    )
    svc = NS(
        storage=storage,
        config=NS(provider_accounts=lambda *, enabled_only=False: [{
            "provider": "kobo", "id": "kobo-2",
            "identity_id": "identity-owner", "enabled": True,
        }]),
        _session_key=lambda provider, credential: (provider, credential),
        _sessions={key: session},
        accounts=[other],
        primary=other,
        current_account=other,
        current_identity=previous,
        _active_session_key=("paris", "paris-1"),
        _source_context_epoch=5,
        identity_by_id=lambda identity: (
            NS(identity_id=identity)
            if identity == "identity-owner" and identity_present else None
        ),
    )
    preview_calls = []
    def preview(s, credential):
        preview_calls.append((s, credential))
        if credential != "kobo-2":
            raise ProviderConfigurationError("synthetic credential unavailable")
        return session
    monkeypatch.setattr(provider_sessions, "connect_kobo_preview", preview)
    return svc, session, previous, read_calls, preview_calls


def test_exact_selected_binds_persisted_account_without_enrolling(monkeypatch):
    svc, session, previous, reads, calls = _rig(monkeypatch)
    selected = provider_sessions.connect_kobo_selected_edition(svc, "kobo-2")
    assert selected is session
    assert calls == [(svc, "kobo-2")]
    assert len(svc.accounts) == 2
    assert svc.accounts[0].provider_id == "paris"
    assert svc.accounts[1] is session._accounts[0]
    assert svc.accounts[1].identity_id == "identity-owner"
    # A selected Kobo request cannot erase or select another Core identity.
    assert svc.current_account is svc.primary is svc.accounts[0]
    assert svc.current_identity is previous
    assert svc._active_session_key == ("paris", "paris-1")
    assert svc._source_context_epoch == 6
    assert reads[-1] == ("scope", {
        "identity_id": "identity-owner", "provider_id": "kobo",
        "credential_id": "kobo-2", "account_id": "borrower-synthetic",
    })
    assert not hasattr(svc.storage, "provider_account_put")
    assert not hasattr(svc.storage, "provider_connection_put")


@pytest.mark.parametrize("case", [
    "foreign_credential", "foreign_provider", "multiple_borrowers",
    "wrong_stored_owner", "identity_unavailable", "disconnected",
])
def test_selected_refuses_bad_scope_without_upgrading_memory(monkeypatch, case):
    rows = [Account(
        unique_id="borrower-synthetic", provider_id="kobo",
        credential_id="kobo-2",
    )]
    session = Session(rows)
    accepted = True
    identity_present = True
    if case == "foreign_credential":
        rows[0].credential_id = "kobo-1"
    elif case == "foreign_provider":
        rows[0].provider_id = "paris"
    elif case == "multiple_borrowers":
        rows.append(Account(
            unique_id="different", provider_id="kobo", credential_id="kobo-2",
        ))
    elif case == "wrong_stored_owner":
        accepted = False
    elif case == "identity_unavailable":
        identity_present = False
    elif case == "disconnected":
        session.connected = False
    svc, _, before, _, _ = _rig(
        monkeypatch, session=session, accepted=accepted,
        identity_present=identity_present,
    )
    old_account = svc.accounts[0]
    old_epoch = svc._source_context_epoch
    with pytest.raises(ProviderConfigurationError):
        provider_sessions.connect_kobo_selected_edition(svc, "kobo-2")
    assert svc.accounts == [old_account]
    assert svc.primary is old_account and svc.current_account is old_account
    assert svc.current_identity is before
    assert svc._source_context_epoch == old_epoch
    assert rows[0].identity_id == "default"


def test_unknown_credential_fails_before_any_scope_promotion(monkeypatch):
    svc, _, before, reads, calls = _rig(monkeypatch)
    with pytest.raises(ProviderConfigurationError):
        provider_sessions.connect_kobo_selected_edition(svc, "other-kobo")
    assert calls == [(svc, "other-kobo")]
    assert reads == []
    assert svc.current_identity is before
    assert svc._source_context_epoch == 5
