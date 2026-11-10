"""#2385: all-profile Library is an explicit read view, not a rights ACL."""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.library_global_projection import global_library_display_rows
from cifereca.gui.library_rows import project_library_rows
from cifereca.gui.library_detail_controller import library_selected
from tests.test_core_ui_1129_library_page_controller import (
    _window, _gtk, _page,
)
from cifereca.gui.library_page_controller import build_library_page
from tests.test_core_ui_identity_async_recent import _harness


def test_global_projection_does_not_merge_identical_artifacts_from_a_and_b():
    a = {"key": "artifact:sha256:shared", "identity_id": "alice",
         "title": "Same book", "artifact_id": "sha256:shared",
         "placement_id": "a", "providers": ["local"]}
    b = {**a, "identity_id": "bob", "placement_id": "b"}
    entries = global_library_display_rows([
        {"identity_id": "alice", "entry": a},
        {"identity_id": "bob", "entry": b},
    ])
    assert len(entries) == 2
    assert [x["identity_id"] for x in entries] == ["alice", "bob"]
    assert entries[0]["key"] == entries[1]["key"]
    assert entries[0]["_library_ui_key"] != entries[1]["_library_ui_key"]
    rows, objects = project_library_rows(
        entries, row_factory=lambda *args: NS(key=args[0], meta=args[3]),
        provider_display_name=lambda x: x,
        availability_label=lambda row: "Local",
        backup_status_label=lambda x: x, format_date=lambda x: x,
    )
    assert len(rows) == len(objects) == 2
    assert "Profil : alice" in rows[0].meta
    assert "Profil : bob" in rows[1].meta
    assert objects[rows[0].key]["placement_id"] == "a"
    assert objects[rows[1].key]["placement_id"] == "b"


def test_ambiguous_same_owner_book_or_wrong_owner_is_not_selectable():
    row = {"key": "artifact:A", "identity_id": "alice", "title": "A"}
    assert global_library_display_rows([
        {"identity_id": "alice", "entry": row},
        {"identity_id": "alice", "entry": dict(row)},
        {"identity_id": "bob", "entry": row},
        {"identity_id": "bob", "entry": {**row, "identity_id": "bob"}},
    ]) == [{
        **row, "identity_id": "bob",
        "_library_ui_key": "global:3:bob:10:artifact:A",
        "_library_view_owner": "bob",
    }]


def test_global_mode_is_explicit_and_reads_core_without_scan_or_current_only():
    window, controls, events, states = _window()
    page = build_library_page(window, Gtk=_gtk(), MasterDetailPage=_page)
    assert window._library_all_profiles is False
    button = controls.primary.children[1]
    assert button.label == "Tous les profils"
    button.click()
    assert window._library_all_profiles is True
    assert events[-1][0] == "rescan"
    assert events[-1][1][0] is False
    button.click()
    assert window._library_all_profiles is False
    assert events[-1][0] == "rescan"


def test_refresh_global_mode_only_reads_core_projection_without_scans():
    host = _harness()
    host._library_all_profiles = True
    host.service.library_entries_all_profiles = Mock(return_value=[
        {"identity_id": "alice", "entry": {
            "key": "a", "identity_id": "alice", "title": "Alice",
        }},
        {"identity_id": "bob", "entry": {
            "key": "b", "identity_id": "bob", "title": "Bob",
        }},
    ])
    host.service.library_entries = Mock(side_effect=AssertionError(
        "global view must not read only current B",
    ))
    host.service.catalog_scan = Mock(side_effect=AssertionError(
        "global view must never rescan",
    ))
    host.refresh_library(True, control=object(), refresh_covers=True)
    request = host.runner.requests[-1]
    entries = request.work()
    assert [row["identity_id"] for row in entries] == ["alice", "bob"]
    host.service.library_entries_all_profiles.assert_called_once_with()
    host.service.library_entries.assert_not_called()
    host.service.catalog_scan.assert_not_called()


def test_foreign_book_does_not_load_provider_detail_or_supplier_actions(monkeypatch):
    from cifereca.gui import library_detail_controller
    monkeypatch.setattr(
        library_detail_controller, "local_book_detail_sections",
        lambda *_a, **_kw: [],
    )
    calls = []
    detail = NS(
        show_presentation=lambda *a, **kw: calls.append("presentation"),
        empty=lambda *a: calls.append("empty"),
    )
    item = {
        "key": "artifact:a", "identity_id": "alice",
        "_library_view_owner": "alice",
        "placement_id": "placed-a", "artifact_id": "sha256:a",
        "title": "Alice book", "source": "loan", "loan_id": "private-a",
    }
    svc = NS(
        current_identity=NS(identity_id="bob"),
        provider_display_name=lambda x: x,
        identity_by_id=lambda x: NS(label=x),
    )
    window = NS(
        _closed=False, service=svc, _cancel_cached_book_details=Mock(),
        _library_objects={"ui:a": item},
        library_page=NS(detail=detail), library_controls=object(),
        _book_description=lambda _row: "",
        _library_availability_label=lambda _row: "",
        _kobo_backup_status_label=lambda _x: "",
        _add_local_book_actions=lambda *_a: calls.append("copy-action"),
        _add_facets=lambda *_a: calls.append("facets"),
        _add_book_recommendation_action=lambda *_a: calls.append("recommend"),
        _add_plugin_book_actions=lambda *_a: calls.append("provider-action"),
        _request_cached_book_details=lambda *_a: calls.append("provider-GET"),
        loan_objects={},
    )
    library_selected(
        window, NS(key="ui:a"), Gtk=NS(Button=object),
        unique_library_loan=lambda *_a: (_ for _ in ()).throw(AssertionError("no loan")),
        book_detail_presentation=lambda **kwargs: (
            calls.append(("links", kwargs["links"])) or object()
        ),
        detail_language=lambda x: x,
        document_links=lambda *_a: (_ for _ in ()).throw(AssertionError("no private link")),
    )
    assert "copy-action" in calls and "presentation" in calls
    assert ("links", ()) in calls
    assert "provider-action" not in calls
    assert "provider-GET" not in calls
    assert "recommend" not in calls
