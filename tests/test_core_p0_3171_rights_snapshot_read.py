"""#3171: explicit scoped Core admission of optional provider rights snapshots."""
from types import SimpleNamespace
from dataclasses import replace

import pytest

from cifereca.models import Account
from cifereca.plugin_api.errors import Cancelled
from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)
from cifereca.core.rights_snapshot_read import read_account_rights_snapshot


STAMP = "2026-10-28T08:00:00+00:00"


class FakeStorage:
    def __init__(self, owners):
        self.owners = owners

    def identity_for_connection(self, provider, credential):
        return self.owners.get((provider, credential))


class ProviderSession:
    def __init__(self, provider, credential, reader=None):
        self.provider_id = provider
        self.credential_id = credential
        self.connected = True
        self._closed = False
        self.calls = 0
        self.reader = reader

    def rights_observation_snapshot(self, account):
        self.calls += 1
        if self.reader is not None:
            return self.reader(account)
        scope = ProviderRightsScope(
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )
        return ProviderRightsSnapshot(
            scope, (
                ProviderRightObservation(
                    "edition-1", "owned-1", "purchase", "active",
                    "provider_response", "synthetic-provider-proof",
                    STAMP, "unknown",
                ),
            ),
            status="partial", coverage="partial", branch="purchases",
        )


def rig(provider="provider-alpha", *, owner="A", credential="cred-A",
        buyer="buyer-A", reader=None):
    account = Account(
        identity_id=owner, provider_id=provider,
        credential_id=credential, unique_id=buyer,
    )
    session = ProviderSession(provider, credential, reader)
    session.accounts = [account]
    service = SimpleNamespace(
        _account_context_epoch=1,
        accounts=[account],
        _sessions={(provider, credential): session},
        storage=FakeStorage({(provider, credential): owner}),
    )
    return service, account, session


@pytest.mark.parametrize("provider", ["provider-alpha", "provider-beta"])
def test_two_independent_provider_adapters_one_read_exact_scope(provider):
    service, account, session = rig(provider)
    report = read_account_rights_snapshot(service, account)
    assert session.calls == 1
    assert report.status == "partial"
    assert len(report.rights) == 1
    assert report.scope == ProviderRightsScope("A", provider, "cred-A", "buyer-A")
    assert report.rights[0].edition_id == "edition-1"
    assert not report.authorizes_mutation
    assert not report.authorizes_negative_reconciliation


@pytest.mark.parametrize("change", [
    "foreign", "missing", "duplicate", "malformed", "unreadable",
])
def test_unbound_live_provider_borrower_denies_rights_read(change):
    service, account, session = rig()
    if change == "foreign":
        session.accounts = [replace(account, unique_id="other-borrower")]
    elif change == "missing":
        session.accounts = []
    elif change == "duplicate":
        session.accounts = [account, replace(account)]
    elif change == "malformed":
        session.accounts = None
    else:
        del session.accounts
    result = read_account_rights_snapshot(service, account)
    assert result.status == "failed" and result.rights == ()
    assert session.calls == 0
    assert not result.authorizes_mutation


def test_live_provider_borrower_rebound_during_rights_read_discarded():
    service, account, session = rig()

    def read(selected):
        snapshot = ProviderSession(
            session.provider_id, session.credential_id,
        ).rights_observation_snapshot(selected)
        # No Core epoch or registry replacement: only upstream session scope.
        session.accounts = [replace(account, unique_id="other-borrower")]
        return snapshot

    session.reader = read
    result = read_account_rights_snapshot(service, account)
    assert session.calls == 1
    assert result.status == "failed" and result.rights == ()
    assert not result.authorizes_mutation


def test_other_borrowers_in_same_session_do_not_block_exact_owner():
    service, account, session = rig()
    session.accounts.append(replace(account, unique_id="linked-borrower"))
    result = read_account_rights_snapshot(service, account)
    assert result.status == "partial" and len(result.rights) == 1
    assert session.calls == 1


def test_missing_optional_method_cannot_fallback_to_entitlements_or_library():
    service, account, session = rig()
    session.rights_observation_snapshot = None
    session.library = lambda: pytest.fail("legacy library must not be read")
    result = read_account_rights_snapshot(service, account)
    assert result.status == "failed" and result.rights == ()
    assert session.calls == 0


@pytest.mark.parametrize("alter", [
    "account_id", "account_identity", "account_credential",
    "connection_owner", "session_rebind", "account_epoch", "disconnect",
    "close", "replace_account", "replace_session",
])
def test_snapshot_from_changed_source_discarded_in_full(alter):
    service, account, session = rig()
    def read(selected):
        raw = ProviderSession(session.provider_id, session.credential_id)
        report = raw.rights_observation_snapshot(selected)
        if alter == "account_id":
            account.unique_id = "borrower-other"
        if alter == "account_identity":
            account.identity_id = "B"
        if alter == "account_credential":
            account.credential_id = "other"
        if alter == "connection_owner":
            service.storage.owners[("provider-alpha", "cred-A")] = "B"
        if alter == "session_rebind":
            session.credential_id = "other"
        if alter == "account_epoch":
            service._account_context_epoch += 1
        if alter == "disconnect":
            session.connected = False
        if alter == "close":
            session._closed = True
        if alter == "replace_account":
            service.accounts = [replace(account)]
        if alter == "replace_session":
            service._sessions = {("provider-alpha", "cred-A"): ProviderSession("provider-alpha", "cred-A")}
        return report
    session.reader = read
    result = read_account_rights_snapshot(service, account)
    assert session.calls == 1
    assert result.status == "failed" and result.rights == ()
    assert not result.authorizes_mutation


