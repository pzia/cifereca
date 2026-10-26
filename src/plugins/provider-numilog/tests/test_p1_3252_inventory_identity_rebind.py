"""P1 #3252: an owned Numilog inventory cannot cross an identity reassignment.

Only synthetic account bindings and private rows. No network, cookies or ACSM.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession


class Broker:
    def __init__(self):
        self.owner = "identity-a"
        self.scope = ""

    def accounts(self):
        return [SimpleNamespace(
            provider_id="numilog", account_id="numilog:" + self.scope,
            identity_id=self.owner,
        )] if self.owner else []


def make_session():
    broker = Broker()
    session = NumilogSession(
        credential_id="synthetic-account", label="Synthetic",
        credentials={}, options={}, config={}, diagnostics=None,
        identity_broker=broker,
    )
    broker.scope = session.scope
    session.connected = True
    return session, broker


@pytest.mark.parametrize("replacement", ["identity-b", None])
def test_library_refuses_positive_rows_when_owner_changes_during_private_get(replacement):
    session, broker = make_session()
    calls = []

    def private_rows(**kwargs):
        calls.append("private-get")
        broker.owner = replacement
        return [{"lid": "42", "variant": "()", "title": "Synthetic"}]

    session._rows = private_rows
    session._normalize = lambda row: row
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            session.library()
        assert calls == ["private-get"]
    finally:
        session.close()


def test_library_refuses_even_empty_rows_when_owner_changes_during_get():
    session, broker = make_session()

    def private_rows(**kwargs):
        broker.owner = "identity-b"
        return []

    session._rows = private_rows
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            session.library()
    finally:
        session.close()


def test_library_refuses_missing_binding_before_network():
    session, broker = make_session()
    broker.owner = None
    calls = []
    session._rows = lambda **kw: calls.append("private-get")
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire indisponible"):
            session.library()
        assert calls == []
    finally:
        session.close()


def test_library_stable_binding_and_legacy_no_broker_remain_supported():
    for with_broker in (True, False):
        session, _broker = make_session()
        if not with_broker:
            session.identity_broker = None
        session._rows = lambda **kw: [{"lid": "42", "title": "Synthetic"}]
        session._normalize = lambda row: row
        try:
            assert session.library() == [{"lid": "42", "title": "Synthetic"}]
        finally:
            session.close()
