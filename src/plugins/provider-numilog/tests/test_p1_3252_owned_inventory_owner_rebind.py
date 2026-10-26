"""#3252: Numilog inventory positives belong to one Core owner during GET.

Fixtures are synthetic: no real account, HTTP, fulfillment or SQLite writes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession


class IdentityBroker:
    def __init__(self):
        self.owner = "identity-A"
        self.calls = 0
        self.account_id = None

    def accounts(self):
        self.calls += 1
        if self.owner is None:
            return []
        return [SimpleNamespace(
            provider_id="numilog", account_id=self.account_id,
            identity_id=self.owner,
        )]


def session(*, with_broker=True):
    broker = IdentityBroker()
    instance = NumilogSession(
        credential_id="synthetic-account", label="Numilog",
        credentials={}, options={}, config={}, diagnostics=None,
        identity_broker=broker if with_broker else None,
    )
    instance.connected = True
    broker.account_id = "numilog:" + instance.scope
    return instance, broker


def owned_row():
    return {
        "lid": "42", "variant": "()", "title": "Livre synthétique",
        "cover": None, "public_url": None,
    }


@pytest.mark.parametrize("new_owner", [None, "identity-B", "identity-A"])
def test_library_rejects_changed_or_revoked_owner_during_http_read(new_owner):
    s, broker = session()
    calls = []

    def fake_rows(**kwargs):
        calls.append("meslivres")
        broker.owner = new_owner
        return [owned_row()]

    s._rows = fake_rows
    try:
        if new_owner == "identity-A":
            result = s.library()
            assert len(result) == 1
            assert result[0].owned is True
            assert result[0].source_credential == "synthetic-account"
        else:
            with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
                s.library()
        assert calls == ["meslivres"]
        assert broker.calls >= 2
    finally:
        s.close()


@pytest.mark.parametrize("new_owner", [None, "identity-A"])
def test_unresolved_owner_keeps_v1_inventory_but_detects_new_binding(new_owner):
    s, broker = session()
    broker.owner = None
    calls = []

    def read(**kwargs):
        calls.append("meslivres")
        broker.owner = new_owner
        return [owned_row()]

    s._rows = read
    try:
        if new_owner is None:
            # Pure library inspection predates the identity broker and must
            # remain possible. The separate ACSM path REQUIRES an owner.
            assert len(s.library()) == 1
        else:
            with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
                s.library()
        assert calls == ["meslivres"]
        assert broker.calls >= 2
    finally:
        s.close()


def test_library_refuses_owner_change_during_projection():
    s, broker = session()
    s._rows = lambda **kwargs: [owned_row()]
    normalize = s._normalize

    def changed(row):
        projected = normalize(row)
        broker.owner = "identity-B"
        return projected

    s._normalize = changed
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.library()
    finally:
        s.close()


def test_legacy_brokerless_library_keeps_existing_behavior():
    s, _broker = session(with_broker=False)
    s._rows = lambda **kwargs: [owned_row()]
    try:
        books = s.library()
        assert len(books) == 1
        assert books[0].owned is True
    finally:
        s.close()
