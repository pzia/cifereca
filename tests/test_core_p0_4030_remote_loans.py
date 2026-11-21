"""#4030: one explicit typed private inventory read using synthetic transport."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import local_origin_remote_loans as remote
from cifereca.core.local_origin_suggestion_operator import run_local_origin_suggestion
from cifereca.plugin_api.loan_inventory import (
    LoanInventoryScope, LoanInventorySnapshot, LoanBranchCoverage, read_loan_inventory,
)
from tests.test_core_p0_4030_private_catalogue import private_rig, rig, store, rows


@pytest.fixture
def case(private_rig, tmp_path, monkeypatch):
    service, request, account, session, calls, path, key, snap = private_rig
    session.capabilities = NS(loans=True)
    session.loans = lambda *_: pytest.fail("legacy fallback not admitted")
    session.search = lambda *_: pytest.fail("no local title sent")
    session.download = lambda *_: pytest.fail("no delivery")
    # The remote catalogue may exist before any exact operational A is cached.
    with service.storage._lock, service.storage._connect() as db:
        db.execute("DELETE FROM source_references")
    scope = LoanInventoryScope.for_account(account)
    observed = replace(service.last_loans[0], id="fresh-loan")
    def reader(selected):
        assert selected is account
        calls.append("typed_inventory")
        return LoanInventorySnapshot(
            scope, (observed,), status="success",
            branches=(LoanBranchCoverage("loans", "digital", "complete", True),),
            observed_at="2026-01-01T00:00:00Z",
        )
    session.loan_inventory_snapshot = reader
    monkeypatch.setattr(remote, "acquisition_lock_file", lambda: tmp_path / "acquire.lock")
    known = run_local_origin_suggestion(service, request)
    evidence = known["public_comparison_evidence"]
    choice, = remote.available_remote_loan_catalogues(service, request, evidence=evidence)
    assert not calls
    return service, request, account, session, path, calls, choice, observed


def test_remote_loan_review_only_sends_selected_account_and_never_persists(case):
    service, request, account, session, path, calls, choice, observed = case
    before, data = rows(service.storage), path.read_bytes()
    memory = list(service.last_loans)
    assert remote.compare_remote_loan_catalogue(service, choice)["status"] == "excluded"
    assert calls == []
    result = remote.compare_remote_loan_catalogue(service, choice, confirmed=True)
    assert result["status"] == "partial"
    match, = result["candidates"]
    assert match.loan_id == "fresh-loan" and match.edition_id == observed.record_id
    assert match.comparison == "title_author_concordant"
    assert not hasattr(match, "source") and not hasattr(match, "branch")
    assert result["observed_at"] == "2026-01-01T00:00:00Z"
    assert result["provider_read_requested"] is True
    assert not any(result[k] for k in ("coverage_complete", "fresh_network_verified",
                                      "association_authorized", "may_write_origin_or_right",
                                      "local_description_transmitted"))
    assert calls == ["typed_inventory"] and service.last_loans == memory
    assert rows(service.storage) == before and path.read_bytes() == data


@pytest.mark.parametrize("change", ["disabled", "session", "account", "reader", "missing_reader",
                                    "file", "navigation", "cancel", "sql_borrower"])
def test_stale_or_unqualified_remote_choice_never_requests_inventory(case, change):
    service, request, account, session, path, calls, choice, observed = case
    opts = {}
    if change == "disabled":
        service.sync_source_enabled = lambda _: False
    elif change == "session":
        service._sessions["private"] = NS(**vars(session))
    elif change == "account":
        service.accounts = [replace(account)]
    elif change == "reader":
        session.loan_inventory_snapshot = lambda _: pytest.fail("different reader")
    elif change == "missing_reader":
        del session.loan_inventory_snapshot
    elif change == "file":
        path.write_bytes(b"changed")
    elif change == "navigation":
        service._account_context_epoch += 2
    elif change == "cancel":
        opts["still_current"] = lambda: False
    else:
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DELETE FROM provider_accounts")
    result = remote.compare_remote_loan_catalogue(service, choice, confirmed=True, **opts)
    assert result["status"] != "partial" and not result["candidates"]
    assert not result["provider_read_requested"] and not calls


@pytest.mark.parametrize("change", ["session", "file", "wrong_scope", "private_error", "duplicate"])
def test_discard_remote_response_after_drift_or_ambiguity(case, change, monkeypatch):
    service, request, account, session, path, calls, choice, observed = case
    original = remote.read_loan_inventory
    def read(*args, **kwargs):
        value = original(*args, **kwargs)
        if change == "session":
            service._sessions["private"] = NS(**vars(session))
        elif change == "file":
            path.write_bytes(b"changed")
        elif change == "wrong_scope":
            return LoanInventorySnapshot(
                replace(value.scope, account_id="foreign"), (), status="success")
        elif change == "private_error":
            raise RuntimeError("PRIVATE upstream body")
        else:
            return replace(value, loans=(observed, replace(observed, record_id="other-edition")))
        return value
    monkeypatch.setattr(remote, "read_loan_inventory", read)
    before = rows(service.storage)
    result = remote.compare_remote_loan_catalogue(service, choice, confirmed=True)
    assert not result["candidates"] and result["provider_read_requested"]
    assert "PRIVATE" not in repr(result) and calls == ["typed_inventory"]
    assert rows(service.storage) == before


def test_missing_edition_is_not_inferred_and_typed_only_has_no_legacy_fallback(case):
    service, request, account, session, path, calls, choice, observed = case
    scope = LoanInventoryScope.for_account(account)
    report = LoanInventorySnapshot(scope, (replace(observed, record_id=""),))
    matches, truncated = remote._matches(report, choice, "Roman commun", ("Autrice",))
    assert len(matches) == 1 and matches[0].edition_id is None and not truncated
    del session.loan_inventory_snapshot
    assert read_loan_inventory(session, account, typed_only=True).status == "failed"
    assert calls == []
