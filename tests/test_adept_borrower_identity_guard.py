"""Focused borrower-provenance safety regression tests (#976)."""
from types import SimpleNamespace

from cifereca.services import LibraryService


def account(identity, borrower, provider="paris"):
    return SimpleNamespace(identity_id=identity, unique_id=borrower, provider_id=provider)


def conflict(accounts, borrower="borrower-a", identity="alice"):
    return LibraryService._borrower_identity_conflict(
        accounts, provider_id="paris", account_id=borrower, identity_id=identity
    )


def test_single_borrower_bound_to_matching_identity():
    assert not conflict([account("alice", "borrower-a")])


def test_shared_linked_borrower_across_two_identities_is_blocked():
    rows = [account("alice", "borrower-a"), account("bob", "borrower-a")]
    assert conflict(rows, identity="alice")
    assert conflict(list(reversed(rows)), identity="bob")


def test_borrower_bound_to_different_identity_is_blocked():
    assert conflict([account("bob", "borrower-a")], identity="alice")


def test_separate_borrower_or_provider_does_not_create_false_conflict():
    assert not conflict([account("alice", "borrower-a"),
                         account("bob", "borrower-b"),
                         account("bob", "borrower-a", "kobo")])


def test_empty_borrower_identifier_is_blocked():
    assert conflict([account("alice", "borrower-a")], borrower="")


def test_missing_borrower_ownership_is_blocked():
    assert conflict([], borrower="borrower-a")


def test_unrelated_account_does_not_prove_borrower_identity():
    assert conflict([account("alice", "borrower-other")])


def transaction(identity, loan_id, *, strategy="loan_auto"):
    return {"identity_id": identity, "strategy": strategy,
            "metadata": {"provider_loan_id": loan_id}}


def test_same_loan_transaction_under_another_identity_is_blocked():
    rows = [transaction("alice", "loan-123")]
    assert LibraryService._cross_identity_loan_conflict(
        rows, provider_loan_id="loan-123", identity_id="bob"
    )


def test_same_loan_transaction_under_same_identity_is_allowed():
    rows = [transaction("alice", "loan-123")]
    assert not LibraryService._cross_identity_loan_conflict(
        rows, provider_loan_id="loan-123", identity_id="alice"
    )


def test_different_loan_does_not_block_new_acquisition():
    rows = [transaction("alice", "loan-other")]
    assert not LibraryService._cross_identity_loan_conflict(
        rows, provider_loan_id="loan-123", identity_id="bob"
    )


def test_unknown_loan_id_is_not_sufficient_for_adept():
    assert LibraryService._cross_identity_loan_conflict(
        [], provider_loan_id="", identity_id="alice"
    )


