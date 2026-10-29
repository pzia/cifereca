"""Session owner lookup must never default to the UI's current identity."""
from dataclasses import replace
from itertools import permutations
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_rights_observation_snapshot import _rig


IDENTITIES = ("hugues", "heloise", "alexandre")


class SelectorBroker:
    """Public accounts(identity_id) semantics, with exact credential refs."""

    def __init__(self):
        self.selected = IDENTITIES[0]
        self.calls = []
        self.refs = [NS(
            provider_id="kobo", account_id="alice", identity_id=identity,
            credential_id="vault-" + identity, scope_version=2,
        ) for identity in IDENTITIES]

    def accounts(self, identity_id=None):
        self.calls.append(identity_id)
        target = self.selected if identity_id is None else identity_id
        return tuple(ref for ref in self.refs if ref.identity_id == target)


def rig(identity, broker):
    session, vault, _, account = _rig([
        {"product_id": "book-" + identity, "account_id": "alice", "owned": True},
    ])
    session.identity_broker = broker
    session.credential_id = session.catalog.credential_id = "vault-" + identity
    for row in (account, session._accounts[0]):
        row.identity_id = identity
        row.credential_id = session.credential_id
    return session, vault, account


@pytest.mark.parametrize("selected", IDENTITIES)
@pytest.mark.parametrize("order", tuple(permutations(IDENTITIES)))
def test_individual_and_global_reads_are_identical_for_every_selector_and_order(
    selected, order,
):
    broker = SelectorBroker()
    broker.selected = selected
    sessions = {identity: rig(identity, broker) for identity in IDENTITIES}
    try:
        for identity in order:
            session, vault, account = sessions[identity]
            first = session.rights_observation_snapshot(account)
            # A second individual sync after another UI selection must
            # produce the same factual observations, apart from freshness.
            broker.selected = IDENTITIES[(IDENTITIES.index(identity) + 1) % 3]
            second = session.rights_observation_snapshot(account)
            assert first.scope == second.scope
            assert first.scope.identity_id == identity
            assert first.scope.credential_id == "vault-" + identity
            assert [replace(r, observed_at="2026-10-29T00:00:00+00:00") for r in first.rights] == [
                replace(r, observed_at="2026-10-29T00:00:00+00:00") for r in second.rights
            ]
            assert [r.right_id for r in first.rights] == ["book-" + identity]
            assert first.coverage == second.coverage == "partial"
            assert vault.calls == 2
        assert None not in broker.calls
    finally:
        for session, _, _ in sessions.values():
            session.close()


def test_selector_change_during_manifest_is_not_a_session_rebind():
    broker = SelectorBroker()
    session, vault, account = rig("heloise", broker)
    original = vault.library_manifest

    def read():
        rows = original()
        broker.selected = "alexandre"
        return rows

    vault.library_manifest = read
    try:
        report = session.rights_observation_snapshot(account)
        assert report.scope.identity_id == "heloise"
        assert broker.calls == ["heloise"] * 3
    finally:
        session.close()


@pytest.mark.parametrize("change", ["missing", "duplicate", "foreign_credential"])
def test_exact_owner_still_required_even_when_ui_selects_target(change):
    broker = SelectorBroker()
    session, vault, account = rig("hugues", broker)
    if change == "missing":
        broker.refs = broker.refs[1:]
    elif change == "duplicate":
        broker.refs.append(broker.refs[0])
    else:
        broker.refs[0].credential_id = "foreign-credential"
    try:
        with pytest.raises(ProviderCapabilityError, match="propriétaire unique"):
            session.rights_observation_snapshot(account)
        assert vault.calls == 0
    finally:
        session.close()


@pytest.mark.parametrize("identity", [None, "", 42])
def test_invalid_session_identity_never_falls_back_to_ui(identity):
    broker = SelectorBroker()
    session, vault, account = rig("hugues", broker)
    session._accounts[0].identity_id = identity
    try:
        with pytest.raises(ProviderCapabilityError):
            session.rights_observation_snapshot(account)
        assert broker.calls == []
        assert vault.calls == 0
    finally:
        session.close()
