"""#6168: visible Library/Reader GTK rows register one exact N2 source."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca import epub_metadata
from cifereca.core.local_cover_evidence import project_verified_local_cover_evidence
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.local_cover_adapter import LocalCoverEvidenceAdapter
from tests.test_epub_cover_contract import _write_epub


GUI = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def method(name):
    tree = ast.parse((GUI / "book_widgets.py").read_text(encoding="utf-8"))
    cell = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "BookListCell")
    func = next(x for x in cell.body if isinstance(x, ast.FunctionDef) and x.name == name)
    ns = {"__name__": "cifereca.gui.book_widgets", "__package__": "cifereca.gui"}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        func,
    ], type_ignores=[])), str(GUI / "book_widgets.py"), "exec"), ns)
    return ns[name]


@pytest.fixture
def ready(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "known.epub"
    data = _write_epub(book, epub3=True)
    sha = hashlib.sha256(data).hexdigest()
    facts = project_verified_local_cover_evidence(
        identity_id="alice", book_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="placement:alice",
        artifact_sha256=sha, artifact_metadata=epub_metadata.inspect_epub(book),
        verified_path=book,
    )
    assert facts is not None and facts.embedded is not None
    loader = CoverLoader(max_workers=2)
    service = DocumentCoverService(loader)
    adapter = LocalCoverEvidenceAdapter()
    try:
        yield facts, service, adapter, loader
    finally:
        adapter.close()
        service.close()
        loader.close()


def test_live_row_registers_and_uses_exact_document_not_hot_origin_url(ready):
    facts, service, adapter, loader = ready
    calls = []
    cover = NS(
        bind_document=lambda *args, **kwargs: calls.append(("N2", args, kwargs)),
        bind_url=lambda *args, **kwargs: calls.append(("legacy", args, kwargs)),
    )
    item = NS(
        key="book:list", _cover_document_ref=facts.scope,
        _cover_local_evidence=facts, thumbnail_url="https://hot.example/img.jpg",
        default_thumbnail_url=None, _cover_paint_observer=None,
    )
    window = NS(document_covers=service, local_cover_adapter=adapter)
    cell = NS(
        window=window, cover=cover, cover_loader=loader,
        _thumbnail_item=item, _bound_key=item.key,
    )
    admit = method("_admit_core_local_cover")
    cell._admit_core_local_cover = lambda row, gateway, document: admit(
        cell, row, gateway, document,
    )
    changed = method("_thumbnail_changed")
    changed(cell, item)
    assert len(calls) == 1 and calls[0][0] == "N2"
    assert calls[0][1] == (service, facts.scope)
    selected = service.decisions.peek(facts.scope, revision=1)
    # Registration is read-only; source is chosen on the first N2 read.
    assert selected is None
    result = []
    service.get_cover(facts.scope, "detail", result.append)
    assert len(result) == 1
    assert service.decisions.peek(facts.scope, revision=1).tier == "embedded"
    assert loader.stats()["download_ok"] == 0


def test_invalid_core_packet_is_not_silently_replaced_by_cached_url(ready):
    facts, service, adapter, loader = ready
    calls = []
    cover = NS(
        bind_document=lambda *args, **kwargs: calls.append(("N2", args)),
        bind_url=lambda *args, **kwargs: calls.append(("fallback", args)),
    )
    item = NS(
        key="book:list", _cover_document_ref=facts.scope,
        _cover_local_evidence=object(),
        thumbnail_url="https://hot.example/img.jpg",
        default_thumbnail_url=None, _cover_paint_observer=None,
    )
    cell = NS(
        window=NS(document_covers=service, local_cover_adapter=adapter),
        cover=cover, cover_loader=loader,
        _thumbnail_item=item, _bound_key=item.key,
    )
    admit = method("_admit_core_local_cover")
    cell._admit_core_local_cover = lambda row, gateway, doc: admit(
        cell, row, gateway, doc,
    )
    method("_thumbnail_changed")(cell, item)
    assert len(calls) == 1 and calls[0][0] == "fallback"
    assert calls[0][1] == (loader, None)
    assert service.decisions.peek(facts.scope, revision=1) is None


def test_detail_prefers_n2_before_any_legacy_url_get():
    detail = (GUI / "detail_panel.py").read_text(encoding="utf-8")
    assert "cover.bind_document(" in detail
    assert "cover.bind_url(self.window.covers, thumbnail, priority=0)" in detail
    assert detail.index("cover.bind_document(") < detail.index(
        "cover.bind_url(self.window.covers, thumbnail, priority=0)"
    )
    lib = (GUI / "library_detail_controller.py").read_text(encoding="utf-8")
    assert 'item.get("_exact_local_cover_evidence")' in lib
    assert "publisher.register(bridge, evidence)" in lib
    assert "cover_document=document" in lib
    assert "admitted_thumbnail = None" in lib
    reader = (GUI / "reader_page_controller.py").read_text(encoding="utf-8")
    assert 'source.get("_exact_local_cover_evidence")' in reader
    assert "publisher.register(bridge, facts)" in reader
    assert "cover_document=cover_document" in reader


def test_one_process_wide_local_adapter_and_deterministic_teardown():
    window = (GUI / "main_window.py").read_text(encoding="utf-8")
    close = (GUI / "window_close.py").read_text(encoding="utf-8")
    assert "self.document_covers = DocumentCoverService(self.covers)" in window
    assert "self.local_cover_adapter = LocalCoverEvidenceAdapter()" in window
    assert close.index("adapter.close()") < close.index("service.close()")
    assert close.index("service.close()") < close.index("window.covers.close()")
