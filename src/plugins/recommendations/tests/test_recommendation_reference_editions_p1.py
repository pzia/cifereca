"""#1599: identical title/author is not a stable reference-edition identifier."""
from types import SimpleNamespace as NS

from cifereca.plugin_api import BookRef
from recommendations import (
    UxRecommendationsView, _reference_label, _reference_selection_key,
)

ISBN_A = "9780306406157"
ISBN_B = "9780140328721"


class Model:
    def __init__(self):
        self.values = ["Toute la bibliothèque"]

    def get_n_items(self):
        return len(self.values)

    def remove(self, index):
        self.values.pop(index)

    def append(self, label):
        self.values.append(label)


class Dropdown:
    def __init__(self):
        self.selected = 0
        self.sensitive = True

    def set_selected(self, index):
        self.selected = index

    def get_selected(self):
        return self.selected

    def set_sensitive(self, value):
        self.sensitive = value


def view_for(reference):
    view = object.__new__(UxRecommendationsView)
    view._reference_book = reference
    view._reference_books = []
    view._reference_model = Model()
    view._reference = Dropdown()
    view._reloading_references = False
    view.plugin = NS(_pending_reference=reference)
    view.refresh = lambda: None
    return view


def book(isbn, local_id=None):
    return BookRef(
        book_id=local_id or ("local-" + isbn),
        title="Même titre",
        authors=("Une autrice",),
        identifiers={"isbn": isbn},
    )


def test_homonymous_editions_have_distinct_keys_and_explicit_isbn_labels():
    first, second = book(ISBN_A), book(ISBN_B)
    assert _reference_selection_key(first) != _reference_selection_key(second)
    assert f"ISBN {ISBN_A}" in _reference_label(first)
    assert f"ISBN {ISBN_B}" in _reference_label(second)


def test_reloading_identical_titles_restores_exact_selected_book():
    first, second = book(ISBN_A), book(ISBN_B)
    view = view_for(first)
    view._reload_reference_books([second, first])
    assert view._reference_books[view._reference.get_selected() - 1] is first
    view._reference_book = second
    view._reload_reference_books([first, second])
    assert view._reference_books[view._reference.get_selected() - 1] is second


def test_missing_selected_edition_is_preserved_in_reference_dropdown():
    chosen = book(ISBN_A)
    same_title_other_edition = book(ISBN_B)
    view = view_for(chosen)
    view._reload_reference_books([same_title_other_edition])
    assert len(view._reference_books) == 2
    assert view._reference_books[view._reference.get_selected() - 1] is chosen


def test_reloaded_copy_of_same_local_book_retains_reference_selection():
    original = book(ISBN_A)
    refreshed = book(ISBN_A)
    view = view_for(original)
    view._reload_reference_books([refreshed])
    assert len(view._reference_books) == 1
    assert view._reference_book is refreshed


def test_different_local_books_with_same_isbn_are_not_silently_swapped():
    first = book(ISBN_A, local_id="copy-A")
    second = book(ISBN_A, local_id="copy-B")
    assert _reference_selection_key(first) != _reference_selection_key(second)
    view = view_for(first)
    view._reload_reference_books([second])
    assert view._reference_books[view._reference.get_selected() - 1] is first


def test_strong_isbn_identifies_source_book_without_local_book_id():
    first = {"title": "Même titre", "authors": ["Une autrice"], "isbn": ISBN_A}
    second = {"title": "Même titre", "authors": ["Une autrice"], "isbn": ISBN_B}
    assert _reference_selection_key(first) == "isbn:" + ISBN_A
    assert _reference_selection_key(first) != _reference_selection_key(second)


def test_unattested_homonyms_remain_distinct_and_keep_original():
    original = {"title": "Même titre", "author": "Une autrice"}
    homonym = {"title": "Même titre", "author": "Une autrice"}
    assert _reference_selection_key(original) != _reference_selection_key(homonym)
    view = view_for(original)
    view._reload_reference_books([homonym])
    assert len(view._reference_books) == 2
    assert view._reference_books[view._reference.get_selected() - 1] is original
    assert "ISBN" not in _reference_label(original)


def test_invalid_isbn_does_not_create_false_edition_identity_or_label():
    value = {"title": "Même titre", "author": "Une autrice", "isbn": "9780000000000"}
    other = dict(value)
    assert _reference_selection_key(value) != _reference_selection_key(other)
    assert "ISBN" not in _reference_label(value)
