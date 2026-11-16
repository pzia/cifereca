"""P0 #6366: Library/Recent detail N3 must retain one owner/Placement.

No GTK dependency: the same presentation/binding guard is exercised using
Core's attested public-C link shape, a virtual owner and a callback sink.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.core.notice_c_local_origin_projection import VerifiedLocalPublicCLink
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.declared_document_cover import bind_declared_document_cover
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.local_public_c_detail_guard import (
    local_public_c_detail_check, staged_local_public_c_detail,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def _view(view):
    link = VerifiedLocalPublicCLink(
        identity_id="alice",
        artifact_id="sha256:" + "a" * 64,
        placement_id="placement-1",
        document=canonical_cover_document_ref("edition:original-book"),
        epoch=3,
        source_refs=("notice-c:v1:" + "b" * 64,),
    )
    item = dict(
        identity_id="alice",
        artifact_id=link.artifact_id,
        placement_id=link.placement_id,
        _public_canonical_cover_link=link,
        _exact_local_cover_evidence=None,
    )
    service = NS(
        current_identity=NS(identity_id="alice"),
        _account_context_epoch=7, _source_context_epoch=9,
    )
    key = "library:one" if view == "library" else "placement-1"
    row = NS(
        key=key, _cover_public_c_link=link,
        _cover_document_ref=link.document,
        _public_row_content=(view, key, link.document, link.epoch),
    )
    host = NS(
        service=service, _library_objects={},
        _recent_objects={}, _library_ambiguous_keys=set(),
    )
    target = (
        host._library_objects if view == "library"
        else host._recent_objects
    )
    target[key] = item
    return host, row, item, link, service


@pytest.mark.parametrize("view", ["library", "recent"])
def test_detail_c_guard_rejects_aba_source_and_replaced_mapping(view):
    host, row, item, link, service = _view(view)
    check = local_public_c_detail_check(host, row, item, link, view=view)
    assert check()
    # A later callback cannot use A's original proof under another profile.
    service.current_identity = NS(identity_id="bob")
    assert not check()
    service.current_identity = NS(identity_id="alice")
    assert check()
    service._account_context_epoch += 1
    assert not check()
    service._account_context_epoch -= 1
    assert check()
    service._source_context_epoch += 1
    assert not check()
    service._source_context_epoch -= 1
    assert check()
    host.service = NS(**vars(service))
    assert not check()
    host.service = service
    assert check()
    # A same-key newer row must never reuse the old detail's C or actions.
    target = (
        host._library_objects if view == "library"
        else host._recent_objects
    )
    target[row.key] = dict(item)
    assert not check()
    target[row.key] = item
    assert check()
    item["_public_canonical_cover_link"] = replace(link)
    assert not check()
    item["_public_canonical_cover_link"] = link
    assert check()
    item["_exact_local_cover_evidence"] = object()
    assert not check()
    item["_exact_local_cover_evidence"] = None
    assert check()
    item["placement_id"] = "other-placement"
    assert not check()


@pytest.mark.parametrize("view", ["library", "recent"])
def test_detail_c_guard_denies_ambiguous_and_foreign_owner(view):
    host, row, item, link, _service = _view(view)
    check = local_public_c_detail_check(host, row, item, link, view=view)
    if view == "library":
        host._library_ambiguous_keys.add(row.key)
        assert not check()
        host._library_ambiguous_keys.clear()
        row._library_ambiguous_key = True
        assert not check()
        row._library_ambiguous_key = False
    else:
        row._recent_ambiguous_key = True
        assert not check()
        row._recent_ambiguous_key = False
    assert check()
    item["_library_view_owner"] = "bob"
    assert not check()
    item["_library_view_owner"] = None
    assert check()
    item["identity_id"] = "bob"
    assert not check()


def test_detail_stage_always_clears_callback_even_on_render_exception():
    detail = NS(_next_local_public_c_check=None)
    called = []
    check = lambda: True
    with staged_local_public_c_detail(detail, check):
        assert detail._next_local_public_c_check is check
        called.append("show")
        with pytest.raises(ValueError, match="invalid_local_public_c_detail_stage"):
            with staged_local_public_c_detail(detail, check):
                raise AssertionError("must not nest")
    assert called == ["show"]
    assert detail._next_local_public_c_check is None
    with pytest.raises(RuntimeError, match="render-failed"):
        with staged_local_public_c_detail(detail, check):
            raise RuntimeError("render-failed")
    assert detail._next_local_public_c_check is None


def test_declared_document_uses_live_check_before_and_after_n3(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    documents = DocumentCoverService(loader)
    try:
        host, row, item, link, service = _view("library")
        check = local_public_c_detail_check(
            host, row, item, link, view="library",
        )
        bound = []
        cover = NS(
            bind_url=lambda *a, **k: bound.append(("url", a, k)),
            bind_document=lambda *a, **k: bound.append(("document", a, k)),
        )
        assert bind_declared_document_cover(
            cover, loader, documents, link.document,
            variant="detail", priority=0, is_current=check,
        )
        assert bound and bound[-1][0] == "document"
        delayed = bound[-1][2]["is_current"]
        assert delayed() is True
        service._source_context_epoch += 1
        assert delayed() is False
        cover_rejected = NS(
            bind_url=lambda *a, **k: bound.append(("url", a, k)),
            bind_document=lambda *_a, **_k: (
                _ for _ in ()
            ).throw(AssertionError("stale detail requested N3")),
        )
        assert not bind_declared_document_cover(
            cover_rejected, loader, documents, link.document,
            variant="detail", priority=0, is_current=check,
        )
        assert bound[-1] == ("url", (loader, None), {"priority": 0})
    finally:
        documents.close()
        loader.close()


def test_both_local_detail_controllers_stage_guard_and_header_forwards_it():
    snippets = {}
    for file, method in [
        ("library_detail_controller.py", "library_selected"),
        ("recent_detail_controller.py", "recent_selected"),
        ("detail_panel.py", "_cover_and_title"),
    ]:
        tree = ast.parse((ROOT / file).read_text(encoding="utf-8"))
        if file == "detail_panel.py":
            cls = next(x for x in tree.body
                       if isinstance(x, ast.ClassDef) and x.name == "DetailPanel")
            fn = next(x for x in cls.body
                      if isinstance(x, ast.FunctionDef) and x.name == method)
        else:
            fn = next(x for x in tree.body
                      if isinstance(x, ast.FunctionDef) and x.name == method)
        snippets[file] = ast.unparse(fn)
    for file in ("library_detail_controller.py", "recent_detail_controller.py"):
        assert "local_public_c_detail_check" in snippets[file]
        assert "staged_local_public_c_detail" in snippets[file]
        assert "detail.show_presentation(" in snippets[file]
    header = snippets["detail_panel.py"]
    assert "_next_local_public_c_check" in header
    assert "bind_declared_document_cover" in header
