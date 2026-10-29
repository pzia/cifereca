"""Exact Numilog purchase references are independent of UI and local layout."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import Account, ProviderCapabilityError
from cifereca_numilog import NumilogSession


IDENTITIES = ("hugues", "heloise", "alexandre")
STAMP = "2026-10-29T00:00:00+00:00"


class SelectorBroker:
    def __init__(self):
        self.selected = "hugues"
        self.calls = []
        self.refs = []

    def accounts(self, identity_id=None):
        self.calls.append(identity_id)
        target = self.selected if identity_id is None else identity_id
        return tuple(ref for ref in self.refs if ref.identity_id == target)


def rig(identity, broker):
    s = NumilogSession(
        credential_id="card-" + identity, label="Synthetic", credentials={},
        options={}, config={}, diagnostics=None, identity_broker=broker,
    )
    account = Account(
        provider_id="numilog", credential_id=s.credential_id,
        unique_id="numilog:" + s.scope, identity_id=identity,
    )
    s._accounts = [account]
    s.connected = True
    broker.refs.append(NS(
        provider_id="numilog", credential_id=s.credential_id,
        account_id=account.unique_id, identity_id=identity, scope_version=2,
    ))
    rows = [dict(lid=str(n), variant="()", title="Achat " + str(n),
                 cover=None, public_url=None) for n in range(1, 7)]
    reads = []

    def read(**kwargs):
        reads.append(kwargs)
        assert not kwargs.get("include_delivery")
        return list(rows)

    s._rows = read
    return s, account, reads


@pytest.mark.parametrize("selected", IDENTITIES)
@pytest.mark.parametrize("target", IDENTITIES)
def test_six_purchases_keep_same_references_with_other_selector_and_subdirectories(
    selected, target, tmp_path,
):
    broker = SelectorBroker()
    broker.selected = selected
    s, account, reads = rig(target, broker)
    try:
        before = s.rights_observation_snapshot(account)
        # Local layout is owned by Core. The provider supplies all six exact
        # purchase refs before any artifact/SHA/placement reconciliation.
        for index in range(1, 7):
            folder = tmp_path / target / "numilog"
            if index > 3:
                folder /= "acsm-" + str(index)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / (str(index) + ".epub")).write_bytes(b"synthetic-local-file")
        broker.selected = IDENTITIES[(IDENTITIES.index(selected) + 1) % 3]
        after = s.rights_observation_snapshot(account)
        library = s.library()
        assert len(before.rights) == len(after.rights) == len(library) == 6
        assert [replace(r, observed_at=STAMP) for r in before.rights] == [
            replace(r, observed_at=STAMP) for r in after.rights
        ]
        assert [r.right_id for r in after.rights] == [r.record_id for r in library]
        assert after.scope.identity_id == target
        assert after.scope.account_id == account.unique_id
        assert after.scope.credential_id == s.credential_id
        assert after.coverage == "partial"
        assert all(r.evidence_ref == "numilog.meslivres" for r in after.rights)
        assert all(r.delivery == "unknown" for r in after.rights)
        assert not after.authorizes_negative_reconciliation
        assert all(value == target for value in broker.calls)
        assert reads == [dict(rights_observation=True)] * 2 + [{}]
        assert len(list(tmp_path.rglob("*.epub"))) == 6
    finally:
        s.close()


def test_selector_change_during_read_is_not_an_owner_change():
    broker = SelectorBroker()
    s, account, _ = rig("heloise", broker)
    read = s._rows

    def change(**kwargs):
        broker.selected = "alexandre"
        return read(**kwargs)

    s._rows = change
    try:
        assert len(s.rights_observation_snapshot(account).rights) == 6
        assert broker.calls == ["heloise"] * 3
    finally:
        s.close()


@pytest.mark.parametrize("change", ["missing", "duplicate", "credential", "rebind"])
def test_invalid_exact_owner_still_refused_without_read(change):
    broker = SelectorBroker()
    s, account, reads = rig("hugues", broker)
    if change == "missing":
        broker.refs.clear()
    elif change == "duplicate":
        broker.refs *= 2
    elif change == "credential":
        broker.refs[0].credential_id = "foreign"
    else:
        broker.refs[0].identity_id = "alexandre"
    try:
        with pytest.raises(ProviderCapabilityError):
            s.rights_observation_snapshot(account)
        assert reads == []
    finally:
        s.close()


@pytest.mark.parametrize("identity", [None, "", 42])
def test_missing_session_identity_cannot_use_the_selected_profile(identity):
    broker = SelectorBroker()
    s, account, reads = rig("hugues", broker)
    caller = replace(account)
    account.identity_id = identity
    try:
        with pytest.raises(ProviderCapabilityError):
            s.rights_observation_snapshot(caller)
        assert broker.calls == []
        assert reads == []
    finally:
        s.close()
