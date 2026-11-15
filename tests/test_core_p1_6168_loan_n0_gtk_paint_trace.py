"""#6168 N0: qualified Loans list/detail source-to-GTK texture receipts."""
from __future__ import annotations

import ast
from copy import copy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.book_cover_source_projection import exact_loan_cover_scope
from cifereca.core.loan_source_identity import loan_source_key
from cifereca.gui.loan_cover_decision_bridge import loan_cover_decision
from cifereca.gui.loan_cover_paint_trace import (
    loan_cover_paint_receipt,
)
from cifereca.models import Loan
from tests.test_core_p1_4979_loan_gtk_projection import a_loan, a_row


ROOT = Path(__file__).resolve().parents[1]
LOANS = ROOT / "src/cifereca/gui/loans_controller.py"


def host(*, probe_result=True):
    current = a_loan()
    library_rows = (a_row(current),)
    calls = []
    def probe(url):
        calls.append(url)
        return NS(source_cached=probe_result, preview_cached=False)
    window = NS(
        service=NS(
            current_identity=NS(identity_id="alice"),
            _account_context_epoch=42,
        ),
        _loan_cover_projection=(42, "alice", library_rows),
        _loan_cover_decision_revision=7,
        loan_objects={loan_source_key(current): current},
        covers=NS(probe_media=probe),
    )
    decision = loan_cover_decision(window, current, library_rows)
    assert decision is not None and decision.status == "selected"
    assert decision.scope == exact_loan_cover_scope(current)
    return window, current, decision, calls


def observed_state(window, scope):
    service = window.service
    return window._loan_cover_trace_observer.snapshot(
        scope, context=(
            service.current_identity.identity_id,
            service._account_context_epoch,
            getattr(service, "_source_context_epoch", None),
        ),
    )


def test_real_n2_event_then_real_texture_marks_list_and_detail_separately():
    window, current, decision, calls = host()
    state = observed_state(window, decision.scope)
    assert state.resolved and state.status == "selected"
    assert not state.media_cached and not state.list_painted
    assert not state.detail_painted
    list_receipt = loan_cover_paint_receipt(
        window, current, surface="list", expected_url=decision.uri,
    )
    detail_receipt = loan_cover_paint_receipt(
        window, current, surface="detail", expected_url=decision.uri,
    )
    assert callable(list_receipt) and callable(detail_receipt)
    assert calls == []  # Projection and model binding do not probe disk.
    list_receipt()
    assert calls == [decision.uri]
    state = observed_state(window, decision.scope)
    assert state.media_cached and state.list_painted
    assert not state.detail_painted
    detail_receipt()
    state = observed_state(window, decision.scope)
    assert state.detail_painted and state.list_painted
    assert calls == [decision.uri, decision.uri]
    assert not hasattr(current, "_on_cover_applied")


def test_invalid_physical_signature_never_counts_as_list_or_detail_paint():
    window, current, decision, calls = host(probe_result=False)
    callback = loan_cover_paint_receipt(
        window, current, surface="detail", expected_url=decision.uri,
    )
    assert callback is not None
    callback()
    state = observed_state(window, decision.scope)
    assert state.resolved and not state.media_cached
    assert not state.detail_painted and not state.list_painted
    assert calls == [decision.uri]


def test_recycled_or_replaced_loan_dto_cannot_acknowledge_other_row():
    window, current, decision, calls = host()
    callback = loan_cover_paint_receipt(
        window, current, surface="list", expected_url=decision.uri,
    )
    window.loan_objects[loan_source_key(current)] = copy(current)
    callback()
    assert calls == []
    assert not observed_state(window, decision.scope).list_painted


@pytest.mark.parametrize("dimension", ["identity", "epoch", "revision", "closed"])
def test_aba_context_change_prevents_late_gtk_paint(dimension):
    window, current, decision, calls = host()
    callback = loan_cover_paint_receipt(
        window, current, surface="detail", expected_url=decision.uri,
    )
    old_status = observed_state(window, decision.scope)
    assert old_status is not None and not old_status.detail_painted
    if dimension == "identity":
        window.service.current_identity.identity_id = "bob"
        window.service.current_identity.identity_id = "alice"
        window.service._account_context_epoch += 1
    elif dimension == "epoch":
        window.service._account_context_epoch += 1
    elif dimension == "revision":
        window._loan_cover_decision_revision += 1
    else:
        window._closed = True
    callback()
    assert calls == []
    assert not old_status.detail_painted


