"""CoreUI P0 #1129: language evidence never crosses provider/item boundaries."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.known_languages_controller import known_item_languages


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "known_languages_controller.py"


def _window(entries=()):
    calls = []
    def catalog_entries():
        calls.append("catalog")
        return list(entries)
    window = NS(service=NS(storage=NS(catalog_entries=catalog_entries)))
    return window, calls


def _row(provider, item, metadata):
    return {"provider_id": provider, "item_id": item, "metadata": metadata}


def test_headless_controller_keeps_public_mainwindow_facade_signature():
    module = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(module)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in item or "main_window" in item for item in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in main.body
               if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    method = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_known_item_languages")
    assert [arg.arg for arg in method.args.args] == [
        "self", "provider_id", "item_id", "metadata",
    ]
    assert "known_languages_controller.known_item_languages" in ast.unparse(method)


def test_explicit_language_from_provider_metadata_is_used_without_local_catalog():
    window, calls = _window()
    assert known_item_languages(
        window, "paris", None, {"language": "fre"},
    ) == {"fr"}
    assert calls == []


def test_known_item_id_loads_read_only_local_catalog_once():
    window, calls = _window([_row("paris", "book", {"language": "en"})])
    assert known_item_languages(window, "paris", "book") == {"en"}
    assert calls == ["catalog"]


def test_book_with_same_item_id_other_provider_does_not_leak_languages():
    window, calls = _window([
        _row("paris", "same", {"language": "fr"}),
        _row("kobo", "same", {"language": "nl"}),
    ])
    assert known_item_languages(window, "paris", "same") == {"fr"}
    assert known_item_languages(window, "kobo", "same") == {"nl"}
    assert calls == ["catalog", "catalog"]


def test_item_id_is_exact_not_prefix_or_casefold_match():
    window, calls = _window([
        _row("paris", "book", {"language": "fr"}),
        _row("paris", "book-2", {"language": "de"}),
        _row("paris", "Book", {"language": "it"}),
    ])
    assert known_item_languages(window, "paris", "book") == {"fr"}


def test_source_metadata_and_matching_catalog_languages_are_unioned():
    window, calls = _window([
        _row("paris", "book", {"languages": ["fr", "en"]}),
        _row("paris", "book", {"language": "de"}),
    ])
    assert known_item_languages(
        window, "paris", "book", {"language": "es"},
    ) == {"fr", "en", "de", "es"}


@pytest.mark.parametrize("value,expected", [
    ("fra", "fr"),
    ("ENG", "en"),
    ("deu", "de"),
    ("fr_FR", "fr-FR"),
    ("pt-br", "pt-BR"),
    ("zh-Hant", "zh-Hant"),
])
def test_normalization_reuses_one_canonical_language_tag(value, expected):
    window, calls = _window()
    assert known_item_languages(
        window, "", None, {"language": value},
    ) == {expected}
    assert calls == []


def test_malformed_language_tags_are_ignored_and_not_invented():
    window, calls = _window([
        _row("paris", "book", {"languages": ["", None, "1234", "--"]}),
    ])
    assert known_item_languages(
        window, "paris", "book", {"languages": ["", "french language", "xx yy"]},
    ) == set()


def test_languages_prefer_explicit_nonempty_array_over_singular_fallback():
    window, calls = _window()
    assert known_item_languages(
        window, "paris", None, {"languages": ["de"], "language": "fr"},
    ) == {"de"}


def test_empty_explicit_array_falls_back_to_singular_language():
    window, calls = _window()
    assert known_item_languages(
        window, "paris", None, {"languages": [], "language": "en"},
    ) == {"en"}


def test_empty_or_missing_metadata_is_neutral():
    window, calls = _window([])
    assert known_item_languages(window, "", None) == set()
    assert known_item_languages(window, "", None, {}) == set()
    assert calls == []


def test_multiple_catalog_matches_union_and_deduplicate_exact_tags():
    window, calls = _window([
        _row("paris", "book", {"languages": ["fr", "en"]}),
        _row("paris", "book", {"languages": ("fra", "en-US")}),
    ])
    assert known_item_languages(window, "paris", "book") == {
        "fr", "en", "en-US",
    }


def test_string_metadata_value_is_one_language_not_character_sequence():
    window, calls = _window([])
    assert known_item_languages(window, "", None, {"languages": "fre"}) == {"fr"}


def test_only_matching_rows_are_read_for_language_metadata():
    class MalformedMetadata:
        def __iter__(self):
            raise AssertionError("never materialize private other-provider metadata")
    window, calls = _window([
        _row("other", "book", MalformedMetadata()),
        _row("paris", "elsewhere", MalformedMetadata()),
        _row("paris", "book", {"language": "fr"}),
    ])
    assert known_item_languages(window, "paris", "book") == {"fr"}


def test_catalogue_values_not_mutated_by_read_projection():
    source = {"languages": ["fr", "en"]}
    catalog = {"language": "de"}
    window, calls = _window([_row("paris", "book", catalog)])
    result = known_item_languages(window, "paris", "book", source)
    assert result == {"fr", "en", "de"}
    assert source == {"languages": ["fr", "en"]}
    assert catalog == {"language": "de"}
