"""P0 #5833: queued loan inventory and cover metadata keep exact Core owner."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.models import Loan
from cifereca.task_queue import TaskPriority


def _function():
    source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/loans_controller.py"
    node = next(
        item for item in ast.parse(source.read_text(encoding="utf-8")).body
        if isinstance(item, ast.FunctionDef) and item.name == "refresh_loans"
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ), node], type_ignores=[],
    ))
    scope = {
        "TaskPriority": TaskPriority, "Loan": Loan,
        "materialized_loan_cover_rows": lambda rows: tuple(rows),
    }
    exec(compile(module, str(source), "exec"), scope)
    return scope["refresh_loans"]


def _setup():
    loan = Loan(
        id="loan-a", title="Livre A", medium="physical", provider="branch",
        record_id="record-a", identity_id="alice", source_provider="paris",
        source_credential="credential-a", source_account_id="account-a",
        state="active",
    )
    inventory = Mock(return_value=[loan])
    local = Mock(return_value=[{"identity_id": "alice", "proof": "local"}])
    service = NS(
        current_identity=NS(identity_id="alice"), _account_context_epoch=5,
        refresh_identity_loans=inventory, library_entries=local,
    )
    queued, shown = [], []
    host = NS(
        service=service, _closed=False, _loan_cover_library_revision=0,
        runner=NS(submit=lambda *args, **kwargs: queued.append((args, kwargs))),
        _loans_refreshed=lambda rows: shown.append(rows),
    )
    return host, service, loan, inventory, local, queued, shown


def test_loan_inventory_and_cover_read_are_from_one_pinned_service():
    host, service, loan, inventory, local, queued, shown = _setup()
    _function()(host, force=True)
    assert len(queued) == 1
    (label, work, done), options = queued[0]
    assert label == "Actualisation des emprunts…"
    assert options["kind"] == "loans"
    assert options["origin"] == "automatic"
    assert options["priority"] == TaskPriority.ACTIVE_LOAN
    rows, covers = work()
    assert rows == [loan]
    assert covers == ({"identity_id": "alice", "proof": "local"},)
    inventory.assert_called_once_with(force=True, identity_id="alice", expected_source_epoch=0, publication_epoch=5)
    local.assert_called_once_with(identity_id="alice", scan=False)
    done((rows, covers))
    assert shown == [[loan]]
    assert host._loan_cover_projection == (5, "alice", covers)


def test_loans_job_is_not_retargeted_to_another_core_with_same_identity():
    host, original, loan, inventory, local, queued, shown = _setup()
    _function()(host, force=False, control=object())
    (_, work, done), options = queued[0]
    assert options["priority"] == TaskPriority.USER_ACTION
    assert options["origin"] == "user"
    replacement = NS(
        current_identity=NS(identity_id="alice"), _account_context_epoch=5,
        refresh_identity_loans=Mock(side_effect=AssertionError("wrong service")),
        library_entries=Mock(side_effect=AssertionError("foreign cover read")),
    )
    host.service = replacement
    payload = work()
    inventory.assert_called_once_with(force=False, identity_id="alice", expected_source_epoch=0, publication_epoch=5)
    local.assert_called_once_with(identity_id="alice", scan=False)
    replacement.refresh_identity_loans.assert_not_called()
    replacement.library_entries.assert_not_called()
    done(payload)
    assert shown == []  # No borrower A result painted in replacement Core.


def test_aba_navigation_during_queued_loan_inventory_does_not_rebind_source():
    host, service, loan, inventory, local, queued, shown = _setup()
    _function()(host, force=True)
    _, work, done = queued[0][0]
    service.current_identity = NS(identity_id="bob")
    service._account_context_epoch += 1
    service.current_identity = NS(identity_id="alice")
    service._account_context_epoch += 1
    payload = work()
    inventory.assert_called_once_with(force=True, identity_id="alice", expected_source_epoch=0, publication_epoch=5)
    local.assert_called_once_with(identity_id="alice", scan=False)
    done(payload)
    assert shown == []
    assert not hasattr(host, "_loan_cover_projection")


def test_shutdown_filters_gtk_delivery_without_restarting_owner_service():
    host, service, loan, inventory, local, queued, shown = _setup()
    _function()(host)
    _, work, done = queued[0][0]
    host._closed = True
    payload = work()
    inventory.assert_called_once_with(force=True, identity_id="alice", expected_source_epoch=0, publication_epoch=5)
    done(payload)
    assert shown == []
