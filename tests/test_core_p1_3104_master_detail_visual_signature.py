"""#3104: the shared GTK list never keeps stale book paintables at a stable key.

Run the REAL MasterDetailPage.show_rows method headlessly with a lightweight
Gio.ListStore facade. No provider, file system or GUI startup is needed.
"""
from __future__ import annotations

import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/master_detail.py"


class BookRow:
    def __init__(self, key="exact:1", *, cover=None, title="Le livre",
                 author="Autrice", badge="Catalogue", fallback=None):
        self.key = key
        self.title = title
        self.subtitle = author
        self.meta = ""
        self.badge = badge
        self.badge_style = ""
        self.thumbnail_url = cover
        self.default_thumbnail_url = fallback
        self.cover_provider = None
        self.cover_record_id = None
        self.cover_title = title
        self.cover_author = None
        self.cover_year = None
        self.action_label = ""
        self.action_icon_name = "view-more-symbolic"


def production_methods(*extra_methods):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    page = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                and n.name == "MasterDetailPage")
    methods = {
        n.name: n for n in page.body if isinstance(n, ast.FunctionDef)
        and n.name in {"show_rows", *extra_methods}
    }
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_same_book_row_presentation")
    fields = next(n for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name)
                          and t.id == "_BOOK_ROW_VISIBLE_FIELDS"
                          for t in n.targets))
    ns = {
        "BookListItem": BookRow,
        "_BOOK_ROW_VISIBLE_FIELDS": ast.literal_eval(fields.value),
        "time": NS(perf_counter=lambda: 1.0),
        "Gtk": NS(SingleSelection=object),
        "capture_detail_selection": lambda *_args, **_kwargs: nullcontext(),
    }
    node = ast.fix_missing_locations(ast.Module(body=[helper, *methods.values()], type_ignores=[]))
    exec(compile(node, str(SOURCE), "exec"), ns)
    return (ns["_same_book_row_presentation"], ns["show_rows"],
            *(ns[name] for name in extra_methods))


def page_with(row):
    class Store:
        def __init__(self):
            self.items = [row]
            self.splice_count = 0
        def get_n_items(self):
            return len(self.items)
        def get_item(self, index):
            return self.items[index]
        def splice(self, start, delete, rows):
            self.splice_count += 1
            self.items[start:start + delete] = list(rows)
        def append(self, item):
            self.items.append(item)
        def remove_all(self):
            self.items.clear()
    store = Store()
    class Selection:
        def __init__(self):
            self.position = 0
            self.changed = None
        def get_selected_item(self):
            if 0 <= self.position < len(store.items):
                return store.items[self.position]
            return None
        def set_selected(self, position):
            previous = self.get_selected_item()
            self.position = position
            if self.changed is not None and self.get_selected_item() is not previous:
                self.changed()
    selection = Selection()
    perf = []
    states = []
    counts = []
    obj = NS(
        store=store, selection=selection,
        window=NS(search_page=None, ui_perf=NS(record=lambda *a: perf.append(a))),
        list_stack=NS(set_visible_child_name=states.append),
        _update_count=counts.append,
        _narrow_detail_visible=False, _replacing_rows=False,
        clear_rows=lambda: store.items.clear(),
    )
    return obj, store, selection, perf, states, counts


@pytest.mark.parametrize("change", [
    {"cover": "https://public.example/exact.png"},
    {"title": "Le livre — édition attestée"},
    {"author": "Autrice enrichie"},
    {"badge": "Prêt BnF"},
    {"fallback": "https://public.example/fallback.png"},
])
def test_same_source_key_with_changed_visible_content_replaces_real_gtk_row(change):
    equal, show = production_methods()
    old = BookRow()
    new = BookRow(**change)
    assert old.key == new.key
    assert not equal(old, new)
    page, store, selected, perf, states, counts = page_with(old)
    show(page, [new], total_count=1)
    assert store.items == [new] and store.splice_count == 1
    assert selected.get_selected_item() is new
    assert perf[-1][0] == "list.replace"
    assert states == ["list"] and counts == [1]


def test_wrong_or_incomplete_explicit_revision_cannot_mask_cover_change():
    equal, show = production_methods()
    old = BookRow(cover=None)
    new = BookRow(cover="https://public.example/exact.jpg")
    old._public_row_content = new._public_row_content = ("loan", old.key)
    assert equal(old, new) is False
    page, store, selection, perf, _, _ = page_with(old)
    show(page, [new])
    assert store.items[0] is new
    assert perf[-1][0] == "list.replace"


def test_explicit_action_context_change_forces_rebind_even_when_visuals_match():
    equal, show = production_methods()
    old, new = BookRow(), BookRow()
    old._public_row_content = ("search", "credential-a")
    new._public_row_content = ("search", "credential-b")
    assert not equal(old, new)
    page, store, _, perf, _, _ = page_with(old)
    show(page, [new])
    assert store.items[0] is new
    assert perf[-1][0] == "list.replace"


