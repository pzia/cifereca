"""#1129: headless regression contracts for the extracted Recent projector."""
from __future__ import annotations

from cifereca.gui.recent_rows import project_recent_rows


def _row(*values):
    return values


def test_recent_rows_preserve_display_order_provenance_and_missing_values():
    items = [
        {"placement_id": "p-kobo", "provider_id": "kobo",
         "title": "Premier", "author": "Auteur", "acquired_at": None,
         "thumbnail_url": "https://example.invalid/a.jpg"},
        {"provider_id": "local", "title": "", "author": None,
         "acquired_at": "not-a-date"},
    ]
    original = [dict(item) for item in items]
    rows, lookup = project_recent_rows(items, row_factory=_row)

    assert [row[0] for row in rows] == ["p-kobo", "recent:1"]
    assert rows[0] == (
        "p-kobo", "Premier", "Auteur", "Date inconnue",
        "Kobo", "https://example.invalid/a.jpg",
    )
    assert rows[1] == (
        "recent:1", "Sans titre", "", "Date inconnue", "Local", None,
    )
    assert lookup == {"p-kobo": items[0], "recent:1": items[1]}
    assert lookup["p-kobo"] is items[0]
    assert items == original


def test_recent_duplicate_placement_keeps_rows_but_last_record_in_lookup():
    items = [
        {"placement_id": "shared", "title": "ancienne"},
        {"placement_id": "shared", "title": "nouvelle"},
    ]
    rows, lookup = project_recent_rows(items, row_factory=_row)
    assert [row[1] for row in rows] == ["ancienne", "nouvelle"]
    assert len(lookup) == 1
    assert lookup["shared"] is items[1]


def test_recent_projection_is_pure_for_empty_and_one_pass_iterable():
    calls = []
    def factory(*fields):
        calls.append(fields)
        return tuple(fields)
    rows, lookup = project_recent_rows(iter(()), row_factory=factory)
    assert rows == [] and lookup == {} and calls == []

    iterable = iter([{"placement_id": "one", "provider_id": "paris"}])
    rows, lookup = project_recent_rows(iterable, row_factory=factory)
    assert rows[0][4] == "Paris"
    assert lookup["one"]["provider_id"] == "paris"
    assert len(calls) == 1
