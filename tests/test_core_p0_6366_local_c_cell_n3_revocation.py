"""P0 #6366: Library/Recent N2 cover cannot paint from stale N3 callback.

The proof was constructed by Core and projected onto a GTK-free row. Only the
same current row/owner/epochs may turn that public C into list pixels.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.core.notice_c_local_origin_projection import VerifiedLocalPublicCLink
from cifereca.gui.local_public_c_cell_guard import local_public_c_cell_current


WIDGETS = Path(__file__).resolve().parents[1] / "src/cifereca/gui/book_widgets.py"


def _stage(view="library"):
    proof = VerifiedLocalPublicCLink(
        identity_id="alice",
        artifact_id="sha256:" + "f" * 64,
        placement_id="placement-1",
        document=canonical_cover_document_ref("edition:proven-origin"),
        epoch=3,
        source_refs=("notice-c:v1:" + "a" * 64,),
    )
    original = {
        "identity_id": "alice",
        "artifact_id": proof.artifact_id,
        "placement_id": proof.placement_id,
        "_exact_local_cover_evidence": None,
        "_public_canonical_cover_link": proof,
    }
    service = NS(
        current_identity=NS(identity_id="alice"),
        _account_context_epoch=7, _source_context_epoch=9,
    )
    key = "library:one" if view == "library" else "placement-1"
    row = NS(
        key=key, _cover_public_c_link=proof,
        _cover_document_ref=proof.document,
        _cover_local_evidence=None,
        _cover_history_evidence=None,
        _public_row_content=(view, key, "Titre", proof.document, proof.epoch),
    )
    host = NS(
        service=service, _library_objects={},
        _recent_objects={}, _library_ambiguous_keys=set(),
        document_covers=NS(),
    )
    objects = host._library_objects if view == "library" else host._recent_objects
    objects[key] = original
    return host, row, proof, original, service, (
        "alice", 7, 9,
    )


@pytest.mark.parametrize("view", ["library", "recent"])
def test_only_current_original_local_public_c_is_admitted(view):
    host, row, proof, original, service, context = _stage(view)
    def current():
        return local_public_c_cell_current(
            host, row, service=service, context=context,
        )

    assert current()
    # A changed source epoch, even with an identical C N2 cached URI,
    # revokes the old row.
    service._source_context_epoch += 1
    assert not current()
    service._source_context_epoch -= 1
    assert current()
    service._account_context_epoch += 1
    assert not current()
    service._account_context_epoch -= 1
    assert current()
    service.current_identity = NS(identity_id="bob")
    assert not current()
    service.current_identity = NS(identity_id="alice")
    assert current()
    host.service = NS(**vars(service))
    assert not current()
    host.service = service
    assert current()

    # Equal-but-replayed snapshot values are not the original Core proof.
    name = "_library_objects" if view == "library" else "_recent_objects"
    mapped = getattr(host, name)
    mapped[row.key] = dict(original)
    assert current()  # Mapping may be copied; the Core proof itself must match.
    replay = replace(proof)
    mapped[row.key]["_public_canonical_cover_link"] = replay
    assert not current()
    mapped[row.key] = original
    assert current()
    original["_exact_local_cover_evidence"] = object()  # N1 EPUB now wins.
    assert not current()
    original["_exact_local_cover_evidence"] = None
    assert current()
    original["placement_id"] = "other-placement"
    assert not current()


@pytest.mark.parametrize("view", ["library", "recent"])
def test_ambiguous_or_foreign_local_public_c_never_paints(view):
    host, row, _proof, original, service, context = _stage(view)
    def current():
        return local_public_c_cell_current(
            host, row, service=service, context=context,
        )
    if view == "library":
        host._library_ambiguous_keys.add(row.key)
        assert not current()
        host._library_ambiguous_keys.clear()
        row._library_ambiguous_key = True
        assert not current()
        row._library_ambiguous_key = False
    else:
        row._recent_ambiguous_key = True
        assert not current()
        row._recent_ambiguous_key = False
    original["_library_view_owner"] = "bob"
    assert not current()
    original["_library_view_owner"] = None
    assert current()
    original["identity_id"] = "bob"
    assert not current()


def _cell_method(name):
    source = WIDGETS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    cell = next(x for x in tree.body
                if isinstance(x, ast.ClassDef) and x.name == "BookListCell")
    method = next(x for x in cell.body
                  if isinstance(x, ast.FunctionDef) and x.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    ns = {
        "__name__": "cifereca.gui.book_widgets",
        "__package__": "cifereca.gui",
        "BookListItem": NS,
    }
    exec(compile(module, str(WIDGETS), "exec"), ns)
    return ns[name]


@pytest.mark.parametrize("view", ["library", "recent"])
def test_real_cell_passes_local_public_c_time_guard_into_n3(view):
    host, row, _proof, original, service, _context = _stage(view)
    calls = []
    fake_cover = NS(
        bind_document=lambda *args, **kw: calls.append(("document", args, kw)),
        bind_url=lambda *args, **kw: calls.append(("url", args, kw)),
    )
    cell = NS(
        window=host, _bind_epoch=5, _bound_key=row.key,
        _thumbnail_item=row, cover=fake_cover,
        cover_loader=NS(),
        _admit_core_local_cover=lambda item, gateway, document: (
            item is row and gateway is host.document_covers
            and document == row._cover_document_ref
        ),
    )
    cell._search_cover_guard = lambda item: _cell_method(
        "_search_cover_guard"
    )(cell, item)
    _cell_method("_thumbnail_changed")(cell, row)
    assert calls and calls[0][0] == "document"
    name, args, kw = calls[0]
    assert args[1] == row._cover_document_ref
    assert kw["variant"] == "list"
    assert kw["minimum_revision"] == row._cover_public_c_link.epoch
    check = kw["is_current"]
    assert callable(check) and check()
    # The UI list cell itself may have been recycled while the N3 load ran.
    cell._bind_epoch += 1
    assert not check()
    cell._bind_epoch -= 1
    assert check()
    cell._thumbnail_item = NS(key=row.key)
    assert not check()
    cell._thumbnail_item = row
    assert check()
    (host._recent_objects if view == "recent" else host._library_objects).pop(row.key, None)
    assert not check()


def test_initial_bind_and_signal_rebind_both_install_local_c_n3_guard():
    tree = ast.parse(WIDGETS.read_text(encoding="utf-8"))
    cell = next(x for x in tree.body
                if isinstance(x, ast.ClassDef) and x.name == "BookListCell")
    for name in ("bind", "_thumbnail_changed"):
        fn = next(x for x in cell.body
                  if isinstance(x, ast.FunctionDef) and x.name == name)
        source = ast.unparse(fn)
        assert "_cover_public_c_link" in source
        assert "self._search_cover_guard(item)" in source
    gate = next(x for x in cell.body
                if isinstance(x, ast.FunctionDef) and x.name == "_search_cover_guard")
    body = ast.unparse(gate)
    assert "local_public_c_cell_current" in body
    assert "self._bind_epoch == bind_epoch" in body
    assert "self._thumbnail_item is item" in body