def test_unchanged_book_and_progressive_append_keep_fast_path_and_selection():
    equal, show = production_methods()
    old, equivalent = BookRow(), BookRow()
    assert equal(old, equivalent)
    page, store, selection, perf, states, counts = page_with(old)
    second = BookRow("exact:2", cover="https://public.example/second.jpg")
    show(page, [equivalent, second], total_count=4)
    assert store.splice_count == 1  # only append the tail
    assert store.items == [old, second]  # unchanged first GObject is reused
    assert selection.get_selected_item() is old
    assert perf[-1][0] == "list.append-fast"
    assert states == ["list"] and counts == [4]



def test_documentref_change_with_identical_url_forces_rebind_and_keeps_selection():
    from dataclasses import replace
    from cifereca.core.book_cover_projection import CoverDocumentScope

    equal, show = production_methods()
    book_a = CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        source_key="notice:exact-edition-a", provider_id="bnf",
        credential_id="__public__",
    )
    book_b = replace(book_a, source_key="notice:exact-edition-b")
    old = BookRow(cover="https://covers.example.org/identical.jpg")
    new = BookRow(cover=old.thumbnail_url)
    old._cover_document_ref = book_a
    new._cover_document_ref = book_b
    old._public_row_content = new._public_row_content = ("search", "exact")
    assert not equal(old, new)

    page, store, selection, perf, _, _ = page_with(old)
    show(page, [new], total_count=1)
    assert store.items[0] is new
    assert selection.get_selected_item() is new
    assert perf[-1][0] == "list.replace"


def test_cover_provenance_change_rebinds_when_other_fields_remain_equal():
    from dataclasses import dataclass, field

    @dataclass(frozen=True)
    class SourcePacket:
        # Search's source_item is intentionally excluded from equality,
        # yet registration requires original SearchResult object identity.
        source_item: object = field(compare=False)
        context: tuple[int, int, int, int] = (1, 2, 3, 4)
        source_uri: str = "https://covers.example.org/same.jpg"

    equal, show = production_methods()
    source_a, source_b = object(), object()
    old = BookRow(cover="https://covers.example.org/same.jpg")
    new = BookRow(cover=old.thumbnail_url)
    old._cover_search_evidence = SourcePacket(source_a)
    new._cover_search_evidence = SourcePacket(source_b)
    old._public_row_content = new._public_row_content = ("search", "same")
    assert not equal(old, new)

    page, store, _, perf, _, _ = page_with(old)
    show(page, [new])
    assert store.items[0] is new
    assert perf[-1][0] == "list.replace"

    later = BookRow(cover=old.thumbnail_url)
    later._cover_search_evidence = SourcePacket(
        source_a, context=(1, 2, 3, 5),
    )
    assert not equal(old, later)


def test_identical_core_proof_keeps_progressive_append_fast_path():
    from dataclasses import dataclass
    from cifereca.core.book_cover_projection import CoverDocumentScope

    @dataclass(frozen=True)
    class ImmutableSource:
        evidence_id: str
        candidates: tuple[str, ...]

    equal, show = production_methods()
    ref = CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        source_key="notice:exact-edition-a", provider_id="bnf",
        credential_id="__public__",
    )
    old, equivalent = BookRow(), BookRow()
    old._cover_document_ref = equivalent._cover_document_ref = ref
    old._cover_local_evidence = ImmutableSource("exact", ("public-cover-a",))
    equivalent._cover_local_evidence = ImmutableSource("exact", ("public-cover-a",))
    assert equal(old, equivalent)

    page, store, selection, perf, _, _ = page_with(old)
    show(page, [equivalent, BookRow("exact:2")])
    assert store.items[0] is old
    assert store.splice_count == 1
    assert selection.get_selected_item() is old
    assert perf[-1][0] == "list.append-fast"

    changed = BookRow()
    changed._cover_document_ref = ref
    changed._cover_local_evidence = ImmutableSource("exact", ("public-cover-b",))
    assert not equal(old, changed)


def test_removing_documentary_proof_forces_rebind_before_legacy_url_fallback():
    equal, show = production_methods()
    old, fresh = BookRow(cover="https://covers.example.org/cached.png"), BookRow(
        cover="https://covers.example.org/cached.png",
    )
    old._cover_history_evidence = object()
    assert not equal(old, fresh)
    page, store, _, perf, _, _ = page_with(old)
    show(page, [fresh])
    assert store.items[0] is fresh
    assert perf[-1][0] == "list.replace"


