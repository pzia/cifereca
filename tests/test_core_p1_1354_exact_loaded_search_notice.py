"""P1 #1354: exact Core-owned Search row, never a plugin-supplied entitlement."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.exact_search_notice_navigation import (
    can_open_loaded_search_notice, open_loaded_search_notice,
)


def _row(*, provider="kobo", credential="account-1", record="record-1",
         docbase="BOOKS", collection="public", result_id="item-1",
         stale=False):
    return NS(
        source_provider=provider, source_credential=credential,
        record_id=record, docbase=docbase, source_collection=collection,
        id=result_id, metadata={
            "search_provenance": "known_catalog_stale" if stale else "remote"
        },
    )


def _window(*rows):
    events = []
    state = NS(text="édition", field="title", scope="all")
    identity = NS(identity_id="reader-1")
    store = NS(
        get_n_items=lambda: len(visible),
        get_item=lambda index: NS(key=visible[index]),
    )
    objects = {f"{row.source_provider}:{row.id}": row for row in rows}
    visible = list(objects)
    page = NS(
        store=store,
        select_key=lambda key: events.append(("select", key)) or key in visible,
    )
    window = NS(
        _closed=False, _search_mode="global",
        _loaded_search_context=("édition", "title", "all", "reader-1"),
        search_controls=NS(state=lambda: state),
        service=NS(current_identity=identity, searchable_providers=lambda: [
            ("kobo", "Kobo"), ("paris", "Paris"),
        ]),
        _remote_search_results=list(rows),
        search_objects=objects, search_page=page,
        navigate=lambda route: events.append(("navigate", route)),
    )
    return window, events, visible, state, identity


def _coords(row=None, *, credential="account-1", record="record-1"):
    return dict(
        provider="kobo", credential=credential, record_id=record,
        docbase="BOOKS", source_collection="public", identity_id="reader-1",
    )


def test_exact_loaded_notice_uses_the_existing_search_selection_only_on_click():
    row = _row()
    window, events, visible, state, identity = _window(row)
    assert can_open_loaded_search_notice(window, **_coords())
    assert events == []
    assert open_loaded_search_notice(window, **_coords())
    assert events == [("navigate", "search"), ("select", "kobo:item-1")]
    assert not any(event[0] == "run_search" for event in events)


def test_same_provider_multiple_accounts_and_editions_never_merge():
    first = _row()
    second = _row(credential="account-2", record="record-2", result_id="item-2")
    window, events, *_ = _window(first, second)
    assert open_loaded_search_notice(window, **_coords(credential="account-2", record="record-2"))
    assert events[-1] == ("select", "kobo:item-2")
    assert not can_open_loaded_search_notice(
        window, **_coords(credential="account-2", record="record-1")
    )
    assert not can_open_loaded_search_notice(
        window, **{**_coords(), "docbase": "OTHER_EDITION"}
    )


@pytest.mark.parametrize("collection", [None, ""])
def test_optional_collection_still_selects_the_exact_loaded_notice(collection):
    row = _row(collection=collection)
    other = _row(collection="public", result_id="item-2")
    window, events, *_ = _window(row, other)
    coordinates = {**_coords(), "source_collection": ""}
    assert can_open_loaded_search_notice(window, **coordinates)
    assert events == []
    assert open_loaded_search_notice(window, **coordinates)
    assert events == [("navigate", "search"), ("select", "kobo:item-1")]
    # Empty is never a wildcard for the populated collection on the other row.
    assert open_loaded_search_notice(window, **_coords())
    assert events[-1] == ("select", "kobo:item-2")


def test_empty_collection_does_not_select_a_different_collection_or_account():
    row = _row()
    window, events, *_ = _window(row)
    assert not open_loaded_search_notice(
        window, **{**_coords(), "source_collection": ""}
    )
    row.source_collection = None
    assert not open_loaded_search_notice(window, **_coords())
    assert not open_loaded_search_notice(
        window, **{**_coords(credential="account-2"), "source_collection": ""}
    )
    assert events == []


def test_same_coordinates_duplicated_or_colliding_row_keys_fail_closed():
    first = _row()
    duplicate = _row(result_id="item-2")
    window, events, *_ = _window(first, duplicate)
    assert not can_open_loaded_search_notice(window, **_coords())
    assert not open_loaded_search_notice(window, **_coords())
    assert events == []
    # A colliding rendered key is also ambiguous even when coordinates vary.
    other = _row(credential="account-2", record="record-2")
    window, events, *_ = _window(first, other)
    assert not can_open_loaded_search_notice(window, **_coords())
    assert events == []


def test_absent_hidden_stale_disabled_identity_changed_or_pending_fail_closed():
    row = _row()
    window, events, visible, state, identity = _window(row)
    visible.clear()
    assert not can_open_loaded_search_notice(window, **_coords())
    visible.append("kobo:item-1")

    window._remote_search_results[0].metadata["search_provenance"] = "known_catalog_stale"
    assert not can_open_loaded_search_notice(window, **_coords())
    row.metadata["search_provenance"] = "remote"

    window.service.searchable_providers = lambda: [("paris", "Paris")]
    assert not can_open_loaded_search_notice(window, **_coords())
    window.service.searchable_providers = lambda: [("kobo", "Kobo")]

    identity.identity_id = "reader-2"
    assert not can_open_loaded_search_notice(window, **_coords())
    identity.identity_id = "reader-1"
    state.text = "different"
    assert not can_open_loaded_search_notice(window, **_coords())
    state.text = "édition"

    window._loaded_search_context = None  # a new Search request is pending
    assert not can_open_loaded_search_notice(window, **_coords())
    window._loaded_search_context = ("édition", "title", "all", "reader-1")

    window._closed = True
    assert not open_loaded_search_notice(window, **_coords())
    assert events == []


def test_invalid_coordinates_cannot_navigate_or_echo_sensitive_data():
    row = _row()
    window, events, *_ = _window(row)
    for malformed in (
        {**_coords(), "credential": ""},
        {**_coords(), "identity_id": "reader-2"},
        {**_coords(), "record_id": None},
        {**_coords(), "provider": 1},
        {**_coords(), "record_id": "x" * 513},
        {**_coords(), "source_collection": "edition-other"},
        {**_coords(), "source_collection": None},
        {**_coords(), "source_collection": "x" * 513},
    ):
        assert not open_loaded_search_notice(window, **malformed)
    assert events == []


def test_identity_change_during_navigation_revokes_selection():
    row = _row()
    window, events, _, _, identity = _window(row)
    def navigate(route):
        events.append(("navigate", route))
        identity.identity_id = "reader-2"
    window.navigate = navigate
    assert not open_loaded_search_notice(window, **_coords())
    assert events == [("navigate", "search")]


def test_search_scope_stamp_binds_identity_query_and_local_only():
    # Compile the GTK-free adapter function in isolation: Core CI runs
    # without PyGObject, while the real GTK checks run separately.
    import ast
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/search_results.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body
              if isinstance(node, ast.FunctionDef)
              and node.name == "_populate_search")
    namespace = {}
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[fn], type_ignores=[])), str(source), "exec"),
         namespace)
    populate = namespace["_populate_search"]

    window, _, _, state, identity = _window(_row())
    def populate_remote():
        # No GTK; render and selection callbacks only record their arguments.
        window._render_search_results = lambda: 1
        window._sync_search_detail_after_results = lambda: None
        window.service.last_search_warnings = []
        window.service.last_search = []
        populate(window, list(window._remote_search_results),
                 announce=False)
    window._loaded_search_context = None
    window._online_source_context = (
        state.text, state.field, state.scope, identity.identity_id
    )
    populate_remote()
    assert can_open_loaded_search_notice(window, **_coords())

    window._online_source_context = ("other", "title", "all", "reader-1")
    populate_remote()
    assert window._loaded_search_context is None
    assert not can_open_loaded_search_notice(window, **_coords())
