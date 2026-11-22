"""#976: exact historical transaction, consent, runtime stable identity and revocation."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.adept_historical_inventory_operator import admit_inventory_comparison
from cifereca.core.adept_historical_maintenance import admit_historical_review, run_historical_review
from tests.test_core_p0_976_historical_maintenance import _setup, _ledger
from tests.test_core_p0_3171_atomic_publication_v14 import store, rows, BYTES


def _fixture(store, tmp_path):
    service, source, path, publication = _setup(store, tmp_path)
    calls = []
    loans = (
        {"id": "adobe-loan", "returned": True, "expired": True, "token_present": False},
        {"id": "FOREIGN-LOAN", "returned": False, "expired": False, "token_present": True,
         "title": "FOREIGN-SECRET", "local_book": "/FOREIGN/private.epub"},
    )
    def read(profile, *, consented, control, stable_id_only):
        calls.append((profile, consented, stable_id_only))
        assert not control.cancelled()
        return NS(status="complete", stable_profile_observed=True, profile_reference=profile, loans=loans)
    runtime = NS(loan_inventory_readonly=read)
    descriptor = NS(id="fixture.runtime", type="runtime.adapter", capabilities=("loan.inventory.readonly",))
    status = NS(descriptor=descriptor, enabled=True, state="ready")
    manager = NS(candidates=lambda *a, **kw: [status], status=lambda _: status, service=lambda _: runtime)
    service.plugin_manager = manager
    request = admit_historical_review(service)
    row, = run_historical_review(service, request)["rows"]
    return service, request, row, path, calls, runtime, manager, status


def test_explicit_historical_comparison_once_without_current_profile_or_foreign_projection(store, tmp_path):
    service, request, row, path, calls, runtime, _, _ = _fixture(store, tmp_path)
    before = rows(store), store.acquisition_get("history-0"), path.read_bytes()
    assert admit_inventory_comparison(service, request, row) is None
    assert not calls
    operation = admit_inventory_comparison(service, request, row, explicitly_requested=True)
    result = operation()
    assert result["inventory_comparison"] == "exact_profile_and_loan_reported"
    assert result["adept_license_state_reported"] == "returned_reported"
    assert result["provider_loan_state"] == "not_checked"
    assert not result["activation_attested"] and not result["inventory_transport_attested"]
    assert not result["may_rebind_or_return"] and not result["may_reacquire"]
    assert "FOREIGN" not in repr(result) and "adobe-loan" not in repr(result)
    assert operation() is None
    assert calls == [("historical-adobe", True, True)]
    assert before == (rows(store), store.acquisition_get("history-0"), path.read_bytes())
    assert path.read_bytes() == BYTES


@pytest.mark.parametrize("change", ["row", "profile", "ambiguous_runtime", "revocation", "manager", "ledger"])
def test_invalid_selection_or_revoked_scope_never_reads_runtime(store, tmp_path, change):
    service, request, row, _, calls, runtime, manager, status = _fixture(store, tmp_path)
    if change == "row":
        row = {**row, "acquisition_id": "foreign"}
    elif change == "profile":
        row = {**row, "recorded_adept_profile_id": "foreign"}
    operation = admit_inventory_comparison(service, request, row, explicitly_requested=True)
    assert operation is not None
    if change == "ambiguous_runtime":
        manager.candidates = lambda *a, **kw: [status, status]
    elif change == "revocation":
        service._account_context_epoch += 2
    elif change == "manager":
        service.plugin_manager = NS()
    elif change == "ledger":
        with store._lock, store._connect() as db:
            db.execute("UPDATE acquisition_ledger SET status='retryable_error' WHERE acquisition_id='history-0'")
    assert operation() is None
    assert not calls


@pytest.mark.parametrize("change", ["scope", "runtime", "alias", "partial", "ledger"])
def test_inventory_changes_during_read_refuse_comparison(store, tmp_path, change):
    service, request, row, _, calls, runtime, manager, _ = _fixture(store, tmp_path)
    read = runtime.loan_inventory_readonly
    def changed(*a, **kw):
        report = read(*a, **kw)
        if change == "scope":
            service._account_context_epoch += 2
        elif change == "runtime":
            manager.service = lambda _: NS(loan_inventory_readonly=read)
        elif change == "alias":
            report.stable_profile_observed = False
        elif change == "partial":
            report.status = "unavailable"
        else:
            with store._lock, store._connect() as db:
                db.execute("UPDATE acquisition_ledger SET status='retryable_error' WHERE acquisition_id='history-0'")
        return report
    runtime.loan_inventory_readonly = changed
    operation = admit_inventory_comparison(service, request, row, explicitly_requested=True)
    assert operation() is None
    assert len(calls) == 1


def test_exact_inventory_absence_is_not_absence_of_right(store, tmp_path):
    service, request, row, _, calls, runtime, _, _ = _fixture(store, tmp_path)
    read = runtime.loan_inventory_readonly
    def other(*a, **kw):
        report = read(*a, **kw)
        report.loans = report.loans[1:]
        return report
    runtime.loan_inventory_readonly = other
    result = admit_inventory_comparison(service, request, row, explicitly_requested=True)()
    assert result["inventory_comparison"] == "not_found_in_supplied_inventory"
    assert result["provider_loan_state"] == "not_checked"
    assert result["may_reacquire"] is False and result["may_rebind_or_return"] is False


@pytest.mark.parametrize("extras", [255, 256, 1024])
def test_historical_inventory_adapter_capabilities_have_bounded_receipt(
    store, tmp_path, extras,
):
    service, request, row, path, calls, _runtime, manager, status = _fixture(
        store, tmp_path,
    )
    before = rows(store), path.read_bytes()
    status.descriptor.capabilities = (
        "loan.inventory.readonly",
        *(f"synthetic.capability.{i}" for i in range(extras)),
    )
    operation = admit_inventory_comparison(
        service, request, row, explicitly_requested=True,
    )
    assert operation is not None
    result = operation()
    assert operation() is None
    if extras == 255:
        assert result is not None
        assert result["inventory_comparison"] == "exact_profile_and_loan_reported"
        assert calls == [("historical-adobe", True, True)]
        assert result["may_reacquire"] is False
    else:
        assert result is None
        assert calls == []
    assert (rows(store), path.read_bytes()) == before