def test_duplicate_registered_accounts_or_sessions_rejected_before_any_read():
    service, account, session = rig()
    service.accounts.append(replace(account))
    result = read_account_rights_snapshot(service, account)
    assert result.status == "failed" and session.calls == 0
    service.accounts.pop()
    service._sessions["duplicate"] = ProviderSession("provider-alpha", "cred-A")
    result = read_account_rights_snapshot(service, account)
    assert result.status == "failed" and session.calls == 0


def test_foreign_or_unregistered_account_never_selected():
    service, account, session = rig()
    other = replace(account, unique_id="buyer-B")
    result = read_account_rights_snapshot(service, other)
    assert result.status == "failed" and session.calls == 0


def test_wrong_scope_snapshot_rejected_without_leaking_foreign_rights():
    service, account, session = rig()
    def wrong(_account):
        foreign = replace(account, unique_id="buyer-foreign")
        return ProviderSession("provider-alpha", "cred-A").rights_observation_snapshot(foreign)
    session.reader = wrong
    result = read_account_rights_snapshot(service, account)
    assert result.status == "failed" and result.rights == ()


def test_cancelled_before_and_after_read_discards_positive_rows():
    service, account, session = rig()
    result = read_account_rights_snapshot(service, account, cancelled=lambda: True)
    assert result.status == "cancelled" and session.calls == 0
    stop = [False]
    def read(selected):
        stop[0] = True
        return ProviderSession("provider-alpha", "cred-A").rights_observation_snapshot(selected)
    session.reader = read
    result = read_account_rights_snapshot(service, account, cancelled=lambda: stop[0])
    assert result.status == "cancelled" and result.rights == ()
    assert session.calls == 1


def test_provider_cancelled_exception_is_distinct_from_provider_failure(caplog):
    service, account, session = rig()
    session.reader = lambda _: (_ for _ in ()).throw(Cancelled("synthetic-user-cancel"))
    result = read_account_rights_snapshot(service, account)
    assert result.status == "cancelled" and result.rights == ()
    assert "rights snapshot read failed" not in caplog.text


def test_raw_original_failure_is_logged_and_status_is_closed(caplog):
    service, account, session = rig()
    session.reader = lambda _: (_ for _ in ()).throw(
        ValueError("synthetic-provider-error?url=/exact-route&status=403")
    )
    with caplog.at_level("WARNING", logger="cifereca"):
        result = read_account_rights_snapshot(service, account)
    assert result.status == "failed" and result.rights == ()
    assert "synthetic-provider-error?url=/exact-route&status=403" in caplog.text
    assert "account_id=buyer-A" in caplog.text
    assert session.calls == 1


def test_bounded_positive_cardinality_zero_and_n():
    service, account, session = rig()
    for count in (0, 3):
        def read(a, n=count):
            scope = ProviderRightsScope(
                a.identity_id, a.provider_id, a.credential_id, a.unique_id,
            )
            return ProviderRightsSnapshot(
                scope, tuple(
                    ProviderRightObservation(
                        f"edition-{i}", f"purchase-{i}", "purchase",
                        "active", "provider_response", f"proof-{i}", STAMP,
                    )
                    for i in range(n)
                ), status="partial", branch="owned",
            )
        session.reader = read
        snapshot = read_account_rights_snapshot(service, account)
        assert snapshot.status == "partial" and len(snapshot.rights) == count
        assert not snapshot.claims_complete_branch


@pytest.mark.parametrize("inflight_change", [
    "append_same_account", "replace_with_same_account",
    "duplicate_same_session", "replace_with_same_session",
])
def test_inplace_duplicate_or_replaced_registration_after_provider_io_is_rejected(
    inflight_change,
):
    service, account, session = rig()
    original_accounts = service.accounts
    original_sessions = service._sessions

    def read(selected):
        result = ProviderSession(
            session.provider_id, session.credential_id,
        ).rights_observation_snapshot(selected)
        if inflight_change == "append_same_account":
            service.accounts.append(replace(account))
        elif inflight_change == "replace_with_same_account":
            service.accounts[0] = replace(account)
        elif inflight_change == "duplicate_same_session":
            service._sessions["duplicate-reader"] = ProviderSession(
                session.provider_id, session.credential_id,
            )
        elif inflight_change == "replace_with_same_session":
            service._sessions[("provider-alpha", "cred-A")] = ProviderSession(
                session.provider_id, session.credential_id,
            )
        else:
            pytest.fail("unrecognized synthetic transition")
        assert service.accounts is original_accounts
        assert service._sessions is original_sessions
        assert service._account_context_epoch == 1
        return result

    session.reader = read
    result = read_account_rights_snapshot(service, account)
    assert session.calls == 1
    assert result.status == "failed"
    assert result.rights == ()
    assert result.coverage == "unknown"
    assert not result.authorizes_mutation


def test_unrelated_registered_session_does_not_invalidate_selected_account():
    service, account, session = rig()
    def read(selected):
        service._sessions["unrelated"] = ProviderSession("provider-beta", "other")
        return ProviderSession(
            session.provider_id, session.credential_id,
        ).rights_observation_snapshot(selected)
    session.reader = read
    result = read_account_rights_snapshot(service, account)
    assert result.status == "partial"
    assert len(result.rights) == 1
    assert session.calls == 1


def test_distinct_borrower_same_credential_does_not_steal_selected_account():
    service, account, session = rig()
    def read(selected):
        service.accounts.append(replace(account, unique_id="borrower-sibling"))
        return ProviderSession(
            session.provider_id, session.credential_id,
        ).rights_observation_snapshot(selected)
    session.reader = read
    result = read_account_rights_snapshot(service, account)
    assert result.status == "partial"
    assert result.scope.account_id == "buyer-A"
    assert session.calls == 1
