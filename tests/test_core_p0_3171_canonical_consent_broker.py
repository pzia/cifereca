"""#3171: CoreUI broker selections for one exact durable SourceKey right.

Real v14 SQLite. Zero provider I/O, ADEPT, downloads or legacy consent.
All account/source identifiers are synthetic and NEVER reflected to CoreUI.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.source_acquisition_consent import read_source_acquisition_consent
from cifereca.models import Account
from tests.test_core_p0_3171_opening_canonical_rights import rig
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register


def _choices(service, account, snapshot):
    persist_attested_source_references(service.storage, snapshot)
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    return broker, sid


def test_one_exact_positive_right_grant_and_revoke_are_persistent(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    try:
        broker, sid = _choices(service, account, snapshot)
        options = broker.canonical_acquisition_choices(sid)
        assert len(options) == 1
        row = options[0]
        assert set(row) == {"reference", "kind", "allowed", "eligible"}
        assert row["kind"] == "purchase"
        assert row["allowed"] is False and row["eligible"] is True
        assert len(row["reference"]) == 64
        assert all(c in "0123456789abcdef" for c in row["reference"])
        for secret in (account.credential_id, account.unique_id, source.right_id, source.edition_id):
            assert secret not in repr(options)
        assert reads == []  # SQLite only; no provider snapshot fetch.

        broker.set_canonical_acquisition_choice(sid, row["reference"], allowed=True)
        assert read_source_acquisition_consent(service, account) == frozenset({source})
        assert broker.canonical_acquisition_choices(sid)[0]["allowed"] is True
        broker.set_canonical_acquisition_choice(sid, row["reference"], allowed=False)
        assert read_source_acquisition_consent(service, account) == frozenset()
        assert broker.canonical_acquisition_choices(sid)[0]["allowed"] is False
        assert reads == [] and service.storage.placements() == []
    finally:
        service.close()


def test_scoped_token_cannot_enable_other_identity_or_edition(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    try:
        broker, sid = _choices(service, account, snapshot)
        token = broker.canonical_acquisition_choices(sid)[0]["reference"]
        second = Account(
            identity_id="B", provider_id="seller-a",
            credential_id="other-card", unique_id="buyer-B",
        )
        service.accounts.append(second)
        register(service.storage, "B", "seller-a", "other-card", account="buyer-B")
        foreign = broker._source_id(second)
        with pytest.raises(ValueError, match="selection_unavailable"):
            broker.set_canonical_acquisition_choice(foreign, token, allowed=True)
        assert read_source_acquisition_consent(service, account) == frozenset()
        assert broker.canonical_acquisition_choices(foreign) == ()

        # A different reference on the SAME account never inherits the grant.
        other = replace(source, right_id="another-right")
        broker.set_canonical_acquisition_choice(sid, token, allowed=True)
        assert read_source_acquisition_consent(service, account) == frozenset({source})
        assert other not in read_source_acquisition_consent(service, account)
    finally:
        service.close()


@pytest.mark.parametrize("bad", ["", "private", "a" * 63, "z" * 64, True, 1, [], {}])
def test_invalid_selection_does_not_touch_any_grant(tmp_path, monkeypatch, bad):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    try:
        broker, sid = _choices(service, account, snapshot)
        with pytest.raises(ValueError):
            broker.set_canonical_acquisition_choice(sid, bad, allowed=True)
        assert read_source_acquisition_consent(service, account) == frozenset()
    finally:
        service.close()


def test_inactive_right_may_revoke_but_never_grant_again(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    try:
        broker, sid = _choices(service, account, snapshot)
        token = broker.canonical_acquisition_choices(sid)[0]["reference"]
        broker.set_canonical_acquisition_choice(sid, token, allowed=True)
        with service.storage._lock, service.storage._connect() as db:
            db.execute("UPDATE source_references SET state='inactive'")
        view = broker.canonical_acquisition_choices(sid)
        assert view[0]["allowed"] is True and view[0]["eligible"] is False
        with pytest.raises(ValueError, match="positive_right_required"):
            broker.set_canonical_acquisition_choice(sid, token, allowed=True)
        broker.set_canonical_acquisition_choice(sid, token, allowed=False)
        assert broker.canonical_acquisition_choices(sid) == ()
        assert read_source_acquisition_consent(service, account) == frozenset()
    finally:
        service.close()


def test_rebind_or_duplicate_account_refuses_ui_consent(tmp_path, monkeypatch):
    service, account, source, snapshot, session, reads = rig(tmp_path, monkeypatch)
    try:
        broker, sid = _choices(service, account, snapshot)
        token = broker.canonical_acquisition_choices(sid)[0]["reference"]
        service.accounts.append(account)
        with pytest.raises(ValueError, match="ambiguous sync source"):
            broker.set_canonical_acquisition_choice(sid, token, allowed=True)
        service.accounts.pop()
        service._account_context_epoch += 1
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "UPDATE provider_connections SET identity_id='other' "
                "WHERE provider_id='seller-a' AND credential_id='card-a'"
            )
        with pytest.raises(ValueError, match="selection_unavailable"):
            broker.set_canonical_acquisition_choice(sid, token, allowed=True)
        assert service.storage.placements() == []
    finally:
        service.close()
