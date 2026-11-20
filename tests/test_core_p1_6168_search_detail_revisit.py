"""P1 #6168: Search row and detail share the SAME already resolved N2 cover.

No GTK or provider I/O is required. The real Search callback is extracted
from the production source and executed with a fake DetailPanel. The image
fallback is presentation-only, source/epoch-scoped and does not re-publish
stale evidence as a fresh holdings response.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.search_cover_bridge import SearchCoverBridge
from cifereca.gui.search_detail_cover_reuse import (
    cached_exact_selected_search_cover,
    visual_search_detail, visual_search_item,
)
from tests.test_core_p1_3104_search_detail_cover_live_row import (
    COVER, detail, host, item, publisher,
)
from tests.test_core_p1_6168_known_search_cover_repaint import actual


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/search_details.py"
CONTEXT = (9, 3, 7, 4)


def with_decision(source=None):
    source = source or item()
    window, row = host(source)
    window.service.current_identity = NS(identity_id="alice")
    publisher()(window, source, detail(source))
    assert row.thumbnail_url == COVER
    assert isinstance(window._search_cover_bridge, SearchCoverBridge)
    return window, source, row


def test_reselect_exact_source_restores_previous_image_without_mutating_dto():
    window, source, row = with_decision()
    waiting = visual_search_item(window, source)
    without_cover = detail(source, url=None)
    loaded = visual_search_detail(window, source, without_cover)
    assert waiting is not source and waiting.thumbnail_url == COVER
    assert loaded is not without_cover and loaded.thumbnail_url == COVER
    assert source.thumbnail_url is None
    assert without_cover.thumbnail_url is None
    assert window.search_objects[row.key] is source
    # A fresh exact SourceKey DTO can replace the old model without hiding
    # the N2 cover or associating it to another credential.
    new_item = replace(source)
    window.search_objects[row.key] = new_item
    assert visual_search_item(window, source) is source
    assert visual_search_item(window, new_item).thumbnail_url == COVER


def test_new_provider_cover_wins_without_overwriting_n1_decision():
    window, source, _row = with_decision()
    fresh_url = "https://public.example.org/new-cover.jpg"
    latest = detail(source, url=fresh_url)
    assert visual_search_detail(window, source, latest) is latest
    with_image = replace(source, thumbnail_url=fresh_url)
    window.search_objects["provider:display-1"] = with_image
    assert visual_search_item(window, with_image) is with_image
    assert window._search_cover_bridge.selected_url(
        source, identity_id="alice", context=CONTEXT,
    ) == COVER


@pytest.mark.parametrize("source_change", [
    {"source_provider": "other"},
    {"source_credential": "card-b"},
    {"record_id": "other-edition"},
    {"docbase": "other-catalogue"},
    {"medium": "physical"},
    {"source_collection": "foreign-branch"},
    {"source_account_id": "foreign-account"},
    {"revision_id": "other-revision"},
])
def test_no_cover_from_another_notice_even_with_same_title_and_url(source_change):
    window, source, row = with_decision()
    other = replace(source, **source_change)
    window.search_objects[row.key] = other
    assert cached_exact_selected_search_cover(window, other) is None
    assert visual_search_item(window, other) is other
    no_picture = detail(other, url=None)
    assert visual_search_detail(window, other, no_picture) is no_picture
    assert no_picture.thumbnail_url is None


@pytest.mark.parametrize("change", [
    ("identity", "bob"), ("account", 8), ("source", 5),
    ("search", 4), ("generation", 10),
])
def test_account_aba_or_any_live_epoch_change_prevents_replay(change):
    window, source, _row = with_decision()
    field, value = change
    if field == "identity":
        window.service.current_identity.identity_id = value
    elif field == "account":
        window.service._account_context_epoch = value
    elif field == "source":
        window.service._source_context_epoch = value
    elif field == "search":
        window._search_generation = value
    else:
        window._identity_context_generation = value
    assert cached_exact_selected_search_cover(window, source) is None
    assert visual_search_item(window, source) is source
    original = detail(source, url=None)
    assert visual_search_detail(window, source, original) is original


def test_deselected_or_reused_row_key_does_not_inherit_other_image():
    window, source, row = with_decision()
    window.search_page.selection.get_selected_item = lambda: None
    assert cached_exact_selected_search_cover(window, source) is None
    window.search_page.selection.get_selected_item = lambda: row
    window.search_objects[row.key] = item(credential="other")
    assert visual_search_item(window, source) is source
    window.search_objects[row.key] = source
    assert visual_search_item(window, source).thumbnail_url == COVER


def test_known_v2_local_entry_uses_same_credential_and_catalogue_proof():
    window, _row, source, local = actual()
    publisher()(window, source, detail(source))
    assert cached_exact_selected_search_cover(window, source) == COVER
    assert visual_search_detail(window, source, detail(source, url=None)).thumbnail_url == COVER
    # An unqualified/stale known row must never borrow the previous image.
    for altered in (
        {"catalogue_scope_version": None},
        {"source_credential": "card-b"},
        {"medium": "physical"},
        {"search_provenance": "known_catalog_stale"},
    ):
        changed = dict(local)
        changed["metadata"] = {**local["metadata"], **altered}
        window._search_local_objects = {local["key"]: changed}
        assert cached_exact_selected_search_cover(window, source) is None
    # Same exact provider/edition after restoring the v2 proof.
    window._search_local_objects = {local["key"]: local}
    assert cached_exact_selected_search_cover(window, source) == COVER


def test_core_source_with_no_visual_cache_is_not_rewritten_or_fetched():
    source = item()
    window, _row = host(source)
    window.service.current_identity = NS(identity_id="alice")
    raw_detail = detail(source, url=None)
    assert cached_exact_selected_search_cover(window, source) is None
    assert visual_search_item(window, source) is source
    assert visual_search_detail(window, source, raw_detail) is raw_detail
    assert not hasattr(window, "_search_cover_bridge")


def actual_callback(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    ns = {
        "__name__": "cifereca.gui.search_details",
        "__package__": "cifereca.gui",
        "result_row": lambda candidate, **_kwargs: {"id": candidate.id},
    }
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")],
            level=0,
        ), function], type_ignores=[],
    )), str(SOURCE), "exec"), ns)
    return ns[name], ns


def _rendering_host(window, recorded):
    window._search_detail_context_current = lambda *_args: True
    window.search_page.detail = NS(
        show_item_detail=lambda source, detail_value:
            recorded.append(("detail", source, detail_value)),
        show_search=lambda source, **kwargs:
            recorded.append(("search", source, kwargs)),
    )
    window._add_facets = lambda *_args: recorded.append(("facets",))
    window._add_local_book_actions = lambda *_args: recorded.append(("actions",))
    window._request_cached_book_details = lambda *_args: recorded.append(("cached",))
    window.toast = lambda msg, *_args: recorded.append(("toast", msg))
    window.search_controls = None
    window.service._sessions = None
    return window


def test_real_apply_callback_uses_cached_visual_but_maintains_raw_proof_input():
    window, source, _row = with_decision()
    calls = []
    window = _rendering_host(window, calls)
    callback, globals_ = actual_callback("_apply_search_detail")
    globals_["_publish_search_detail_cover"] = (
        lambda owner, result, raw: calls.append(("publish", owner, result, raw))
    )
    raw = detail(source, url=None)
    callback(window, source.id, source, raw, context=(3, 9, 3, "alice"))
    assert [row[0] for row in calls] == [
        "publish", "detail", "facets", "actions", "cached",
    ]
    assert calls[0][3] is raw
    assert calls[1][1] is source
    assert calls[1][2] is not raw and calls[1][2].thumbnail_url == COVER
    assert raw.thumbnail_url is None and source.thumbnail_url is None


def test_actual_failure_callback_shows_same_known_cache_without_provider_retry():
    window, source, _row = with_decision()
    calls = []
    _rendering_host(window, calls)
    callback, _ = actual_callback("_search_detail_error")
    callback(window, source.id, source,
             RuntimeError("secret-token=NEVER_RENDER"), context=None)
    assert calls[0][0] == "search"
    assert calls[0][1] is not source
    assert calls[0][1].thumbnail_url == COVER
    assert calls[-1] == (
        "toast", "Disponibilités indisponibles. Réessayez ultérieurement.",
    )
    assert "NEVER_RENDER" not in repr(calls)


def test_production_initial_local_and_global_renderers_use_visual_decision():
    code = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(code)
    selected = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "_search_selected")
    apply = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name == "_apply_search_detail")
    assert "visual_thumbnail =" in ast.unparse(selected)
    assert "cached_exact_selected_search_cover(self, qualified)" in ast.unparse(selected)
    assert "visual_search_item(self, item)" in ast.unparse(selected)
    assert "visual_search_detail(self, item, detail)" in ast.unparse(apply)
    assert "_publish_search_detail_cover(self, item, detail)" in ast.unparse(apply)