def narrow_page_with(row, open_detail):
    _, show, selection_changed, clear = production_methods("_selection_changed", "clear_rows")
    page, _store, selection, _perf, _states, _counts = page_with(row)
    navigation = []

    def navigate(detail):
        page._narrow_detail_visible = detail
        navigation.append("detail" if detail else "list")

    page._narrow_master_detail = True
    page._narrow_detail_visible = open_detail
    page._show_narrow_detail = lambda: navigate(True)
    page._show_narrow_list = lambda: navigate(False)
    page._selected_callbacks = []
    page.detail = NS(empty=lambda *_args: None)
    page.count_label = NS(set_label=lambda _label: None)
    page.clear_rows = lambda: clear(page)
    selection.changed = lambda: selection_changed(page, selection, None)
    return page, show, navigation


@pytest.mark.parametrize("open_detail", [True, False])
def test_refresh_restores_selected_key_without_changing_the_narrow_view(open_detail):
    old, updated = BookRow(), BookRow(title="Notice enrichie")
    page, show, navigation = narrow_page_with(old, open_detail)
    selected = []
    page._selected_callbacks.append(selected.append)

    show(page, [updated])

    assert page.selection.get_selected_item() is updated
    assert page._narrow_detail_visible is open_detail
    assert navigation == []
    assert selected == [None, updated]


@pytest.mark.parametrize("replacement", [[], [BookRow("exact:2")]])
def test_refresh_returns_to_narrow_list_when_selected_document_disappears(replacement):
    page, show, navigation = narrow_page_with(BookRow(), True)

    show(page, replacement)

    assert page.selection.get_selected_item() is None
    assert not page._narrow_detail_visible
    assert navigation == ["list"]


def test_clearing_a_narrow_page_returns_to_the_list():
    page, _show, navigation = narrow_page_with(BookRow(), True)

    page.clear_rows()

    assert page.selection.get_selected_item() is None
    assert not page._narrow_detail_visible
    assert navigation == ["list"]


def test_row_replacement_fallback_preserves_narrow_detail_for_the_retained_key(monkeypatch):
    page, show, navigation = narrow_page_with(BookRow(), True)
    updated = BookRow(title="Notice enrichie")

    def splice_failure(*_args):
        raise RuntimeError("splice unavailable")

    monkeypatch.setattr(page.store, "splice", splice_failure)
    show(page, [updated])

    assert page.selection.get_selected_item() is updated
    assert page._narrow_detail_visible
    assert navigation == []


@pytest.mark.parametrize("open_detail", [True, False])
@pytest.mark.parametrize("duplicate_before", [True, False])
def test_ambiguous_selection_key_is_not_restored_on_refresh(open_detail, duplicate_before):
    old = BookRow()
    page, show, navigation = narrow_page_with(old, open_detail)
    if duplicate_before:
        page.store.append(BookRow(old.key, title="Autre source"))
        replacement = [BookRow(old.key, title="Nouvelle notice")]
    else:
        replacement = [BookRow(old.key), BookRow(old.key, title="Autre source")]
    show(page, replacement)
    assert page.selection.get_selected_item() is None
    assert page.store.items == replacement
    assert navigation == (["list"] if open_detail else [])


def test_select_key_refuses_duplicate_without_changing_selection():
    _, _, select = production_methods("select_key")
    initial = BookRow("current")
    page, store, selection, *_ = page_with(initial)
    store.append(BookRow("ambiguous"))
    store.append(BookRow("ambiguous", title="Autre source"))
    assert select(page, "ambiguous") is False
    assert selection.get_selected_item() is initial


@pytest.mark.parametrize("change", ["replace", "remove", "move", "duplicate", "reselect"])
def test_select_key_does_not_focus_stale_index_after_synchronous_notification(change):
    _, _, select = production_methods("select_key")
    initial = BookRow("current")
    target = BookRow("target")
    page, store, selection, *_ = page_with(initial)
    store.append(target)
    scrolled = []
    page.listview = NS(scroll_to=lambda *args: scrolled.append(args))
    select.__globals__["Gtk"].ListScrollFlags = NS(FOCUS=1)

    def on_selected():
        # Avoid recursive notification in the synthetic selection facade.
        selection.changed = None
        if change == "replace":
            store.items[1] = BookRow(target.key, title="Autre instance")
        elif change == "remove":
            store.items.pop()
        elif change == "move":
            store.items[:] = [target, initial]
            selection.position = 0
        elif change == "duplicate":
            store.append(BookRow(target.key, title="Autre source"))
        elif change == "reselect":
            selection.position = 0

    selection.changed = on_selected
    assert select(page, target.key) is False
    assert scrolled == []


def test_select_key_focuses_exact_still_selected_row_after_independent_append():
    _, _, select = production_methods("select_key")
    initial, target = BookRow("current"), BookRow("target")
    page, store, selection, *_ = page_with(initial)
    store.append(target)
    scrolled = []
    page.listview = NS(scroll_to=lambda *args: scrolled.append(args))
    select.__globals__["Gtk"].ListScrollFlags = NS(FOCUS=1)
    selection.changed = lambda: store.append(BookRow("independent"))

    assert select(page, target.key) is True
    assert selection.get_selected_item() is target
    assert scrolled == [(1, 1, None)]
