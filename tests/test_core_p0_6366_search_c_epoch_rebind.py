"""P0 #6366: a public C with an unchanged DocumentRef can change N2 epoch.

Search's canonical known rows must force GTK rebind when C's persisted
cover contribution changes; neither query, title nor N3 bytes are proof
that the previous N4 texture is still current.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from tests.test_core_p1_3104_master_detail_visual_signature import (
    BookRow, page_with, production_methods,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
SEARCH = ROOT / "search_results.py"


class Row(BookRow):
    def __init__(self, key, title, subtitle, meta, badge, thumbnail):
        # MasterDetailPage uses isinstance(item, BookListItem) to restore
        # selection. Reuse the test harness's BookRow class for both methods.
        super().__init__(
            key, cover=thumbnail, title=title, author=subtitle, badge=badge,
        )
        self.meta = meta
        self.action_callback = None


def _production_search_render():
    source = ast.parse(SEARCH.read_text(encoding="utf-8"))
    fn = next(x for x in source.body
              if isinstance(x, ast.FunctionDef)
              and x.name == "_render_search_results")
    module = ast.fix_missing_locations(
        ast.Module(body=[fn], type_ignores=[]),
    )
    env = {
        "__name__": "cifereca.gui.search_results",
        "__package__": "cifereca.gui",
        "BookListItem": Row,
    }
    # Search's GTK-free logic imports exact Core reference validation inside
    # this function. Only the widget row class is replaced.
    exec(compile(module, str(SEARCH), "exec"), env)
    return env["_render_search_results"]


def test_real_local_search_rerender_changes_signature_when_only_c_epoch_changes():
    doc = canonical_cover_document_ref("edition:one-source")
    known = {
        "source": "notice_c_public",
        "identity_id": "alice",
        "key": "c-public:" + doc.source_key,
        "provider_id": "paris",
        "title": "Titre immuable", "author": "Autrice",
        "thumbnail_url": None,
        "metadata": {"notice_c_epoch": 3, "notice_c_stale": False},
        "_cover_document_ref": doc,
    }
    produced = []
    service = NS(
        current_identity=NS(identity_id="alice"),
        provider_display_name=lambda provider: provider,
        _account_context_epoch=9, _source_context_epoch=4,
    )
    window = NS(
        service=service,
        _search_mode="local",
        _search_local_objects={known["key"]: known},
        _search_local_snapshot_dirty=False,
        search_controls=NS(state=lambda: NS(
            text="", field="all", filter=lambda rows: list(rows),
        )),
        search_page=NS(show_rows=lambda rows: produced.append(list(rows))),
        _attach_row_facets=lambda *_a: None,
        _update_search_count=lambda _count: None,
    )
    render = _production_search_render()
    assert render(window) == 1
    first = produced[-1][0]
    assert first._cover_document_ref == doc
    assert first._cover_public_c_epoch == 3

    # New C source epoch without any visible change of title or DocumentRef.
    known["metadata"]["notice_c_epoch"] = 4
    assert render(window) == 1
    updated = produced[-1][0]
    assert updated._cover_document_ref == first._cover_document_ref
    assert updated.title == first.title
    assert updated.thumbnail_url == first.thumbnail_url is None
    assert updated._cover_public_c_epoch == 4
    assert updated._public_row_content != first._public_row_content

    same, show_rows = production_methods()
    assert not same(first, updated)
    page, store, selected, perf, _states, _counts = page_with(first)
    show_rows(page, [updated])
    assert store.items[0] is updated
    assert selected.get_selected_item() is updated
    assert perf[-1][0] == "list.replace"

    # No catalogue N2 change: append-fast preserves the current same source.
    assert render(window) == 1
    unchanged = produced[-1][0]
    assert same(updated, unchanged)


def test_both_global_and_local_c_first_signatures_track_native_epoch():
    tree = ast.parse(SEARCH.read_text(encoding="utf-8"))
    render = next(x for x in tree.body
                  if isinstance(x, ast.FunctionDef)
                  and x.name == "_render_search_results")
    source = ast.unparse(render)
    assert "view_row._cover_public_c_epoch = epoch" in source
    assert "view._cover_public_c_epoch = epoch" in source
    # Two presentation-signature constructions: local and global Search.
    assert source.count("getattr(row, '_cover_public_c_epoch', None)") >= 2
    assert "type(epoch) is int and epoch > 0" in source
