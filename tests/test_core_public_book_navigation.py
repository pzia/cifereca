from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.book_navigation import library_key_for_book
from cifereca.plugin_api import BookRef, UiBookNavigationHost


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _rows():
    return [
        {
            "key": "artifact:sha256:alice123", "identity_id": "alice",
            "book_id": "paris-1", "item_id": "paris-1",
            "provider_id": "paris", "title": "Titre identique",
            "source_refs": [{"provider_id": "paris", "item_id": "paris-1"}],
        },
        {
            "key": "artifact:sha256:kobo123", "identity_id": "alice",
            "book_id": "kobo-1", "item_id": "kobo-1",
            "provider_id": "kobo", "title": "Titre identique",
            "source_refs": [
                {"provider_id": "kobo", "item_id": "kobo-1"},
                {"provider_id": "bnf", "item_id": "bnf-1"},
            ],
        },
        {
            "key": "artifact:sha256:bob123", "identity_id": "bob",
            "book_id": "paris-1", "item_id": "paris-1",
            "provider_id": "paris", "title": "Titre identique",
            "source_refs": [{"provider_id": "paris", "item_id": "paris-1"}],
        },
    ]


def test_public_contract_is_optional_and_structural():
    host = NS(
        toast=lambda message, timeout=3: None,
        navigate=lambda route: None,
        plugin_statuses=lambda: [],
        open_book_detail=lambda book, *, identity_id=None: True,
    )
    assert isinstance(host, UiBookNavigationHost)
    del host.open_book_detail
    assert not isinstance(host, UiBookNavigationHost)


def test_find_exact_book_in_active_identity_not_other_user():
    rows = _rows()
    ref = BookRef("paris-1", title="Titre identique",
                  identifiers={"provider": "paris", "source_item_id": "paris-1"})
    assert library_key_for_book(ref, rows, identity_id="alice") == "artifact:sha256:alice123"
    assert library_key_for_book(ref, rows, identity_id="bob") == "artifact:sha256:bob123"
    assert library_key_for_book(ref, rows, identity_id="charlie") is None


def test_provider_source_pair_can_match_secondary_source_of_deduplicated_book():
    ref = BookRef("bnf-1", identifiers={"provider": "bnf", "source_item_id": "bnf-1"})
    assert library_key_for_book(ref, _rows(), identity_id="alice") == "artifact:sha256:kobo123"
    assert library_key_for_book(
        BookRef("bnf-1", identifiers={"provider": "other", "source_item_id": "bnf-1"}),
        _rows(), identity_id="alice",
    ) is None


def test_title_or_isbn_never_guesses_a_book_when_id_not_known():
    for book in (
        BookRef("", title="Titre identique"),
        BookRef("unknown", title="Titre identique"),
        BookRef("", identifiers={"isbn": "9780306406157"}),
    ):
        assert library_key_for_book(book, _rows(), identity_id="alice") is None


def test_ambiguous_book_ids_never_choose_arbitrary_source():
    rows = _rows()
    rows.append({
        "key": "artifact:sha256:other", "identity_id": "alice",
        "item_id": "paris-1", "provider_id": "other",
    })
    assert library_key_for_book(BookRef("paris-1"), rows, identity_id="alice") is None
    assert library_key_for_book(
        BookRef("paris-1", identifiers={"provider": "paris"}),
        rows, identity_id="alice",
    ) == "artifact:sha256:alice123"


def test_duplicate_provider_source_pair_rejected_instead_of_navigating_wrong_book():
    rows = _rows()
    rows.append({
        "key": "source:paris:other", "identity_id": "alice",
        "item_id": "paris-1", "provider_id": "paris",
    })
    ref = BookRef("paris-1", identifiers={"provider": "paris", "source_item_id": "paris-1"})
    assert library_key_for_book(ref, rows, identity_id="alice") is None


def test_missing_keys_stale_identity_and_bad_source_shapes_fail_closed():
    rows = [
        {"item_id": "id", "identity_id": "alice", "provider_id": "local"},
        {"key": "bob", "item_id": "id", "identity_id": "bob", "provider_id": "local"},
        {"key": "alice-good", "item_id": "yes", "identity_id": "alice",
         "provider_id": "local", "source_refs": [None, "bogus", {}]},
    ]
    assert library_key_for_book(BookRef("id"), rows, identity_id="alice") is None
    assert library_key_for_book(BookRef("yes"), rows, identity_id="alice") == "alice-good"
    assert library_key_for_book(BookRef("bob"), rows, identity_id="alice") is None
    assert library_key_for_book(BookRef("yes"), rows, identity_id="") is None


def _window_method():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "open_book_detail")
    return ast.fix_missing_locations(ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            method,
        ],
        type_ignores=[],
    )), cls


def test_real_window_callback_navigates_only_to_unique_current_identity_book():
    module, _ = _window_method()
    from cifereca.gui import library_navigation
    ns = {"__package__": "cifereca.gui", "library_navigation": library_navigation}
    exec(compile(module, str(MAIN), "exec"), ns)
    opened = []
    window = NS(
        service=NS(current_identity=NS(identity_id="alice")),
        _library_entries_cache=_rows(),
        _open_library_item=lambda key: opened.append(key),
    )
    navigate = ns["open_book_detail"]
    assert navigate(window, BookRef("kobo-1", identifiers={"provider": "kobo"})) is True
    assert opened == ["artifact:sha256:kobo123"]
    assert navigate(window, BookRef("paris-1"), identity_id="bob") is False
    assert navigate(window, BookRef("missing")) is False
    assert opened == ["artifact:sha256:kobo123"]
    window.service.current_identity = NS(identity_id="bob")
    assert navigate(window, BookRef("paris-1"), identity_id="alice") is False
    assert navigate(window, BookRef("paris-1")) is True
    assert opened[-1] == "artifact:sha256:bob123"


def test_public_ui_host_delegates_book_navigation_to_window():
    _, cls = _window_method()
    from tests.gui_source import gui_source_text
    host = next(node for node in ast.parse(gui_source_text(MAIN)).body
                if isinstance(node, ast.ClassDef) and node.name == "_UiHost")
    method = next(node for node in host.body if isinstance(node, ast.FunctionDef)
                  and node.name == "open_book_detail")
    source = ast.unparse(method)
    assert "self.window.open_book_detail(book, identity_id=identity_id)" in source
    assert "def open_book_detail" in ast.unparse(cls)
