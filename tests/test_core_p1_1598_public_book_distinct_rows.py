"""#1598: the public plugin book adapter must not conflate identical row keys.

Run the actual adapter class without importing GI; Gtk, Core services, and
plugin/network actions are not needed for list projection and selection.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "src/cifereca/gui/public_book_view.py"
)


class ListItem:
    def __init__(self, key, title, subtitle, meta, badge, thumbnail_url):
        self.key = key
        self.title = title
        self.subtitle = subtitle
        self.meta = meta
        self.badge = badge
        self.thumbnail_url = thumbnail_url


class Page:
    def __init__(self, *_args):
        self.rows = []
        self.total_count = None
        self.selected = None

    def show_rows(self, rows, total_count=None):
        self.rows = rows
        self.total_count = total_count

    def connect_selected(self, callback):
        self.selected = callback


def adapter():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    klass = next(
        node for node in tree.body if isinstance(node, ast.ClassDef)
        and node.name == "PublicBookMasterDetailView"
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[
                ast.alias(name="annotations")], level=0),
            klass,
        ],
        type_ignores=[],
    ))
    namespace = dict(MasterDetailPage=Page, BookListItem=ListItem, Any=object)
    exec(compile(module, str(SOURCE), "exec"), namespace)
    view = namespace["PublicBookMasterDetailView"](
        NS(), title="Catalogue", empty_desc="Vide",
    )
    return view


def book(key, title, provider):
    return NS(
        key=key, title=title, subtitle="Auteur", meta=provider,
        badge="Livre", thumbnail_url=None, provider_id=provider,
    )


def test_generator_rows_are_projected_once_and_keep_original_keys():
    view = adapter()
    a, b = book("a", "Alpha", "paris"), book("b", "Beta", "kobo")
    produced = []
    def stream():
        for item in (a, b):
            produced.append(item)
            yield item
    chosen = []
    view.connect_selected(chosen.append)
    view.show_rows(stream(), total_count=11)
    assert produced == [a, b]
    assert [r.key for r in view._page.rows] == ["a", "b"]
    assert view._page.total_count == 11
    for row in view._page.rows:
        view._page.selected(row)
    assert chosen == [a, b]
    assert set(view._rows) == {"a", "b"}


def test_duplicate_provider_keys_never_select_the_last_book_for_both():
    view = adapter()
    first = book("same", "Compte Alice", "paris")
    second = book("same", "Compte Bob", "paris")
    third = book("same", "Catalogue tiers", "youth")
    chosen = []
    view.connect_selected(chosen.append)
    view.show_rows((first, second, third))
    keys = [row.key for row in view._page.rows]
    assert keys[0] == "same"  # legacy unique/first key remains stable
    assert len(set(keys)) == 3
    assert [r.title for r in view._page.rows] == [
        "Compte Alice", "Compte Bob", "Catalogue tiers",
    ]
    for row in view._page.rows:
        view._page.selected(row)
    assert chosen == [first, second, third]
    assert first.key == second.key == third.key == "same"


def test_generated_keys_cannot_collide_with_real_source_key():
    view = adapter()
    first = book("item", "One", "a")
    collision = book("item::row-2", "Real name", "b")
    duplicate = book("item", "Two", "c")
    view.show_rows([first, collision, duplicate])
    keys = [row.key for row in view._page.rows]
    assert keys[0] == "item"
    assert keys[1] == "item::row-2"
    assert keys[2] not in {"item", "item::row-2"}
    assert len(set(keys)) == 3
    assert [view._rows[key] for key in keys] == [first, collision, duplicate]


def test_empty_refresh_discards_stale_private_row_mapping():
    view = adapter()
    private = book("secret-old", "Private item", "account-A")
    view.show_rows([private])
    view.show_rows([])
    assert view._page.rows == []
    assert view._rows == {}
    chosen = []
    view.connect_selected(chosen.append)
    view._page.selected(NS(key="secret-old"))
    assert chosen == [None]
    next_book = book("new", "New", "account-B")
    view.show_rows([next_book])
    view._page.selected(view._page.rows[0])
    assert chosen == [None, next_book]
