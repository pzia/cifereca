"""#6168 History: original provider occurrence -> exact N2 -> shared N3.

Never infer a History picture from title, ISBN, prior Loan, generic default
cover, another account or shared physical bytes. Revisions survive ABA.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.models import HistoryItem
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.history_document_cover_bridge import (
    VerifiedHistoryCoverEvidence, exact_history_document_evidence,
    register_exact_history_document,
)
from cifereca.gui.history_cover_decision_bridge import exact_history_cover_scope


URI = "https://history.example.org/occurrence-image.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40
ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


@pytest.fixture
def selected(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    item = HistoryItem(
        id="occurrence-17", provider="paris", medium="digital",
        record_id="catalogue-17", title="A title", thumbnail_url=URI,
        source_provider="paris", source_credential="credential-alice",
        source_account_id="", identity_id="alice",
    )
    service = NS(
        current_identity=NS(identity_id="alice"),
        _account_context_epoch=4,
        _source_context_epoch=9,
    )
    window = NS(
        service=service, history_objects={item.id: item},
        _history_cover_revision=7,
    )
    loader = CoverLoader(max_workers=2)
    gateway = DocumentCoverService(loader)
    loader._path(URI).write_bytes(PNG)
    try:
        yield window, item, gateway, loader
    finally:
        gateway.close()
        loader.close()


def test_live_provider_occurrence_admits_shared_documentref_and_n3(selected):
    window, item, gateway, loader = selected
    proof = exact_history_document_evidence(window, item)
    assert type(proof) is VerifiedHistoryCoverEvidence
    assert proof.document == exact_history_cover_scope(item, identity_id="alice")
    assert proof.snapshot.candidates[0].url == URI
    assert proof.snapshot.candidates[0].tier == "catalog"
    assert register_exact_history_document(
        window, item, gateway, proof, proof.document,
    )
    results = []
    read = gateway.get_cover(proof.document, "detail", results.append)
    assert read.source_uri == URI and read.still_current
    assert results == [loader._path(URI)]


def test_same_bytes_may_be_used_by_other_profile_but_never_prove_book_link(selected):
    window, alice, gateway, loader = selected
    old = exact_history_document_evidence(window, alice)
    assert register_exact_history_document(window, alice, gateway, old, old.document)
    bob = replace(
        alice, identity_id="bob", source_credential="credential-bob",
    )
    window.service.current_identity.identity_id = "bob"
    window.service._account_context_epoch += 1
    window.service._source_context_epoch += 1
    window._history_cover_revision += 1
    window.history_objects = {bob.id: bob}
    assert not register_exact_history_document(
        window, alice, gateway, old, old.document,
    )
    own = exact_history_document_evidence(window, bob)
    assert own is not None
    assert own.document != old.document
    assert register_exact_history_document(window, bob, gateway, own, own.document)
    assert gateway.get_cover(own.document, "detail", lambda _: None).source_uri == URI
    assert loader._path(URI).is_file()  # One process-wide physical cache.


def test_aba_source_epoch_or_history_refresh_revokes_old_occurrence(selected):
    window, item, gateway, loader = selected
    one = exact_history_document_evidence(window, item)
    assert register_exact_history_document(window, item, gateway, one, one.document)
    old = gateway.get_cover(one.document, "detail", lambda _: None)
    assert old.still_current
    window.service._account_context_epoch += 1
    window.service._source_context_epoch += 1
    window._history_cover_revision += 1
    assert not register_exact_history_document(window, item, gateway, one, one.document)
    two = exact_history_document_evidence(window, item)
    assert two is not None and two.snapshot.revision > one.snapshot.revision
    assert register_exact_history_document(window, item, gateway, two, two.document)
    assert not old.still_current
    assert gateway.get_cover(two.document, "detail", lambda _: None).still_current


@pytest.mark.parametrize("mutate", [
    {"identity_id": "bob"},
    {"source_credential": "changed-credential"},
    {"source_account_id": "another-account"},
    {"record_id": "another-record"},
    {"id": "another-occurrence"},
    {"thumbnail_url": "https://history.example.org/new.png"},
])
def test_rebound_source_object_cannot_reuse_previous_proof(selected, mutate):
    window, item, gateway, loader = selected
    previous = exact_history_document_evidence(window, item)
    assert previous is not None
    changed = replace(item, **mutate)
    window.history_objects = {changed.id: changed}
    assert not register_exact_history_document(
        window, changed, gateway, previous, previous.document,
    )


def test_retained_default_or_title_lookup_is_never_documentary_source(selected):
    window, item, gateway, loader = selected
    no_provider = replace(
        item, thumbnail_url=None, default_thumbnail_url=URI,
    )
    window.history_objects = {no_provider.id: no_provider}
    assert exact_history_document_evidence(window, no_provider) is None
    fake_foreign = replace(no_provider, identity_id="bob", thumbnail_url=URI)
    window.history_objects = {fake_foreign.id: fake_foreign}
    assert exact_history_document_evidence(window, fake_foreign) is None


def _source_method(cls, name):
    path = ROOT / ("detail_panel.py" if cls == "DetailPanel" else "book_widgets.py")
    tree = ast.parse(path.read_text())
    owner = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == cls)
    fn = next(x for x in owner.body if isinstance(x, ast.FunctionDef) and x.name == name)
    context = {
        "__name__": "cifereca.gui." + path.stem,
        "__package__": "cifereca.gui",
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ), fn,
    ], type_ignores=[])), str(path), "exec"), context)
    return context[name]


def test_actual_list_cell_publishes_history_proof_only_on_visible_bind(selected):
    window, item, gateway, loader = selected
    proof = exact_history_document_evidence(window, item)
    assert proof is not None
    cell = NS(window=window)
    row = NS(
        _history_source_item=item, _cover_history_evidence=proof,
        _cover_local_evidence=None,
    )
    admit = _source_method("BookListCell", "_admit_core_local_cover")
    assert admit(cell, row, gateway, proof.document)
    assert gateway.decisions.peek(proof.document, revision=proof.snapshot.revision) is None
    assert not admit(cell, row, gateway, replace(
        proof.document, source_key="history-exact:other",
    ))
    row._cover_local_evidence = object()
    assert not admit(cell, row, gateway, proof.document)


def test_actual_history_detail_wrapper_consumes_evidence_exactly_once(selected):
    window, item, gateway, loader = selected
    proof = exact_history_document_evidence(window, item)
    events = []
    panel = NS(_next_cover_evidence=None)
    def render(history):
        events.append((history, panel._next_cover_evidence))
    panel.show_history = render
    wrapper = _source_method("DetailPanel", "show_history_document")
    wrapper(panel, item, cover_evidence=proof)
    assert events == [(item, proof)]
    assert panel._next_cover_evidence is None


def test_production_views_keep_legacy_fallback_without_minting_history_scope():
    main = (ROOT / "main_window.py").read_text()
    detail = (ROOT / "detail_panel.py").read_text()
    cell = (ROOT / "book_widgets.py").read_text()
    assert "row._cover_document_ref = history_evidence.document" in main
    assert "row._cover_history_evidence = history_evidence" in main
    assert "history_evidence = exact_history_document_evidence(self, x)" in main
    assert "renderer(presented, cover_evidence=proof)" in main
    assert "self.history_page.detail.show_history(presented)" in main
    assert "def show_history(self, item: HistoryItem)" in detail
    assert "def show_history_document(" in detail
    assert "cover.bind_document(" in detail
    assert "register_exact_history_document(" in cell
    assert "cover.bind_url(self.window.covers, None, priority=0)" in detail
