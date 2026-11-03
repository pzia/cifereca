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
    def __init__(self, owners, bound_accounts=()):
        self.owners = owners
        self.bound_accounts = set(bound_accounts)

    def identity_for_connection(self, provider, credential):
        return self.owners.get((provider, credential))

    def provider_scope_account_bound(
        self, *, identity_id, provider_id, credential_id, account_id,
    ):
        return (
            self.owners.get((provider_id, credential_id)) == identity_id
            and (identity_id, provider_id, credential_id, account_id)
            in self.bound_accounts
        )


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
        _source_context_epoch=1,
        accounts=[account],
        _sessions={(provider, credential): session},
        sync_source_enabled=lambda selected: selected is account,
        storage=FakeStorage(
            {(provider, credential): owner},
            {(owner, provider, credential, buyer)},
        ),
    )
    return service, account, session


@pytest.mark.parametrize("change", ["missing", "sibling_only", "foreign_owner"])
def test_deleted_or_reassigned_sql_borrower_blocks_first_private_get(change):
    service, account, session = rig()
    if change == "missing":
        service.storage.bound_accounts.clear()
    elif change == "sibling_only":
        service.storage.bound_accounts.clear()
        service.storage.bound_accounts.add((
            "A", "provider-alpha", "cred-A", "other-borrower",
        ))
    else:
        service.storage.owners[("provider-alpha", "cred-A")] = "B"
        service.storage.bound_accounts.clear()
        service.storage.bound_accounts.add((
            "B", "provider-alpha", "cred-A", "buyer-A",
        ))
    result = read_account_rights_snapshot(service, account)
    assert result.status == "failed" and result.rights == ()
    assert session.calls == 0


def test_sql_borrower_removed_during_private_read_discards_entire_batch():
    service, account, session = rig()

    def revoke(_account):
        service.storage.bound_accounts.clear()
        return ProviderSession(
            session.provider_id, session.credential_id,
        ).rights_observation_snapshot(account)

    session.reader = revoke
    result = read_account_rights_snapshot(service, account)
    assert session.calls == 1
    assert result.status == "failed" and result.rights == ()
    assert result.coverage == "unknown"
    assert not result.authorizes_mutation


def test_sql_borrower_removed_between_pages_prevents_next_private_get():
    service, account, session = rig()
    calls = []
    first = ProviderSession(
        session.provider_id, session.credential_id,
    ).rights_observation_snapshot(account)

    def paged(_account, *, resume_cursor=""):
        calls.append(resume_cursor)
        if resume_cursor:
            pytest.fail("no private continuation after SQL borrower revocation")
        service.storage.bound_accounts.clear()
        return replace(first, resume_cursor="cursor-after-first")

    session.rights_observation_snapshot = paged
    result = read_account_rights_snapshot(service, account)
    assert result.status == "failed" and result.rights == ()
    assert calls == [""]


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
            service._source_context_epoch += 1
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


def test_provider_failure_logs_closed_reason_without_private_values(caplog):
    service, account, session = rig(
        provider="provider-private", owner="PRIVATE_IDENTITY",
        credential="PRIVATE_CREDENTIAL", buyer="PRIVATE_BORROWER",
    )
    signed = "https://delivery.example.invalid/PRIVATE_SIGNED?key=SECRET"
    session.reader = lambda _: (_ for _ in ()).throw(
        ValueError("provider failure: " + signed)
    )
    with caplog.at_level("WARNING", logger="cifereca"):
        result = read_account_rights_snapshot(service, account)
    assert result.status == "failed" and result.rights == ()
    assert "rights snapshot read failed code=provider_error" in caplog.text
    assert "reader_kind=rights_observation_snapshot" in caplog.text
    for secret in (
        signed, "PRIVATE_IDENTITY", "PRIVATE_CREDENTIAL", "PRIVATE_BORROWER",
        "PRIVATE_SIGNED", "SECRET", "provider failure:",
    ):
        assert secret not in caplog.text
    assert session.calls == 1


def test_pagination_failure_keeps_positive_prefix_and_redacts_provider_error(caplog):
    service, account, session = rig()
    private_error = "https://supplier.example.invalid/SIGNED?secret=PRIVATE_TOKEN"
    observed = ProviderSession(
        session.provider_id, session.credential_id,
    ).rights_observation_snapshot(account)

    def paged(selected, *, resume_cursor=""):
        if resume_cursor:
            raise RuntimeError(private_error)
        assert selected is account
        return replace(observed, resume_cursor="PRIVATE_CURSOR")

    session.rights_observation_snapshot = paged
    with caplog.at_level("WARNING", logger="cifereca"):
        result = read_account_rights_snapshot(service, account)
    assert result.status == result.coverage == "partial"
    assert len(result.rights) == 1
    assert result.resume_cursor == "PRIVATE_CURSOR"
    assert "TRACE rights pagination code=page_unavailable" in caplog.text
    assert private_error not in caplog.text
    assert "PRIVATE_TOKEN" not in caplog.text
    assert "PRIVATE_CURSOR" not in caplog.text
    assert "buyer-A" not in caplog.text


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
        assert service._source_context_epoch == 1
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
