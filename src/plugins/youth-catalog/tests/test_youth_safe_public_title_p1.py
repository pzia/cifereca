"""#1599: one safe title projection for Youth list/detail/BookRef/actions.

Headless cached books and public host only; never fetches data or acquires.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from youth_page import YouthView, book_ref, detail_presentation, list_entry
from tests.test_youth_page import youth_book


class Poison:
    def __str__(self):
        raise AssertionError("Bearer PRIVATE_ACCOUNT /home/PRIVATE_USER")


class SecretText(str):
    pass


@pytest.mark.parametrize("invalid", (
    Poison(),
    {"PRIVATE_SESSION": "not a title"},
    SecretText("PRIVATE_ACCOUNT"),
    "Public\u202ePRIVATE_TITLE",
    "PUBLIC\nPRIVATE_TITLE",
    "Z" * 2049,
    None,
))
def test_invalid_cached_titles_never_reach_list_detail_or_public_bookref(invalid):
    book = youth_book(record_id="safe-42", title=invalid, age_ranges=())
    row = list_entry(book)
    presentation = detail_presentation(book)
    reference = book_ref(book)
    assert row.title == presentation.title == reference.title == "Sans titre"
    assert not any(
        f.id == "title" for section in presentation.sections for f in section.fields
    )
    assert "PRIVATE" not in repr((row, presentation, reference))


def _view(book):
    view = object.__new__(YouthView)
    view.rows = {"the-edition": book}
    shown = []
    queries = []
    view.master = NS(
        show_detail=shown.append, empty_detail=lambda: None,
    )
    view.host = NS(search_books=lambda *args, **kwargs: queries.append((args, kwargs)) or True)
    view.identity_id = lambda: "alice"
    return view, shown, queries


def test_invalid_title_never_creates_a_library_search_action():
    for title in (Poison(), "PRIVATE\u202eTITLE", "Z" * 2049):
        book = youth_book(
            title=title, identifiers={"source_item_id": "only-local-id"}, age_ranges=(),
        )
        view, shown, calls = _view(book)
        YouthView.selected(view, NS(key="the-edition"))
        assert [action.id for action in shown[-1].actions] == []
        assert calls == []


def test_safe_isbn_lookup_survives_bad_title_without_forged_title_lookup():
    book = youth_book(
        title=Poison(),
        identifiers={"isbn": "9780306406157", "source_item_id": "record-42"},
        age_ranges=(),
    )
    view, shown, calls = _view(book)
    YouthView.selected(view, NS(key="the-edition"))
    assert [a.id for a in shown[-1].actions] == ["search-library-isbn"]
    assert shown[-1].actions[0].callback() is True
    assert calls == [(("9780306406157",), {
        "field": "isbn", "online": False, "identity_id": "alice",
    })]


def test_valid_multilingual_title_normalizes_identically_across_public_surfaces():
    book = youth_book(
        title="  L’Été   des  étoiles   —  第一卷  ",
        authors=("Une autrice",),
        age_ranges=(),
    )
    row, detail, reference = list_entry(book), detail_presentation(book), book_ref(book)
    assert row.title == detail.title == reference.title == "L’Été des étoiles — 第一卷"
    assert any(
        field.id == "title" and field.value == detail.title
        for section in detail.sections for field in section.fields
    )
    view, shown, calls = _view(book)
    YouthView.selected(view, NS(key="the-edition"))
    assert [a.id for a in shown[-1].actions] == ["search-library", "search-library-isbn"]
    assert shown[-1].actions[0].callback() is True
    assert calls[0] == ((detail.title,), {
        "field": "title", "online": False, "identity_id": "alice",
    })


def test_malformed_isbn_mapping_getter_cannot_prevent_safe_title_detail():
    class MalformedIdentifiers(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("PRIVATE_AUTH")

    book = youth_book(
        title="Titre attesté",
        identifiers=MalformedIdentifiers({"isbn": "9780306406157"}),
        age_ranges=(),
    )
    view, shown, calls = _view(book)
    YouthView.selected(view, NS(key="the-edition"))
    assert shown[-1].title == "Titre attesté"
    # The identifier map's custom get() cannot be trusted as attested ISBN.
    # The dict copy itself is safe but its public ISBN remains available.
    assert [a.id for a in shown[-1].actions] == ["search-library", "search-library-isbn"]
    assert "PRIVATE" not in repr(shown[-1])
