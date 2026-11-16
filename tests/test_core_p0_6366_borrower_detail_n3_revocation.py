"""P0 #6366: delayed N3 detail paint may not reuse a borrower C receipt.

Exact public C is a documentary cover, not a transferable Loan/History right.
All time-scoped UI checks execute without a GTK import or provider/SQLite I/O.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.borrower_public_c_bridge import (
    bind_borrower_public_c_detail,
)
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.core.loan_source_identity import loan_source_key
from cifereca.models import HistoryItem, Loan
from cifereca.storage import Storage
from tests.test_core_p0_6366_borrower_public_c_lists_detail import (
    admit, bibliography, history, host_for, loan,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
INTRUDER = "https://covers.example.org/not-an-attested-book.png"


class CoverSink:
    def __init__(self):
        self.calls = []

    def bind_url(self, *args, **kwargs):
        self.calls.append(("url", args, kwargs))

    def bind_document(self, *args, **kwargs):
        self.calls.append(("document", args, kwargs))


@pytest.mark.parametrize("make", [loan, history])
def test_borrower_detail_n3_callback_checks_live_source_and_producer_generation(
    tmp_path, make,
):
    db = Storage(tmp_path / ("delayed-" + make.__name__ + ".sqlite"))
    loader = CoverLoader(max_workers=2)
    documents = DocumentCoverService(loader)
    try:
        _, expected = bibliography(db)
        original = make()
        assert admit(db, [original]) == 1
        host = host_for(original)
        host.document_covers = documents
        host.covers = NS()
        host._loan_cover_decision_revision = 7
        sink = CoverSink()
        receipt = original.__dict__["_core_public_c_cover_receipt"]
        assert bind_borrower_public_c_detail(
            host, sink, expected.document, receipt,
        )
        name, (service, document), kwargs = sink.calls.pop()
        assert name == "document"
        assert service is documents and document == expected.document
        assert kwargs["variant"] == "detail" and kwargs["priority"] == 0
        assert INTRUDER not in repr(sink.calls)
        check = kwargs["is_current"]
        assert callable(check) and check() is True
        # A→B→A does not return to the same owner generation.
        host.service._account_context_epoch += 1
        assert check() is False
        host.service._account_context_epoch += 1
        assert check() is False
        host.service._account_context_epoch -= 2
        assert check() is True
        # The library and history producer revision changes even when the
        # underlying private source object is reused.
        revision_key = (
            "_loan_cover_decision_revision"
            if type(original) is Loan else "_history_cover_revision"
        )
        setattr(host, revision_key, getattr(host, revision_key) + 1)
        assert check() is False
        setattr(host, revision_key, getattr(host, revision_key) - 1)
        assert check() is True
        # An alternate service bound to the same identity is never the
        # original service, even if every scalar scope is replayed.
        old_service = host.service
        host.service = NS(**vars(old_service))
        assert check() is False
        host.service = old_service
        assert check() is True
        # Replacing the DTO in the live UI action map invalidates N3.
        if type(original) is Loan:
            host.loan_objects[loan_source_key(original)] = replace(original)
        else:
            host.history_objects[original.id] = replace(original)
        assert check() is False
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        documents.close()
        loader.close()
        db.close()


@pytest.mark.parametrize("make", [loan, history])
def test_rejected_borrower_n3_has_no_url_fallback_or_document_request(
    tmp_path, make,
):
    db = Storage(tmp_path / "denied.sqlite")
    loader = CoverLoader(max_workers=2)
    documents = DocumentCoverService(loader)
    try:
        _, c = bibliography(db)
        original = make()
        assert admit(db, [original]) == 1
        host = host_for(original)
        host.covers = NS()
        host.document_covers = documents
        receipt = original.__dict__["_core_public_c_cover_receipt"]
        host.service._source_context_epoch += 1
        sink = CoverSink()
        assert not bind_borrower_public_c_detail(
            host, sink, c.document, receipt,
        )
        assert sink.calls == [
            ("url", (host.covers, None), {"priority": 0}),
        ]
        sink.calls.clear()
        assert not bind_borrower_public_c_detail(
            host, sink, object(), receipt,
        )
        assert sink.calls[0][0] == "url"
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        documents.close()
        loader.close()
        db.close()


def _panel_method(name):
    """Exercise the actual production DetailPanel method without importing GTK."""
    file = ROOT / "detail_panel.py"
    tree = ast.parse(file.read_text(encoding="utf-8"))
    cls = next(
        x for x in tree.body
        if isinstance(x, ast.ClassDef) and x.name == "DetailPanel"
    )
    method = next(
        x for x in cls.body
        if isinstance(x, ast.FunctionDef) and x.name == name
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[method], type_ignores=[],
    ))
    ns = {
        "__name__": "cifereca.gui.detail_panel",
        "__package__": "cifereca.gui",
        "Loan": Loan, "HistoryItem": HistoryItem,
    }
    exec(compile(module, str(file), "exec"), ns)
    return ns[name]


@pytest.mark.parametrize("make,method_name,render_name", [
    (loan, "show_loan_public_c", "show_loan"),
    (history, "show_history_public_c", "show_history"),
])
def test_detail_stages_receipt_only_during_exact_render(
    tmp_path, make, method_name, render_name,
):
    db = Storage(tmp_path / "detail-stage.sqlite")
    try:
        _, c = bibliography(db)
        original = make()
        assert admit(db, [original]) == 1
        window = host_for(original)
        captures = []
        panel = NS(
            window=window,
            _next_cover_evidence=None,
            _next_cover_document_ref=None,
            _next_borrower_public_c_receipt=None,
        )
        receipt = original.__dict__["_core_public_c_cover_receipt"]
        def render(item):
            assert item is original
            captures.append((
                panel._next_cover_document_ref,
                panel._next_borrower_public_c_receipt,
            ))
        setattr(panel, render_name, render)
        show = _panel_method(method_name)
        show(panel, original, document=c.document)
        assert captures == [(c.document, receipt)]
        assert panel._next_cover_document_ref is None
        assert panel._next_borrower_public_c_receipt is None

        def interrupted(_item):
            raise RuntimeError("synthetic render interruption")
        setattr(panel, render_name, interrupted)
        with pytest.raises(RuntimeError, match="synthetic"):
            show(panel, original, document=c.document)
        assert panel._next_cover_document_ref is None
        assert panel._next_borrower_public_c_receipt is None

        # A stale/forged candidate may not claim an arbitrary public ref.
        window.service._source_context_epoch += 1
        with pytest.raises(ValueError, match="invalid_"):
            show(panel, original, document=c.document)
        assert panel._next_borrower_public_c_receipt is None
    finally:
        db.close()


def test_detail_header_routes_staged_borrower_proof_through_n3_guard():
    source = (ROOT / "detail_panel.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(x for x in tree.body
               if isinstance(x, ast.ClassDef) and x.name == "DetailPanel")
    fn = next(x for x in cls.body
              if isinstance(x, ast.FunctionDef) and x.name == "_cover_and_title")
    out = ast.unparse(fn)
    assert "_next_borrower_public_c_receipt" in out
    assert "bind_borrower_public_c_detail" in out
    assert "bind_declared_document_cover" in out
    # Only the borrower-specific staged branch may employ the pinned guard.
    assert out.index("bind_borrower_public_c_detail") < out.index(
        "bind_declared_document_cover"
    )
