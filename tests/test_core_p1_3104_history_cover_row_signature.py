"""P1 #3104 — GTK History rows must repaint after an exact-source cover update.

Tests extract the real MainWindow facade without importing Gtk: the shared
MasterDetailPage has an append-fast branch which only notices a new
_public_row_content on same-key rows.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
MASTER = MAIN.with_name("master_detail.py")


class Row:
    def __init__(self, key, title, subtitle, meta, badge, thumbnail_url,
                 **kwargs):
        self.key = key
        self.title = title
        self.subtitle = subtitle
        self.meta = meta
        self.badge = badge
        self.thumbnail_url = thumbnail_url
        self.cover_provider = kwargs.get("cover_provider")
        self.cover_record_id = kwargs.get("cover_record_id")
        self.cover_title = kwargs.get("cover_title")
        self.cover_author = kwargs.get("cover_author")
        self.cover_year = kwargs.get("cover_year")
        self.default_thumbnail_url = kwargs.get("default_thumbnail_url")


def build_row():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "MainWindow")
    func = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                and n.name == "_history_book_row")
    env = {"BookListItem": Row, "fr_date": lambda value: value or "—"}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[])),
                 str(MAIN), "exec"), env)
    return env["_history_book_row"]


def history(**changes):
    fields = {
        "id": "history-1", "title": "Une histoire", "author": "A.",
        "borrowed_at": "2026-10-01", "year": "2025", "document_type": "Livre",
        "medium": "digital", "provider": "bnf-pret", "record_id": "edition-1",
        "thumbnail_url": None, "default_thumbnail_url": None,
        "source_provider": "bnf-pret", "source_credential": "card-1",
        "source_account_id": "account-1", "identity_id": "alice",
    }
    fields.update(changes)
    return NS(**fields)


def render(item, *, primary=True):
    host = NS(service=NS(current_account=NS(is_primary=primary)),
              search_controls=NS(), _attach_row_facets=lambda *args: None)
    return build_row()(host, item)


def test_same_key_missing_to_valid_cover_invalidates_gtk_presentation():
    first = render(history())
    later = render(history(thumbnail_url="https://covers.example/item/1.jpg"))
    assert first.key == later.key == "history-1"
    assert first._public_row_content != later._public_row_content
    assert first.thumbnail_url is None
    assert later.thumbnail_url == "https://covers.example/item/1.jpg"
    assert later._public_row_content[0] == "history"
    assert later._public_row_content[6] == later.thumbnail_url


def test_fallback_cover_and_primary_link_change_rebind_without_new_source_key():
    before = render(history())
    fallback = render(history(default_thumbnail_url="https://covers.example/default.jpg"))
    assert before.key == fallback.key
    assert before._public_row_content != fallback._public_row_content
    linked = render(history(), primary=False)
    assert linked.key == before.key
    assert linked.cover_provider is None
    assert before.cover_provider == "bnf-pret"
    assert linked._public_row_content != before._public_row_content


def test_different_provenance_and_loan_scope_are_distinct_presentation():
    original = render(history())
    for changed in (
        {"record_id": "edition-2"},
        {"source_provider": "other"},
        {"source_credential": "card-2"},
        {"source_account_id": "account-2"},
        {"identity_id": "bob"},
    ):
        other = render(history(**changed))
        assert original.key == other.key
        assert original._public_row_content != other._public_row_content


def test_unchanged_row_keeps_fast_append_path_and_no_extra_io():
    original = render(history(thumbnail_url="https://covers.example/item/1.jpg"))
    same = render(history(thumbnail_url="https://covers.example/item/1.jpg"))
    assert original._public_row_content == same._public_row_content
    source = MASTER.read_text(encoding="utf-8")
    assert 'getattr(old, "_public_row_content", None)' in source
    assert 'getattr(new, "_public_row_content", None)' in source
    method = ast.unparse(next(
        n for n in ast.walk(ast.parse(MAIN.read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef) and n.name == "_history_book_row"
    ))
    assert "service.holdings" not in method
    assert "service.library_entries" not in method
    assert "history_cover_resolver.request" not in method
