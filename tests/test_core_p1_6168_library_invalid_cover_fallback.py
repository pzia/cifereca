"""#6168: a rejected Library Core packet cannot silently paint a legacy URL."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui import library_detail_controller as controller
from tests.test_core_p1_6168_library_row_core_cover_binding import local, project


URL = "https://public.example.org/legacy-cover.png"


def test_library_list_packet_valid_invalid_and_legacy_compatibility(local):
    facts, entry = local
    valid, _ = project({**entry, "thumbnail_url": URL})
    assert valid._cover_document_ref == facts.scope
    assert valid.args[5] is None  # exact N2 read, not old URL

    for wrong in (
        {**entry, "identity_id": "bob", "thumbnail_url": URL},
        {**entry, "artifact_id": "sha256:" + "0" * 64, "thumbnail_url": URL},
        {**entry, "placement_id": "other", "thumbnail_url": URL},
        {**entry, "_library_view_owner": "bob", "thumbnail_url": URL},
        {**entry, "_exact_local_cover_evidence": object(), "thumbnail_url": URL},
    ):
        row, _ = project(wrong)
        assert not hasattr(row, "_cover_document_ref")
        assert row.args[5] is None
        assert row._public_row_content[6] is None

    legacy, _ = project({
        **entry, "_exact_local_cover_evidence": None, "thumbnail_url": URL,
    })
    assert not hasattr(legacy, "_cover_document_ref")
    assert legacy.args[5] == URL


def test_library_detail_rejects_url_downgrade_but_keeps_no_packet_legacy(local, monkeypatch):
    facts, entry = local
    monkeypatch.setattr(controller, "local_book_detail_sections", lambda *a, **k: ())
    observed = []
    detail = NS(
        show_presentation=lambda presentation, **k: observed.append(
            ("legacy", presentation["thumbnail"]),
        ),
        show_local_presentation=lambda presentation, **k: observed.append(
            ("n2", presentation["thumbnail"], k["cover_evidence"].scope),
        ),
    )
    window = NS(
        _closed=False, _cancel_cached_book_details=lambda: None,
        service=NS(current_identity=NS(identity_id="alice"),
                   provider_display_name=lambda _: "Local",
                   identity_by_id=lambda _: None),
        library_page=NS(detail=detail),
        _library_objects={}, _book_description=lambda _: "",
        _library_availability_label=lambda _: "Local",
        _kobo_backup_status_label=lambda _: "",
        _add_local_book_actions=lambda *a: None,
        _add_book_recommendation_action=lambda *a: None,
        _add_facets=lambda *a: None,
        _add_plugin_book_actions=lambda *a: None,
        _request_cached_book_details=lambda *a: None,
        library_controls=None,
    )

    def select(item):
        window._library_objects[item["key"]] = item
        controller.library_selected(
            window, NS(key=item["key"]), Gtk=None,
            unique_library_loan=lambda *a: None,
            book_detail_presentation=lambda **kwargs: kwargs,
            detail_language=str, document_links=lambda *_: (),
        )
        return observed.pop()

    assert select({**entry, "thumbnail_url": URL}) == ("n2", None, facts.scope)
    assert select({**entry, "identity_id": "bob", "thumbnail_url": URL}) == ("legacy", None)
    assert select({
        **entry, "_exact_local_cover_evidence": object(), "thumbnail_url": URL,
    }) == ("legacy", None)
    assert select({
        **entry, "_exact_local_cover_evidence": None, "thumbnail_url": URL,
    }) == ("legacy", URL)