def test_stronger_new_loan_proof_resets_previous_n0_receipts():
    window, current, first, _calls = host()
    callback = loan_cover_paint_receipt(
        window, current, surface="list", expected_url=first.uri,
    )
    callback()
    assert observed_state(window, first.scope).list_painted
    window._loan_cover_decision_revision += 1
    rows = window._loan_cover_projection[2]
    newer = loan_cover_decision(window, current, rows)
    assert newer.revision == first.revision + 1
    snapshot = observed_state(window, first.scope)
    assert snapshot.status == "selected"
    assert snapshot.resolved and not snapshot.list_painted
    callback()
    assert not snapshot.list_painted
    fresh = loan_cover_paint_receipt(
        window, current, surface="list", expected_url=newer.uri,
    )
    assert fresh is not None
    fresh()
    assert observed_state(window, first.scope).list_painted


def test_n2_explicit_invalidation_rejects_even_valid_stale_media_receipt():
    window, current, decision, calls = host()
    callback = loan_cover_paint_receipt(
        window, current, surface="detail", expected_url=decision.uri,
    )
    window._loan_cover_decision_cache.invalidate(
        decision.scope, cause="loan_changed",
    )
    callback()
    assert calls == []
    state = observed_state(window, decision.scope)
    assert state.invalidated and not state.detail_painted


def test_wrong_url_unknown_source_and_nonselected_decision_are_not_observed():
    window, current, decision, calls = host()
    for surface, url in (
        ("list", None), ("detail", "https://covers.example.org/other.png"),
        ("unknown", decision.uri),
    ):
        assert loan_cover_paint_receipt(
            window, current, surface=surface, expected_url=url,
        ) is None
    assert loan_cover_paint_receipt(
        window, a_loan(account=None), surface="detail",
        expected_url=decision.uri,
    ) is None
    assert loan_cover_paint_receipt(
        window, a_loan(account="another"), surface="list",
        expected_url=decision.uri,
    ) is None
    assert calls == []


def test_no_coverloader_keeps_existing_loan_projection_without_new_observers():
    current = a_loan()
    rows = (a_row(current),)
    window = NS(
        service=NS(
            current_identity=NS(identity_id="alice"),
            _account_context_epoch=7,
        ),
        _loan_cover_projection=(7, "alice", rows),
        _loan_cover_decision_revision=1,
        loan_objects={loan_source_key(current): current},
    )
    assert loan_cover_decision(window, current, rows).status == "selected"
    assert not hasattr(window, "_loan_cover_lifecycle")
    assert loan_cover_paint_receipt(
        window, current, surface="list",
        expected_url=rows[0]["thumbnail_url"],
    ) is None


def test_real_loan_detail_does_not_write_observer_to_business_loan():
    tree = ast.parse(LOANS.read_text(encoding="utf-8"))
    method = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_loan_selected")
    namespace = {
        "__name__": "cifereca.gui.loans_controller",
        "__package__": "cifereca.gui",
        "Loan": Loan,
        "copy": copy,
        "_loan_display_cover": lambda _window, l: l,
        "loan_cover_paint_receipt": loan_cover_paint_receipt,
    }
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")], level=0,
            ),
            method,
        ], type_ignores=[]),
    ), str(LOANS), "exec"), namespace)
    window, current, decision, _calls = host()
    def present(_window, loan):
        displayed = copy(loan)
        displayed.thumbnail_url = decision.uri
        return displayed
    namespace["_loan_display_cover"] = present
    shown = []
    window.loan_page = NS(detail=NS(show_loan=shown.append))
    window._add_local_book_actions = lambda *_args: None
    window._add_facets = lambda *_args: None
    window.search_controls = object()
    namespace["_loan_selected"](window, NS(key=loan_source_key(current)))
    assert len(shown) == 1
    assert shown[0] is not current
    assert callable(shown[0]._on_cover_applied)
    assert not hasattr(current, "_on_cover_applied")
    shown[0]._on_cover_applied()
    assert observed_state(window, decision.scope).detail_painted


def test_no_extra_network_work_on_lists_or_loan_refreshes():
    source = LOANS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods = {
        n.name: ast.unparse(n) for n in tree.body
        if isinstance(n, ast.FunctionDef)
    }
    assert "item._on_cover_applied = observer" in methods["_render_loans"]
    assert "displayed._on_cover_applied = observer" in methods["_loan_selected"]
    assert "displayed._on_cover_applied = observer" in methods["_loans_refreshed"]
    for name in ("_render_loans", "_loan_selected", "_loans_refreshed"):
        assert "service.library_entries(" not in methods[name]
        assert "service.holdings(" not in methods[name]
