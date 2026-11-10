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


def test_foreign_global_library_removes_private_cover_and_raw_session_metadata():
    private = "https://example.org/loan/cover.jpg?X-Amz-Signature=PRIVATE"
    alice = {
        "key": "artifact:alice", "identity_id": "alice",
        "title": "Bibliographie partagée",
        "author": "Autrice A",
        "state": "remote_loan_active",
        "thumbnail_url": private,
        "download_url": "https://example.org/download?token=SECRET",
        "possessions": [{"identity_id": "alice", "kind": "loan",
                         "provider_id": "paris"}],
        "metadata": {
            "description": "Ouvrage sur l'histoire",
            "subjects": ["histoire", "archives"],
            "token": "SESSION-SECRET",
            "private_response": {"cookie": "SECRET"},
            "cover_resolution": {
                "status": "selected", "tier": "origin",
                "source": "loan", "url": private,
            },
            "epub_metadata": {
                "authors": ["Autrice A"], "token": "SECRET",
            },
        },
    }
    original_metadata = dict(alice["metadata"])
    foreign, = global_library_display_rows(
        [{"identity_id": "alice", "entry": alice}],
        viewing_identity_id="bob",
    )
    assert foreign["identity_id"] == "alice"
    assert foreign["title"] == "Bibliographie partagée"
    assert foreign["thumbnail_url"] is None
    assert "download_url" not in foreign
    assert foreign["possessions"] == alice["possessions"]
    assert foreign["metadata"] == {
        "description": "Ouvrage sur l'histoire",
        "subjects": ["histoire", "archives"],
        "epub_metadata": {"authors": ["Autrice A"]},
    }
    assert alice["thumbnail_url"] == private
    assert alice["metadata"] == original_metadata

    # An owner's active-profile view remains fully unchanged.
    own, = global_library_display_rows(
        [{"identity_id": "alice", "entry": alice}],
        viewing_identity_id="alice",
    )
    assert own["thumbnail_url"] == private
    assert own["download_url"] == alice["download_url"]
    assert own["metadata"] is alice["metadata"]


def test_foreign_global_library_preserves_only_proven_public_catalogue_cover():
    url = "https://covers.example.org/catalogue/book.jpg"
    book = {
        "key": "artifact:alice", "identity_id": "alice",
        "title": "Ouvrage", "thumbnail_url": url,
        "metadata": {
            "language": "fr", "genres": ["Histoire"],
            "cover_resolution": {
                "status": "selected", "url": url,
                "tier": "catalog", "source": "catalogue",
            },
        },
    }
    def foreign(row):
        return global_library_display_rows(
            [{"identity_id": "alice", "entry": row}],
            viewing_identity_id="bob",
        )[0]

    projected = foreign(book)
    assert projected["thumbnail_url"] == url
    assert projected["metadata"] == {
        "language": "fr", "genres": ["Histoire"],
    }

    signed = url + "?Key-Pair-Id=private"
    assert foreign({
        **book, "thumbnail_url": signed, "metadata": {
            **book["metadata"], "cover_resolution": {
                "status": "selected", "url": signed, "tier": "catalog",
            },
        },
    })["thumbnail_url"] is None
    assert foreign({
        **book, "metadata": {
            **book["metadata"], "cover_resolution": {
                "status": "selected", "url": url, "tier": "origin",
                "source": "entitlement",
            },
        },
    })["thumbnail_url"] is None
    assert foreign({
        **book, "thumbnail_url": "file:///home/other/secret.png",
        "metadata": {
            "cover_resolution": {
                "status": "selected", "url": "file:///home/other/secret.png",
                "tier": "embedded", "source": "epub",
            },
        },
    })["thumbnail_url"] is None


def test_foreign_metadata_malformed_and_urls_fail_closed_not_crash():
    problematic = [
        {"token": "bearer SECRET", "description": "https://auth.invalid/x"},
        {"language": ["fr", "https://private.invalid/secret"]},
        {"epub_metadata": {"authors": ["A", "Bearer PRIVATE"]}},
        {"isbn": {"url": "https://provider.invalid/token"}},
        ["not a mapping"],
        None,
    ]
    for metadata in problematic:
        item = {
            "key": "local:1", "identity_id": "alice",
            "title": "Ouvrage", "metadata": metadata,
        }
        rows = global_library_display_rows(
            [{"identity_id": "alice", "entry": item}],
            viewing_identity_id="bob",
        )
        assert len(rows) == 1
        assert rows[0]["metadata"] == {}
