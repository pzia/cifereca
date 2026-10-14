"""P0 #1129: one catalog read per History filter, with exact provider scope."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.history_language_projection import (
    metadata_languages, item_languages, history_rows_with_languages,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "history_language_projection.py"


def _entry(provider, item, **metadata):
    return {"provider_id": provider, "item_id": item, "metadata": metadata}


def _history(id, provider, record=None):
    return NS(id=id, source_provider=provider, record_id=record)


def _window_for_history(rows, entries, selected=("fr",), page_size=2):
    events = []
    reads = []

    def catalog():
        reads.append("catalog")
        return list(entries)

    host = NS(
        service=NS(storage=NS(catalog_entries=catalog)),
        history_languages=NS(values=lambda: list(selected)),
        history_page=NS(
            show_rows=lambda values, *, total_count: events.append(
                ("shown", [row.id for row in values], total_count)
            )
        ),
        _history_page_size=page_size,
        _history_all_rows=[],
        _history_rows=[],
        _history_loaded_count=0,
        _history_append_scheduled=False,
        _history_book_row=lambda item: item,
        history_objects={},
        diagnostics=NS(add=lambda *args, **kwargs: events.append(
            ("diagnostic", args, kwargs)
        )),
    )
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_populate_history")
    module = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(
            name="annotations")], level=0),
        method,
    ], type_ignores=[]))
    from cifereca.gui import history_language_projection, secondary_results_controller
    namespace = {
        "history_language_projection": history_language_projection,
        "secondary_results_controller": secondary_results_controller,
    }
    exec(compile(module, str(MAIN), "exec"), namespace)
    populate = namespace["_populate_history"]
    return host, events, reads, lambda values, **kwargs: populate(host, values, **kwargs)


def test_owner_is_headless_and_mainwindow_public_language_lookup_signature_unchanged():
    owner = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(owner)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in item or "main_window" in item for item in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in main.body
               if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    known = next(n for n in cls.body
                 if isinstance(n, ast.FunctionDef) and n.name == "_known_item_languages")
    assert [arg.arg for arg in known.args.args] == [
        "self", "provider_id", "item_id", "metadata",
    ]
    assert "history_language_projection.item_languages" in ast.unparse(known)
    populate = next(n for n in cls.body
                    if isinstance(n, ast.FunctionDef) and n.name == "_populate_history")
    source = ast.unparse(populate)
    assert "secondary_results_controller.populate_history" in source
    controller = ast.parse(
        (ROOT / "secondary_results_controller.py").read_text(encoding="utf-8")
    )
    controller_source = ast.unparse(controller)
    assert "history_language_projection.history_rows_with_languages" in controller_source
    assert "window.history_page.show_rows(" in controller_source


def test_metadata_language_aliases_and_country_variants_are_preserved():
    assert metadata_languages({"languages": ["fra", "eng", "pt_BR", "invalid-value-!!!"]}) == {
        "fr", "en", "pt-BR",
    }


def test_singular_fallback_when_plural_is_empty_or_missing():
    assert metadata_languages({"languages": [], "language": "deu"}) == {"de"}
    assert metadata_languages({"language": "nl"}) == {"nl"}
    assert metadata_languages({}) == set()


def test_plural_language_takes_precedence_over_singular_metadata():
    assert metadata_languages({"languages": ["fr"], "language": "de"}) == {"fr"}


def test_scalar_and_set_values_are_supported_without_iteration_of_string_characters():
    assert metadata_languages({"languages": "fr"}) == {"fr"}
    assert metadata_languages({"languages": {"en", "es"}}) == {"en", "es"}
    assert metadata_languages({"languages": ("it", "de")}) == {"it", "de"}


def test_single_item_merges_direct_metadata_and_only_same_provider_and_item_catalog_rows():
    entries = [
        _entry("paris", "42", language="eng"),
        _entry("paris", "42", languages=["deu"]),
        _entry("gallica", "42", language="it"),
        _entry("paris", "other", language="es"),
    ]
    result = item_languages(
        "paris", "42", {"language": "fr"}, catalog_rows=entries,
    )
    assert result == {"fr", "en", "de"}


def test_single_item_without_id_never_uses_catalogue_rows():
    assert item_languages(
        "paris", None, {"language": "fr"},
        catalog_rows=[_entry("paris", "", language="en")],
    ) == {"fr"}


def test_provider_id_is_not_inferred_from_homonymous_catalogue_item():
    entries = [_entry("paris", "1", language="fr")]
    assert item_languages("gallica", "1", catalog_rows=entries) == set()


def test_history_projection_keeps_original_order_and_provider_specific_languages():
    entries = [
        _entry("paris", "same", language="fr"),
        _entry("gallica", "same", language="en"),
        _entry("paris", "other", language="fr"),
    ]
    history = [
        _history("h1", "paris", "same"),
        _history("h2", "gallica", "same"),
        _history("h3", "paris", "other"),
    ]
    assert history_rows_with_languages(history, {"fr"}, entries) == [
        history[0], history[2],
    ]
    assert history_rows_with_languages(history, {"en"}, entries) == [history[1]]


def test_history_projection_falls_back_to_history_id_when_record_absent():
    item = _history("id-fallback", "paris")
    assert history_rows_with_languages(
        [item], {"fr"}, [_entry("paris", "id-fallback", language="fr")],
    ) == [item]


def test_history_projection_unions_duplicate_catalogue_rows_for_same_key():
    item = _history("history", "paris", "item")
    assert history_rows_with_languages(
        [item], {"de"}, [
            _entry("paris", "item", language="fr"),
            _entry("paris", "item", language="de"),
        ],
    ) == [item]


def test_no_filter_preserves_all_history_rows_even_without_catalog_entries():
    history = [_history("h1", "paris"), _history("h2", "gallica")]
    assert history_rows_with_languages(history, set(), iter(())) == history


def test_invalid_or_unknown_language_cannot_gain_false_match():
    assert history_rows_with_languages(
        [_history("h", "paris", "item")], {"fr"},
        [_entry("paris", "item", language="not_an_iso_language_tag")],
    ) == []


def test_real_history_populate_reads_catalogue_once_for_large_filtered_batch():
    total = 563
    items = [_history(f"h-{i}", "paris", f"item-{i}") for i in range(total)]
    entries = [
        _entry("paris", f"item-{i}", language="fr" if i % 2 == 0 else "en")
        for i in range(total)
    ]
    window, events, reads, populate = _window_for_history(
        items, entries, page_size=23,
    )
    populate(items)
    expected_count = (total + 1) // 2
    assert reads == ["catalog"]
    assert len(window._history_rows) == expected_count
    assert len(window._history_all_rows) == total
    assert window._history_loaded_count == 23
    assert events[0] == ("shown", [f"h-{i}" for i in range(0, 46, 2)], expected_count)
    assert events[1][0] == "diagnostic"
    assert events[1][2]["detail"] == {
        "loaded": 23, "total": expected_count, "page_size": 23,
    }
    assert list(window.history_objects) == [f"h-{i}" for i in range(0, total, 2)]


def test_real_history_populate_without_language_filter_does_not_read_catalogue():
    items = [_history("h1", "paris"), _history("h2", "gallica")]
    window, events, reads, populate = _window_for_history(
        items, [_entry("paris", "h1", language="fr")],
        selected=(), page_size=10,
    )
    populate(items)
    assert reads == []
    assert events == [("shown", ["h1", "h2"], 2)]
    assert len(window._history_all_rows) == 2
    assert window._history_loaded_count == 2


def test_history_reapply_facet_does_not_mutate_original_snapshot_or_duplicate_reads():
    items = [_history("h1", "paris", "a"), _history("h2", "paris", "b")]
    entries = [
        _entry("paris", "a", language="fr"),
        _entry("paris", "b", language="en"),
    ]
    window, events, reads, populate = _window_for_history(items, entries)
    populate(items)
    assert len(window._history_all_rows) == 2
    populate(list(window._history_all_rows), preserve_all=True)
    assert reads == ["catalog", "catalog"]
    assert len(window._history_all_rows) == 2
    assert list(window.history_objects) == ["h1"]
    assert window._history_loaded_count == 1
    assert events == [
        ("shown", ["h1"], 1),
        ("shown", ["h1"], 1),
    ]


def test_empty_history_with_active_filter_never_reads_catalogue():
    window, events, reads, populate = _window_for_history(
        [], [_entry("paris", "x", language="fr")],
    )
    populate([])
    assert reads == []
    assert events == [("shown", [], 0)]


def test_history_language_filter_does_not_infer_missing_catalogue_metadata():
    window, events, reads, populate = _window_for_history(
        [_history("h", "paris", "x")],
        [_entry("paris", "x", title="French book")],
    )
    populate([_history("h", "paris", "x")])
    assert reads == ["catalog"]
    assert events == [("shown", [], 0)]
