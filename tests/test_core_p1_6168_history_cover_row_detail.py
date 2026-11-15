"""#6168: History cover discovered in list/detail repaints the same exact row.

The existing HistoryCoverResolver's title search remains an optional
primary-account UI fallback. It is not an entitlement or a Core N1
documentary proof, and must never be replayed for another HistoryItem.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.history_cover_presentation import (
    cached_history_cover, history_cover_context, publish_history_cover,
)
from cifereca.models import HistoryItem

ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
IMAGE = "https://covers.example.org/history-one.png"


def item(*, owner="alice", account="primary", key="h-1",
         credential="card-a"):
    return HistoryItem(
        id=key, provider="paris", medium="digital", record_id="edition-1",
        title="Même titre, édition privée", author="Autrice",
        thumbnail_url=None, default_thumbnail_url=None,
        identity_id=owner, source_provider="paris",
        source_credential=credential, source_account_id=account,
    )


class Row:
    def __init__(self, source):
        self.key = source.id
        self._history_source_item = source
        self.thumbnail_url = source.thumbnail_url
        self._public_row_content = (
            "history", source.id, source.title, source.author,
            "metadata", "badge", source.thumbnail_url, None,
        )
        self.events = []

    def set_thumbnail_url(self, url):
        old = self.thumbnail_url
        self.thumbnail_url = url
        content = self._public_row_content
        if content[6] == old:
            self._public_row_content = (*content[:6], url, *content[7:])
        self.events.append(url)


def window(source):
    service = NS(
        current_identity=NS(identity_id="alice"),
        current_account=NS(is_primary=True),
        _account_context_epoch=3,
        _source_context_epoch=9,
    )
    return NS(
        service=service, _identity_context_generation=11,
        history_objects={source.id: source},
    )


def test_exact_history_item_is_painted_in_place_then_reused_in_list_and_detail():
    source = item()
    owner = window(source)
    row = Row(source)
    context = history_cover_context(owner)
    assert context.identity_id == "alice"
    assert cached_history_cover(owner, source) is None
    assert publish_history_cover(owner, source, row, IMAGE, context=context)
    assert row.events == [IMAGE]
    assert row.thumbnail_url == IMAGE
    assert row._public_row_content[6] == IMAGE
    assert cached_history_cover(owner, source) == IMAGE
    assert source.thumbnail_url is None  # business DTO untouched


@pytest.mark.parametrize("dimension", [
    "identity", "account_epoch", "source_epoch", "identity_generation",
    "account_kind",
])
def test_identity_epoch_and_linked_account_never_replay_image(dimension):
    source = item()
    owner = window(source)
    row = Row(source)
    context = history_cover_context(owner)
    assert publish_history_cover(owner, source, row, IMAGE, context=context)
    if dimension == "identity":
        owner.service.current_identity.identity_id = "bob"
    elif dimension == "account_epoch":
        owner.service._account_context_epoch += 1
    elif dimension == "source_epoch":
        owner.service._source_context_epoch += 1
    elif dimension == "identity_generation":
        owner._identity_context_generation += 1
    elif dimension == "account_kind":
        owner.service.current_account.is_primary = False
    assert cached_history_cover(owner, source) is None
    assert not publish_history_cover(
        owner, source, row, IMAGE, context=context,
    )
    assert source.thumbnail_url is None


def test_same_title_provider_and_record_but_different_source_object_is_unlinked():
    old = item()
    owner = window(old)
    row = Row(old)
    context = history_cover_context(owner)
    assert publish_history_cover(owner, old, row, IMAGE, context=context)
    fresh = replace(old)
    owner.history_objects[old.id] = fresh
    assert cached_history_cover(owner, fresh) is None
    assert cached_history_cover(owner, old) is None
    assert not publish_history_cover(owner, old, row, IMAGE, context=context)
    assert not publish_history_cover(owner, fresh, row, IMAGE, context=context)


def test_current_history_entry_never_paints_another_same_title_row():
    alice = item(key="h-a")
    bob = item(key="h-b", credential="card-b")
    owner = window(alice)
    owner.history_objects[bob.id] = bob
    assert not publish_history_cover(
        owner, alice, Row(bob), IMAGE,
        context=history_cover_context(owner),
    )
    assert cached_history_cover(owner, bob) is None


@pytest.mark.parametrize("bad_url", [
    "https://cdn.example.org/book.jpg?token=SECRET",
    "file:///home/alice/private.png",
    "https://127.0.0.1/admin.png",
    "javascript:alert(1)", "",
    None,
])
def test_private_or_unqualified_cover_is_not_painted(bad_url):
    source = item()
    owner = window(source)
    row = Row(source)
    assert not publish_history_cover(
        owner, source, row, bad_url,
        context=history_cover_context(owner),
    )
    assert row.events == [] and cached_history_cover(owner, source) is None


def test_account_aba_after_new_epoch_never_adopts_earlier_cover():
    source = item()
    owner = window(source)
    row = Row(source)
    old = history_cover_context(owner)
    assert publish_history_cover(owner, source, row, IMAGE, context=old)
    owner.service.current_identity.identity_id = "bob"
    owner.service._account_context_epoch += 1
    owner.service.current_identity.identity_id = "alice"
    assert cached_history_cover(owner, source) is None
    assert not publish_history_cover(owner, source, row, IMAGE, context=old)


def test_stale_cell_with_same_key_cannot_publish_after_rebound():
    source = item()
    owner = window(source)
    old_row = Row(source)
    old_row._history_source_item = replace(source)
    assert not publish_history_cover(
        owner, source, old_row, IMAGE,
        context=history_cover_context(owner),
    )


def test_128_item_cache_is_bounded_and_keyed_by_object_not_book_title():
    first = item()
    owner = window(first)
    context = history_cover_context(owner)
    for number in range(140):
        current = item(key="h-" + str(number))
        owner.history_objects[current.id] = current
        assert publish_history_cover(
            owner, current, Row(current), IMAGE,
            context=context,
        )
    assert len(owner._history_cover_presentation) == 128
    assert cached_history_cover(owner, first) is None
    assert cached_history_cover(owner, owner.history_objects["h-139"]) == IMAGE


def test_actual_history_row_projector_reuses_visual_cover_on_same_dto():
    source = item()
    owner = window(source)

    class BookRow:
        def __init__(self, key, title, subtitle, meta, badge, cover, **kwargs):
            self.key, self.title, self.subtitle = key, title, subtitle
            self.meta, self.badge, self.thumbnail_url = meta, badge, cover
            self.default_thumbnail_url = kwargs.get("default_thumbnail_url")
            self.cover_provider = kwargs.get("cover_provider")
            self.cover_record_id = kwargs.get("cover_record_id")
            self.cover_title = kwargs.get("cover_title")
            self.cover_author = kwargs.get("cover_author")
            self.cover_year = kwargs.get("cover_year")
        def set_thumbnail_url(self, url):
            self.thumbnail_url = url

    owner._attach_row_facets = lambda *_a: None
    owner.search_controls = None
    code = (ROOT / "main_window.py").read_text(encoding="utf-8")
    cls = next(n for n in ast.parse(code).body
               if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == "_history_book_row")
    namespace = {
        "__package__": "cifereca.gui",
        "BookListItem": BookRow, "fr_date": lambda value: "inconnu",
    }
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[fn], type_ignores=[]),
    ), str(ROOT / "main_window.py"), "exec"), namespace)
    render = namespace["_history_book_row"]
    blank = render(owner, source)
    assert blank.thumbnail_url is None
    assert blank.cover_provider == "paris"
    assert blank._history_source_item is source
    assert publish_history_cover(
        owner, source, blank, IMAGE,
        context=history_cover_context(owner),
    )
    rebuilt = render(owner, source)
    assert rebuilt.thumbnail_url == IMAGE
    assert rebuilt.cover_provider is None
    assert rebuilt._public_row_content[6] == IMAGE
    assert source.thumbnail_url is None


def test_history_cell_and_detail_both_reuse_the_exact_bound_model_and_signal():
    widgets = (ROOT / "book_widgets.py").read_text(encoding="utf-8")
    widget_ast = ast.parse(widgets)
    model = next(n for n in widget_ast.body
                 if isinstance(n, ast.ClassDef) and n.name == "BookListItem")
    setter = next(n for n in model.body
                  if isinstance(n, ast.FunctionDef) and n.name == "set_thumbnail_url")
    setter_source = ast.unparse(setter)
    assert 'content[0] == \'history\'' in setter_source
    assert 'content[6] == previous' in setter_source
    assert '"thumbnail-changed"' in widgets
    assert 'publish_history_cover(' in widgets
    detail = (ROOT / "detail_panel.py").read_text(encoding="utf-8")
    assert "cached_history_cover(self.window, item)" in detail
    assert "publish_history_cover(" in detail
    assert "not item.thumbnail_url and not present_cover" in detail
