"""#6168 N4: repaint the SAME exact Loan list cell, without list splice.

The controller tests execute the real _render_loans and the existing exact
loan/N1/N2 projection from the Core. No GTK or provider I/O is involved.
"""
from __future__ import annotations

import ast
from copy import copy, deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.loan_cover_repaint import repaint_exact_loan_cover_rows
from cifereca.core.loan_source_identity import loan_source_key
from cifereca.core.loan_cover_fallback import loan_local_cover
from cifereca.core.covers import candidate
from cifereca.gui.loan_view import (
    LOAN_FILTER_LABELS, loan_context_label, loan_due_presentation,
    loan_medium_counts, loan_row_metadata,
)
from cifereca.models import Loan
from tests.test_core_p1_4979_loan_gtk_projection import a_loan, a_row
from tests.test_core_p1_4979_late_library_cover_repaint import snapshot_method


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/cifereca/gui/loans_controller.py"


class Row:
    def __init__(self, key, title, subtitle, meta, badge, thumbnail, *,
                 badge_style=None):
        self.key = key
        self.title = title
        self.subtitle = subtitle
        self.meta = meta
        self.badge = badge
        self.badge_style = badge_style
        self.thumbnail_url = thumbnail
        self.changed = []

    def set_thumbnail_url(self, url):
        if self.thumbnail_url == url:
            return False
        self.thumbnail_url = url
        self.changed.append(url)
        return True


class Store:
    def __init__(self):
        self.rows = []

    def get_n_items(self):
        return len(self.rows)

    def get_item(self, i):
        return self.rows[i]


class Page:
    def __init__(self):
        self.store = Store()
        self.replaces = 0
        self.appends = 0
        self.selected_key = None
        self.detail_covers = []
        self.detail = NS(show_loan=lambda loan: self.detail_covers.append(loan.thumbnail_url))
        self.selection = NS(get_selected_item=self._selected_item)

    def _selected_item(self):
        return next(
            (row for row in self.store.rows if row.key == self.selected_key),
            None,
        )

    def show_rows(self, rows, *, total_count=None):
        # Production MasterDetailPage.show_rows uses exactly this
        # signature/visible-fields gate before choosing an append-fast path.
        old = self.store.rows
        if (old and len(old) == len(rows)
                and all(
                    a.key == b.key
                    and a._public_row_content == b._public_row_content
                    and a.thumbnail_url == b.thumbnail_url
                    for a, b in zip(old, rows)
                )):
            self.appends += 1
            return
        self.replaces += 1
        self.store.rows = list(rows)


def render_method():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    methods = [
        next(x for x in tree.body
             if isinstance(x, ast.FunctionDef) and x.name == name)
        for name in ("_loan_display_cover", "_render_loans")
    ]
    namespace = {
        "Loan": Loan,
        "copy": copy,
        "loan_local_cover": loan_local_cover,
        "candidate": candidate,
        "BookListItem": Row,
        "loan_source_key": loan_source_key,
        "loan_due_presentation": loan_due_presentation,
        "loan_row_metadata": loan_row_metadata,
        "loan_medium_counts": loan_medium_counts,
        "loan_context_label": loan_context_label,
        "LOAN_FILTER_LABELS": LOAN_FILTER_LABELS,
    }
    code = compile(ast.fix_missing_locations(ast.Module(
        body=methods, type_ignores=[],
    )), str(SOURCE), "exec")
    exec(code, namespace)
    return namespace["_render_loans"]


def host():
    book = a_loan()
    page = Page()
    service = NS(
        current_identity=NS(identity_id="alice"),
        _account_context_epoch=42,
        _source_context_epoch=7,
        last_loans=[book],
    )
    obj = NS(
        service=service,
        loan_filter="all",
        loan_objects={loan_source_key(book): book},
        loan_page=page,
        search_controls=None,
        _library_entries_cache=(),
        _library_all_profiles=False,
        _loan_cover_projection=(42, "alice", ()),
        _attach_row_facets=lambda *_args: None,
    )
    obj._render_loans = lambda: render_method()(obj)
    obj._displayed_loan = book
    obj._render_loans()
    page.selected_key = loan_source_key(book)
    return obj, book, page


def test_same_exact_loan_cover_updates_bound_cell_without_splice_or_reselection():
    obj, book, page = host()
    prior = page.store.rows[0]
    assert prior.thumbnail_url is None
    initial_replaces = page.replaces
    snapshot_method()(obj, [a_row(book)])
    assert page.replaces == initial_replaces
    assert page.appends == 1
    assert page.store.rows[0] is prior
    assert page.selection.get_selected_item() is prior
    assert prior.changed == ["file:///home/alice/.cache/cifereca/cover.png"]
    assert page.detail_covers == [prior.thumbnail_url]
    assert book.thumbnail_url is None
    assert obj._loan_cover_in_place_revision is None
    assert prior._public_row_content[-1] == ("alice", 42, 7)


