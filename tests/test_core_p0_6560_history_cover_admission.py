"""Queued visual history fallback: exact source, no UI navigation authority."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.library_projection import capture_history_cover_lookup
from cifereca.services import LibraryService
from tests.test_core_p0_5851_history_live_source_lease import _service
from tests.test_core_p0_5851_history_cover_scope import _result
from tests.test_history_cover_resolver_coordination import _resolver_types


def _rig():
    fake, account, other, session, state, _, _, writes = _service()
    service = LibraryService.__new__(LibraryService)
    service.__dict__.update(vars(fake))
    service._history_source_lease_current = (
        lambda account, session, *, scope, epoch:
        LibraryService._history_source_lease_current(
            service, account, session, scope=scope, epoch=epoch,
        )
    )
    account.is_primary = True
    reads = []
    cache = []

    def search(*args, **kwargs):
        reads.append("alice")
        return [_result("https://covers.example/ALICE")]

    session.search = search
    service.storage.cache_get = lambda *args: cache.append(args)
    return service, account, other, session, state, reads, writes, cache


def _capture(service, account):
    return capture_history_cover_lookup(
        service, account, provider="ORIGIN", record_id="123",
        title="Synthetic", author=None, year=None, default_url=None,
    )


@pytest.mark.parametrize("aba", [False, True])
def test_native_resolver_captures_before_queue_and_never_reads_new_borrower(aba):
    service, account, other, session, state, reads, writes, cache = _rig()
    resolver_type, workers, idle = _resolver_types()
    resolver = resolver_type(service, NS(add=lambda *a, **k: None))
    delivered = []
    args = ("ORIGIN", "123", "Synthetic", None, None, None)
    resolver.request(*args, delivered.append, scope="original")
    assert len(workers) == 1 and reads == cache == []
    service.current_account = other
    service._account_context_epoch += 1
    if aba:
        service.current_account = account
        service._account_context_epoch += 1
    future, fn, arguments = workers.pop()
    future.set_result(fn(*arguments))
    assert reads == ["alice"]
    assert len(writes) == 1
    assert writes[0][2]["url"] == "https://covers.example/ALICE"
    for fn, args in idle:
        fn(*args)
    assert delivered == ["https://covers.example/ALICE"]
    resolver.close()


@pytest.mark.parametrize("revocation", ["source", "binding", "session"])
@pytest.mark.parametrize("during_get", [False, True])
def test_revocation_refuses_result_and_cache(revocation, during_get):
    service, account, _, session, state, reads, writes, cache = _rig()
    admitted = _capture(service, account)
    assert admitted is not None
    _, work, _ = admitted

    def revoke():
        if revocation == "source":
            service._source_context_epoch += 2
        elif revocation == "binding":
            state["account_bound"] = False
        else:
            service._sessions[(account.provider_id, account.credential_id)] = NS(**vars(session))

    if during_get:
        original = session.search
        def search(*a, **k):
            rows = original(*a, **k)
            revoke()
            return rows
        session.search = search
    else:
        revoke()
    assert work() == (None, "error")
    assert reads == (["alice"] if during_get else [])
    assert len(cache) == int(during_get)
    assert writes == []


def test_native_resolver_does_not_replay_memory_after_persisted_binding_revoked():
    service, account, _, session, state, reads, writes, cache = _rig()
    resolver_type, workers, idle = _resolver_types()
    resolver = resolver_type(service, NS(add=lambda *a, **kw: None))
    delivered = []
    args = ("ORIGIN", "123", "Synthetic", None, None, None)

    def drain():
        future, fn, arguments = workers.pop()
        future.set_result(fn(*arguments))
        while idle:
            fn, arguments = idle.pop(0)
            fn(*arguments)

    resolver.request(*args, delivered.append, scope="same")
    drain()
    state["account_bound"] = False
    resolver.request(*args, delivered.append, scope="same")
    assert len(workers) == 1
    drain()
    assert delivered == ["https://covers.example/ALICE", None]
    assert reads == ["alice"] and len(cache) == len(writes) == 1
    resolver.close()


def test_admission_rejects_nul_coordinate_before_queue():
    service, account, _, _, _, reads, writes, cache = _rig()
    account.unique_id = "invalid\x00borrower"
    assert _capture(service, account) is None
    assert reads == writes == cache == []


def test_reconnect_in_place_does_not_join_an_old_pending_cover():
    service, account, _, session, _, reads, writes, cache = _rig()
    resolver_type, workers, idle = _resolver_types()
    resolver = resolver_type(service, NS(add=lambda *a, **k: None))
    args = ("ORIGIN", "123", "Synthetic", None, None, None)
    old, new = [], []
    session._epoch = 1
    resolver.request(*args, old.append, scope="same")
    # Provider reconnects its own session object without changing the
    # Core source epoch or the displayed account.
    session._epoch = 2
    resolver.request(*args, new.append, scope="same")
    assert len(workers) == 2
    for future, fn, arguments in workers:
        future.set_result(fn(*arguments))
    for fn, arguments in idle:
        fn(*arguments)
    assert old == []
    assert new == ["https://covers.example/ALICE"]
    assert reads == ["alice"] and len(writes) == len(cache) == 1
    resolver.close()
