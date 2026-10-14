"""Pure regression contract for recent acquisitions (P0 #1129)."""

from cifereca.gui.recent_rows import project_recent_rows


def _factory(*fields):
    return fields


def test_empty_recents_has_no_rows_or_lookup():
    assert project_recent_rows([], row_factory=_factory) == ([], {})


def test_duplicate_placement_keeps_both_rows_and_last_lookup_value():
    first = {"placement_id": "same", "title": "Premier", "provider_id": "kobo"}
    second = {"placement_id": "same", "title": "Second", "provider_id": "local"}
    rows, lookup = project_recent_rows([first, second], row_factory=_factory)
    assert len(rows) == 2
    assert [row[1] for row in rows] == ["Premier", "Second"]
    assert rows[0][4] == "Kobo"
    assert rows[1][4] == "Local"
    assert lookup == {"same": second}


def test_missing_placement_generates_stable_index_keys_without_mutation():
    items = [{"title": "A"}, {"title": "B", "placement_id": ""}]
    snapshot = [dict(item) for item in items]
    rows, lookup = project_recent_rows(items, row_factory=_factory)
    assert [row[0] for row in rows] == ["recent:0", "recent:1"]
    assert list(lookup) == ["recent:0", "recent:1"]
    assert items == snapshot


def test_missing_metadata_uses_user_visible_defaults():
    rows, _ = project_recent_rows([{}], row_factory=_factory)
    assert rows[0][1] == "Sans titre"
    assert rows[0][2] == ""
    assert rows[0][4] == "Local"
    assert rows[0][5] is None
