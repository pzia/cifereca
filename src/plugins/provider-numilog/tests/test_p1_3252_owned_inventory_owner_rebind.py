"""#3252: Numilog inventory positives belong to one Core owner during GET.

Fixtures are synthetic: no real account, HTTP, fulfillment or SQLite writes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.plugin_api import Account, ProviderCapabilityError
from cifereca_numilog import NumilogSession


class IdentityBroker:
    def __init__(self):
        self.owner = "identity-A"
        self.calls = 0
        self.account_id = None

    def accounts(self, identity_id=None):
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
    instance._accounts = [Account(provider_id="numilog", credential_id=instance.credential_id,
                                  unique_id=broker.account_id, identity_id=broker.owner)]
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


def _candidate_broker(session, identities):
    """Synthetic same-account owner candidates, possibly ambiguous."""
    owner_scope = "numilog:" + session.scope
    broker = SimpleNamespace()
    broker.identities = list(identities)
    broker.calls = 0
    def accounts(identity_id=None):
        broker.calls += 1
        return [
            SimpleNamespace(provider_id="numilog", account_id=owner_scope,
                            identity_id=owner)
            for owner in broker.identities
        ]
    broker.accounts = accounts
    return broker


@pytest.mark.parametrize(("before", "after"), [
    (["identity-A", "identity-B"], ["identity-A", "identity-C"]),
    (["identity-A", "identity-A"], ["identity-A", "identity-B"]),
    (["identity-A", "identity-B"], []),
    ([], ["identity-A", "identity-B"]),
    (["identity-A", None], ["identity-A", "identity-B"]),
])
@pytest.mark.parametrize("phase", ["http", "projection"])
def test_ambiguous_owner_change_cannot_publish_inventory(before, after, phase):
    s, _ = session()
    broker = _candidate_broker(s, before)
    s.identity_broker = broker
    original = s._normalize

    def change():
        broker.identities = list(after)

    def rows(**_):
        if phase == "http":
            change()
        return [owned_row()]

    def normalize(row):
        result = original(row)
        if phase == "projection":
            change()
        return result

    s._rows = rows
    s._normalize = normalize
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.library()
        assert broker.calls == 2
    finally:
        s.close()


@pytest.mark.parametrize("owners", [
    [], ["identity-A", "identity-B"], [None], ["identity-A", None],
])
def test_stable_ambiguous_or_unresolved_identity_retains_read_only_inventory(owners):
    s, _ = session()
    broker = _candidate_broker(s, owners)
    s.identity_broker = broker
    s._rows = lambda **_: [owned_row()]
    try:
        books = s.library()
        assert len(books) == 1 and books[0].source_credential == "synthetic-account"
        assert broker.calls == 2
    finally:
        s.close()


def test_broker_account_order_does_not_create_a_false_owner_change():
    s, _ = session()
    broker = _candidate_broker(s, ["identity-A", "identity-B"])
    s.identity_broker = broker
    def rows(**_):
        broker.identities.reverse()
        return [owned_row()]
    s._rows = rows
    try:
        assert len(s.library()) == 1
        assert broker.calls == 2
    finally:
        s.close()


def test_replacing_identity_broker_with_same_candidate_is_new_epoch():
    s, _ = session()
    first = _candidate_broker(s, ["identity-A"])
    next_broker = _candidate_broker(s, ["identity-A"])
    s.identity_broker = first
    def rows(**_):
        s.identity_broker = next_broker
        return [owned_row()]
    s._rows = rows
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.library()
        assert first.calls == 1 and next_broker.calls == 1
    finally:
        s.close()


def test_unrelated_provider_binding_change_does_not_affect_numilog_account():
    s, _ = session()
    broker = _candidate_broker(s, ["identity-A"])
    s.identity_broker = broker
    original = broker.accounts
    foreign = []
    def accounts(identity_id=None):
        return original() + list(foreign)
    broker.accounts = accounts
    def rows(**_):
        foreign.append(SimpleNamespace(
            provider_id="kobo", account_id="another-account", identity_id="identity-Z",
        ))
        return [owned_row()]
    s._rows = rows
    try:
        assert len(s.library()) == 1
        assert broker.calls == 2
    finally:
        s.close()