def test_two_exact_revisions_emit_once_per_changed_url_and_update_detail():
    obj, book, page = host()
    first = a_row(book)
    snapshot_method()(obj, [first])
    original = page.store.rows[0]
    revised = deepcopy(first)
    revised["thumbnail_url"] = "file:///home/alice/.cache/cifereca/next.png"
    revised["metadata"]["cover_resolution"]["url"] = revised["thumbnail_url"]
    revised["_exact_loan_cover_proofs"][0]["url"] = revised["thumbnail_url"]
    snapshot_method()(obj, [revised])
    assert page.replaces == 1
    assert page.appends == 2
    assert page.store.rows[0] is original
    assert original.changed == [
        first["thumbnail_url"], revised["thumbnail_url"],
    ]
    assert page.detail_covers == original.changed
    snapshot_method()(obj, [revised])
    assert page.replaces == 1
    assert original.changed == page.detail_covers
    assert book.thumbnail_url is None


def test_empty_authoritative_snapshot_revokes_old_visual_cover_in_place():
    obj, book, page = host()
    snapshot_method()(obj, [a_row(book)])
    original = page.store.rows[0]
    assert original.thumbnail_url
    snapshot_method()(obj, [])
    assert page.store.rows[0] is original
    assert original.thumbnail_url is None
    assert original.changed[-1] is None
    assert page.detail_covers[-1] is None


def test_other_non_image_presentation_change_uses_normal_full_replacement():
    obj, book, page = host()
    stale = page.store.rows[0]
    fresh = copy(book)
    fresh.title = "Titre renommé"
    obj.service.last_loans = [fresh]
    obj.loan_objects[loan_source_key(fresh)] = fresh
    snapshot_method()(obj, [a_row(fresh)])
    assert page.replaces == 2
    assert page.store.rows[0] is not stale
    assert stale.changed == []


def test_account_epoch_aba_never_updates_an_old_bound_cell():
    obj, book, page = host()
    original = page.store.rows[0]
    obj.service.current_identity.identity_id = "bob"
    obj.service._account_context_epoch = 43
    obj.service.current_identity.identity_id = "alice"
    snapshot_method()(obj, [a_row(book)])
    assert page.replaces == 2
    assert page.store.rows[0] is not original
    assert original.changed == []
    assert original.thumbnail_url is None


def test_source_epoch_rebind_never_reuses_an_old_cell():
    obj, book, page = host()
    original = page.store.rows[0]
    obj.service._source_context_epoch = 8
    snapshot_method()(obj, [a_row(book)])
    assert page.replaces == 2
    assert page.store.rows[0] is not original
    assert original.changed == []


def test_unselected_loan_row_only_receives_signal_without_detail_reopen():
    obj, book, page = host()
    page.selected_key = "another-absent-key"
    snapshot_method()(obj, [a_row(book)])
    assert page.store.rows[0].thumbnail_url
    assert page.store.rows[0].changed
    assert page.detail_covers == []


def test_non_exact_foreign_proof_does_not_trigger_any_image_paint():
    obj, book, page = host()
    foreign = a_row(a_loan(account="foreign"))
    original = page.store.rows[0]
    snapshot_method()(obj, [foreign])
    assert page.store.rows[0] is original
    assert original.changed == []
    assert page.detail_covers == []


def test_helper_rejects_different_order_identity_source_epoch_or_title_atomically():
    obj, book, page = host()
    old = page.store.rows[0]
    first = copy(old)
    first.thumbnail_url = "https://example.org/one.jpg"
    first._public_row_content = (
        *old._public_row_content[:6], first.thumbnail_url,
        *old._public_row_content[7:],
    )
    first._loan_cover_context = old._loan_cover_context
    mismatch = copy(first)
    mismatch.title = "Nouveau titre"
    assert repaint_exact_loan_cover_rows(
        page, [mismatch], context=("alice", 42, 7),
    ) is None
    assert repaint_exact_loan_cover_rows(
        page, [first], context=("alice", 43, 7),
    ) is None
    assert repaint_exact_loan_cover_rows(
        page, [first], context=("alice", 42, 8),
    ) is None
    assert old.thumbnail_url is None
    assert old.changed == []


def test_helper_updates_no_row_if_second_row_fails_verification():
    a, b = Row("a", "Livre A", "", "", "", None), Row(
        "b", "Livre B", "", "", "", None,
    )
    current = ("alice", 1, 1)
    for row in (a, b):
        row._loan_cover_context = current
        row._public_row_content = (
            "loan", row.key, row.title, "", "", "", None, "", current,
        )
    page = Page()
    page.store.rows = [a, b]
    good = copy(a)
    good.thumbnail_url = "https://example.org/good.jpg"
    good._public_row_content = (
        "loan", a.key, a.title, "", "", "", good.thumbnail_url, "", current,
    )
    bad = copy(b)
    bad.key = "wrong-source-key"
    assert repaint_exact_loan_cover_rows(
        page, [good, bad], context=current,
    ) is None
    assert a.thumbnail_url is None and a.changed == []
    assert b.thumbnail_url is None and b.changed == []


def test_loan_render_controller_guards_fast_path_by_revision_and_provenance():
    src = SOURCE.read_text(encoding="utf-8")
    ast.parse(src)
    assert "repaint_exact_loan_cover_rows(" in src
    assert "self._loan_cover_in_place_revision" in src
    assert "self.loan_page.show_rows(rows, total_count=len(rows))" in src
    assert "self.loan_page.detail.show_loan(_loan_display_cover(self, fresh))" in src
    assert "loan_source_key(current) == selected_key" in src
    assert "loan_source_key(fresh) == selected_key" in src
