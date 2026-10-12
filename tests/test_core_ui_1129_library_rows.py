"""Headless behavior contracts for the #1129 Library row projector."""
from __future__ import annotations

from cifereca.gui.library_rows import project_library_rows


def _project(items, *, state=lambda row: ""):
    calls = []
    def display_name(provider):
        calls.append(("display", provider))
        return {"local": "Local", "kobo": "Kobo", "paris": "Paris"}.get(provider, provider)
    def backup(value):
        calls.append(("backup", value))
        return "À synchroniser"
    def date(value):
        calls.append(("date", value))
        return "12/10/2026"
    rows, objects = project_library_rows(
        items, row_factory=lambda *fields: fields,
        provider_display_name=display_name,
        availability_label=state,
        backup_status_label=backup,
        format_date=date,
    )
    return rows, objects, calls


def test_library_rows_keep_filtered_order_and_nonredundant_local_state():
    entries = [
        {"key": "local-1", "provider_id": "local", "state": "local",
         "title": "Livre", "author": "Auteur", "thumbnail_url": None},
        {"key": "paris-2", "provider_id": "paris", "title": "Prêt",
         "due_at": "2026-10-12", "state": "borrowed"},
    ]
    previous = [dict(entry) for entry in entries]
    rows, objects, calls = _project(
        entries,
        state=lambda item: "" if item["key"] == "local-1" else "Emprunté",
    )
    assert [row[0] for row in rows] == ["local-1", "paris-2"]
    assert rows[0][3] == ""  # Local is not redundantly repeated.
    assert rows[1][3] == "Emprunté · retour 12/10/2026"
    assert rows[0][4] == "Local" and rows[1][4] == "Paris"
    assert calls.count(("date", "2026-10-12")) == 1
    assert objects["local-1"] is entries[0]
    assert entries == previous


def test_library_rows_preserve_multi_source_and_kobo_device_labels():
    entries = [
        {"provider_id": "kobo", "providers": ["kobo", "paris"],
         "sync_status": "pending", "device_statuses": [
             {"observed_state": "present", "desired_state": "keep"},
             {"observed_state": "missing", "desired_state": "keep"},
         ]},
        {"provider_id": "kobo", "sync_status": "pending",
         "device_statuses": [{"desired_state": "keep"}]},
        {"provider_id": "kobo", "local": True, "sync_status": "pending"},
    ]
    rows, objects, calls = _project(entries)
    assert rows[0][4] == "2 sources"
    assert rows[0][3] == "À synchroniser · Kobo 1/2"
    assert rows[1][3] == "À synchroniser · Kobo à synchroniser"
    assert rows[2][3] == ""
    assert [row[0] for row in rows] == ["library:0", "library:1", "library:2"]
    assert sum(name == "backup" for name, _value in calls) == 2
    assert list(objects.values()) == entries


def test_library_rows_preserve_duplicate_keys_and_single_pass_inputs():
    first = {"key": "shared", "title": "ancienne"}
    last = {"key": "shared", "title": "nouvelle"}
    rows, objects, calls = _project(iter((first, last)))
    assert [row[1] for row in rows] == ["ancienne", "nouvelle"]
    assert objects["shared"] is last
    assert len(objects) == 1
    assert len(rows) == 2


def test_library_rows_do_not_fetch_or_mutate_when_empty():
    rows, objects, calls = _project(iter(()))
    assert rows == [] and objects == {} and calls == []
