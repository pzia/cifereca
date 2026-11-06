"""#4664: same Search detail/actions inside Recommendations, no fake rights.

The host gets only exact source coordinates and a selection lease. Fixtures
exercise the actual Core port without GTK or network; no provider POST.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import recommendation_exact_detail as port
from cifereca.models import ItemDetail, SearchResult
from cifereca.plugin_api import Cancelled


def _source():
    return SearchResult(
        id="search-1", record_id="record-1", docbase="EBOOKS",
        medium="digital", title="Titre exact", author=None,
        type=None, format="epub", publisher=None, published_at=None,
        description=None, thumbnail_url=None, friendly_url=None,
        scenario="EBOOKS", source_provider="paris",
        source_credential="card-A", source_collection="collection-1",
    )


def _detail():
    return ItemDetail(
        record_id="record-1", docbase="EBOOKS", title="Titre exact",
        author=None, type=None, year=None, thumbnail_url=None,
        availability_status="available",
        availability_label="Selon le catalogue", holdings=[],
        source_provider="paris", source_credential="card-A",
    )


def _fixture(monkeypatch):
    item, detail = _source(), _detail()
    token = object()
    calls = []
    outputs = []
    scheduled = []
    session = NS(provider_id="paris", credential_id="card-A", connected=True)
    storage = NS(
        identity_for_connection=lambda provider, credential: "alice",
        provider_connections=lambda **kwargs: [{
            "identity_id": "alice", "provider_id": "paris",
            "credential_id": "card-A", "enabled": True,
        }],
    )
    service = NS(
        current_identity=NS(identity_id="alice"), storage=storage,
        _session_for_source=lambda *args: session,
        holdings=lambda *args, **kw: calls.append((args, kw)) or detail,
    )
    def submit(*args, **kwargs):
        scheduled.append((args, kwargs))
    window = NS(
        service=service, _closed=False, _search_generation=3,
        _identity_context_generation=2, _shell_page_rebuild_token=token,
        _last_search_query="Titre exact",
        search_objects={"paris:search-1": item},
        runner=NS(submit=submit), toast=lambda *args: None,
    )
    mapped = [True]
    view = NS(
        _window=window, widget=NS(get_mapped=lambda: mapped[0]),
        show_existing_search_detail=lambda *a, **k: outputs.append((a, k)),
    )
    selection = [True]
    loaded = [True]
    monkeypatch.setattr(
        port, "loaded_notice_key",
        lambda win, **coords: "paris:search-1" if loaded[0] else None,
    )
    kwargs = dict(
        view=view, context_sections=(), is_current=lambda: selection[0],
        provider="paris", credential="card-A",
        record_id="record-1", docbase="EBOOKS",
        source_collection="collection-1", identity_id="alice",
    )
    return NS(window=window, service=service, session=session, storage=storage,
              item=item, detail=detail, calls=calls, outputs=outputs,
              scheduled=scheduled, selection=selection, loaded=loaded,
              mapped=mapped, kwargs=kwargs)


def _start(f):
    assert port.present_loaded_search_detail_in_place(f.window, **f.kwargs)
    assert len(f.scheduled) == 1
    args, kwargs = f.scheduled[0]
    assert args[0].startswith("Consultation")
    assert kwargs["kind"] == "holdings" and kwargs["origin"] == "user"
    assert f.calls == []  # no GET before executing the explicit task
    return args[1], args[2], args[3]


def test_explicit_click_uses_core_holdings_and_same_in_place_renderer(monkeypatch):
    f = _fixture(monkeypatch)
    work, success, failure = _start(f)
    result = work()
    assert result is f.detail
    assert f.calls == [((f.item, "Titre exact"), {"force": False})]
    success(result)
    assert len(f.outputs) == 1
    assert f.outputs[0][0] == (f.item, f.detail)
    assert f.outputs[0][1]["context_sections"] == ()


def test_no_implicit_click_or_lost_plugin_selection(monkeypatch):
    f = _fixture(monkeypatch)
    f.selection[0] = False
    assert not port.present_loaded_search_detail_in_place(f.window, **f.kwargs)
    assert f.scheduled == [] and f.calls == []
    f.selection[0] = True
    work, success, failure = _start(f)
    f.selection[0] = False
    work()  # user already explicitly requested harmless metadata
    success(f.detail)
    assert f.outputs == []


@pytest.mark.parametrize("change", [
    lambda f: setattr(f.window, "_identity_context_generation", 3),
    lambda f: setattr(f.service, "current_identity", NS(identity_id="bob")),
    lambda f: setattr(f.window, "_search_generation", 4),
    lambda f: setattr(f.session, "connected", False),
    lambda f: setattr(f.service, "_session_for_source", lambda *_: object()),
    lambda f: setattr(f.storage, "identity_for_connection", lambda *_: "bob"),
    lambda f: setattr(f.storage, "provider_connections", lambda **_: []),
])
def test_changed_profile_source_or_enabled_binding_blocks_work_without_get(
    monkeypatch, change,
):
    f = _fixture(monkeypatch)
    work, success, failure = _start(f)
    change(f)
    with pytest.raises(Cancelled):
        work()
    assert f.calls == [] and f.outputs == []
    success(f.detail)
    assert f.outputs == []


def test_rebound_search_result_cannot_publish_stale_actions(monkeypatch):
    f = _fixture(monkeypatch)
    work, success, failure = _start(f)
    work()
    f.window.search_objects["paris:search-1"] = _source()
    success(f.detail)
    assert len(f.calls) == 1
    assert f.outputs == []


def test_hidden_or_ambiguous_route_cannot_schedule_or_publish(monkeypatch):
    f = _fixture(monkeypatch)
    f.loaded[0] = False
    assert not port.present_loaded_search_detail_in_place(f.window, **f.kwargs)
    assert f.scheduled == []
    f.loaded[0] = True
    work, success, failure = _start(f)
    f.loaded[0] = False
    success(work())
    assert f.outputs == []


def test_unrelated_or_foreign_panel_refused_without_side_effects(monkeypatch):
    f = _fixture(monkeypatch)
    fake = NS(_window=object(), show_existing_search_detail=lambda *_: None)
    for change in ({"view": fake}, {"context_sections": (None,) * 17},
                   {"is_current": None}):
        assert not port.present_loaded_search_detail_in_place(
            f.window, **{**f.kwargs, **change},
        )
    assert f.scheduled == [] and f.calls == []


def test_core_panel_method_calls_canonical_search_renderer():
    from pathlib import Path
    import ast
    path = (Path(__file__).resolve().parents[1] /
            "src/cifereca/gui/public_book_view.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef)
               and x.name == "PublicBookMasterDetailView")
    method = next(x for x in cls.body if isinstance(x, ast.FunctionDef)
                  and x.name == "show_existing_search_detail")
    body = ast.unparse(method)
    assert "panel.show_item_detail(item, detail)" in body
    assert "panel._group" in body
    assert "borrow_search_item" not in body
    assert "download_direct_item" not in body


def test_unmapped_plugin_page_cannot_start_or_publish_external_actions(monkeypatch):
    f = _fixture(monkeypatch)
    f.mapped[0] = False
    assert not port.present_loaded_search_detail_in_place(f.window, **f.kwargs)
    assert f.scheduled == [] and f.calls == []
    f.mapped[0] = True
    work, success, failure = _start(f)
    f.mapped[0] = False  # navigation or removal after explicit user click
    success(work())
    assert f.outputs == []


def test_attested_known_v2_mirror_is_fetched_only_after_explicit_click(monkeypatch):
    f = _fixture(monkeypatch)
    row = {"item_id": "record-1", "metadata": {
        "catalogue_scope_version": 2, "source_credential": "card-A",
        "docbase": "EBOOKS", "source_collection": "collection-1",
    }}
    records = [row]
    monkeypatch.setattr(
        port, "_known_item",
        lambda window, coords: (f.item, dict(records[0]))
        if records[0] is not None else None,
    )
    kwargs = {**f.kwargs, "known_source": True}
    assert port.present_loaded_search_detail_in_place(f.window, **kwargs)
    assert f.calls == [] and len(f.scheduled) == 1
    work, success, failure = (f.scheduled[0][0][1:4])
    assert work() is f.detail
    success(f.detail)
    assert len(f.outputs) == 1
    assert f.calls == [((f.item, "Titre exact"), {"force": False})]


def test_known_mirror_removed_or_revised_blocks_work_and_late_gtk(monkeypatch):
    f = _fixture(monkeypatch)
    mirror = [{"revision": "v1"}]
    monkeypatch.setattr(
        port, "_known_item",
        lambda window, coords: (f.item, dict(mirror[0]))
        if mirror[0] is not None else None,
    )
    kwargs = {**f.kwargs, "known_source": True}
    assert port.present_loaded_search_detail_in_place(f.window, **kwargs)
    work, success, failure = f.scheduled[0][0][1:4]
    mirror[0] = {"revision": "v2"}
    with pytest.raises(Cancelled):
        work()
    assert f.calls == []
    success(f.detail)
    assert f.outputs == []
    mirror[0] = None
    assert not port.present_loaded_search_detail_in_place(f.window, **kwargs)


def test_known_mirror_changed_after_worker_is_not_published(monkeypatch):
    f = _fixture(monkeypatch)
    mirror = [{"revision": "v1"}]
    monkeypatch.setattr(
        port, "_known_item",
        lambda window, coords: (f.item, dict(mirror[0]))
        if mirror[0] is not None else None,
    )
    kwargs = {**f.kwargs, "known_source": True}
    assert port.present_loaded_search_detail_in_place(f.window, **kwargs)
    work, success, failure = f.scheduled[0][0][1:4]
    work()
    mirror[0] = {"revision": "v2"}
    success(f.detail)
    assert f.outputs == []


def test_unknown_source_mode_is_not_truthy_fallback(monkeypatch):
    f = _fixture(monkeypatch)
    assert not port.present_loaded_search_detail_in_place(
        f.window, **{**f.kwargs, "known_source": 1},
    )
    assert f.calls == [] and f.scheduled == []