def test_automatic_acquisitions_are_serialized_in_process(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    from time import sleep

    service = object.__new__(LibraryService)
    counters = {"active": 0, "peak": 0}
    monitor = Lock()

    def guarded(self, loan, *, force_refresh=False):
        with monitor:
            counters["active"] += 1
            counters["peak"] = max(counters["peak"], counters["active"])
        sleep(0.02)
        with monitor:
            counters["active"] -= 1
        return {"status": "unchanged"}

    from pathlib import Path
    import cifereca.services as services

    class FakeInterProcessLock:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(services, "InterProcessLock", FakeInterProcessLock)
    monkeypatch.setattr(services, "acquisition_lock_file", lambda: Path("/tmp/cifereca-acquisition.lock"))
    monkeypatch.setattr(LibraryService, "_acquire_active_loan_automatically_guarded", guarded)
    loans = [SimpleNamespace(id=str(i), source_provider="paris") for i in range(6)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(service._acquire_active_loan_automatically, loans))
    assert len(results) == 6
    assert counters["peak"] == 1


def test_automatic_acquisition_lock_uses_same_path_for_shared_loan(monkeypatch):
    from pathlib import Path
    import cifereca.services as services

    paths = []

    class FakeInterProcessLock:
        def __init__(self, path):
            paths.append(path)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(services, "InterProcessLock", FakeInterProcessLock)
    monkeypatch.setattr(services, "acquisition_lock_file", lambda: Path("/tmp/acquisition.lock"))
    monkeypatch.setattr(
        LibraryService, "_acquire_active_loan_automatically_guarded",
        lambda self, loan, **kwargs: {"status": "unchanged"},
    )
    service = object.__new__(LibraryService)
    for identity in ("alice", "bob"):
        loan = SimpleNamespace(id="shared-123", source_provider="paris", identity_id=identity)
        service._acquire_active_loan_automatically(loan)
    assert len(paths) == 2
    assert paths[0] == paths[1]
    assert "shared-123" not in paths[0].name


def test_manual_reacquisition_rejects_unbound_connection_without_mutation(monkeypatch):
    from pathlib import Path
    import cifereca.services as services

    class FakeLock:
        def __init__(self, path):
            self.path = path
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False

    monkeypatch.setattr(services, "InterProcessLock", FakeLock)
    monkeypatch.setattr(services, "acquisition_lock_file", lambda: Path("/tmp/acquisition.lock"))
    service = object.__new__(LibraryService)
    service.can_download_loan = lambda loan: True
    service.storage = SimpleNamespace(identity_for_connection=lambda *args: None)
    service.identity_by_id = lambda identity_id: None
    loan = SimpleNamespace(id="loan-1", source_provider="paris", source_credential="paris-a")
    result = service.reacquire_digital_loan(loan)
    assert result["status"] == "attention_required"
    assert result["code"] == "adept_identity_unproven"


def test_manual_reacquisition_rejects_shared_borrower_before_ledger_update(monkeypatch):
    from pathlib import Path
    import cifereca.services as services

    class FakeLock:
        def __init__(self, path):
            self.path = path
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False

    monkeypatch.setattr(services, "InterProcessLock", FakeLock)
    monkeypatch.setattr(services, "acquisition_lock_file", lambda: Path("/tmp/acquisition.lock"))
    service = object.__new__(LibraryService)
    service.can_download_loan = lambda loan: True
    service.storage = SimpleNamespace(identity_for_connection=lambda *args: "alice")
    service.identity_by_id = lambda identity_id: SimpleNamespace(identity_id=identity_id)
    service.accounts = [account("alice", "borrower-a"), account("bob", "borrower-a")]
    loan = SimpleNamespace(id="loan-1", source_provider="paris",
                           source_credential="paris-a", source_account_id="borrower-a")
    result = service.reacquire_digital_loan(loan)
    assert result["status"] == "attention_required"
    assert result["code"] == "adept_borrower_ambiguous"


def test_auto_acquisition_missing_loan_id_fails_closed():
    service = object.__new__(LibraryService)
    loan = SimpleNamespace(id="", source_provider="paris")
    result = service._acquire_active_loan_automatically(loan)
    assert result["status"] == "attention_required"
    assert result["code"] == "adept_loan_identity_unproven"


def test_auto_acquisition_lock_busy_never_enters_fulfillment(monkeypatch):
    from pathlib import Path
    import cifereca.services as services

    class BusyLock:
        def __init__(self, path):
            self.path = path
        def __enter__(self):
            raise services.LockBusyError("busy")
        def __exit__(self, *_):
            return False

    monkeypatch.setattr(services, "InterProcessLock", BusyLock)
    monkeypatch.setattr(services, "acquisition_lock_file", lambda: Path("/tmp/acquisition.lock"))

    def forbidden(*args, **kwargs):
        raise AssertionError("ADEPT fulfillment must not run")

    monkeypatch.setattr(LibraryService, "_acquire_active_loan_automatically_guarded", forbidden)
    service = object.__new__(LibraryService)
    result = service._acquire_active_loan_automatically(
        SimpleNamespace(id="loan-1", source_provider="paris")
    )
    assert result["status"] == "attention_required"
    assert result["code"] == "acquisition_busy"


def test_cross_identity_active_entitlement_blocks_when_acquisition_missing():
    rights = [{"loan_id": "loan-123", "identity_id": "alice"}]
    assert LibraryService._cross_identity_loan_conflict(
        [], provider_loan_id="loan-123", identity_id="bob",
        entitlements=rights,
    )


def test_same_identity_active_entitlement_does_not_block():
    rights = [{"loan_id": "loan-123", "identity_id": "alice"}]
    assert not LibraryService._cross_identity_loan_conflict(
        [], provider_loan_id="loan-123", identity_id="alice",
        entitlements=rights,
    )


def test_different_entitlement_loan_does_not_block():
    rights = [{"loan_id": "loan-456", "identity_id": "alice"}]
    assert not LibraryService._cross_identity_loan_conflict(
        [], provider_loan_id="loan-123", identity_id="bob",
        entitlements=rights,
    )
