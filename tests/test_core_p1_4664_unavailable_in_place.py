"""#4664: exact Recommendation detail retains Search's safe fallback on outage.

All handlers are the actual Core view/port, exercised with no GTK, provider
requests, acquisition or private diagnostic disclosure.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.query_model import result_row
from cifereca.plugin_api import Cancelled, DetailField, DetailSection
from tests.test_core_p1_4664_recommendation_exact_detail import (
    _fixture, _source, _detail, _start,
)
from tests.test_core_p1_4664_in_place_local_actions import _adapter


class SensitiveProviderError(RuntimeError):
    def __str__(self):
        raise AssertionError("Never render or log raw upstream errors")


def _fallback_fixture(monkeypatch):
    f = _fixture(monkeypatch)
    events = []
    f.kwargs["view"].show_existing_search_unavailable = (
        lambda *args, **kw: events.append(("fallback", args, kw))
    )
    f.window.toast = lambda *args: events.append(("toast", args))
    return f, events


def test_real_exact_failure_publishes_fallback_only_after_explicit_click(monkeypatch):
    f, events = _fallback_fixture(monkeypatch)
    _, success, failure = _start(f)
    assert events == [] and f.calls == []
    failure(SensitiveProviderError("token=PRIVATE, file=/home/secret"))
    assert events == [
        ("fallback", (f.item,), {"context_sections": ()}),
        ("toast", ("Disponibilités indisponibles pour cette notice.", 5)),
    ]
    assert f.outputs == [] and f.calls == []


@pytest.mark.parametrize("change", [
    lambda f: setattr(f.service, "current_identity", NS(identity_id="bob")),
    lambda f: setattr(f.window, "_identity_context_generation", 8),
    lambda f: setattr(f.window, "_search_generation", 99),
    lambda f: setattr(f.session, "connected", False),
    lambda f: setattr(f.storage, "identity_for_connection", lambda *_: "bob"),
    lambda f: setattr(f.service, "_source_context_epoch", 1),
    lambda f: f.selection.__setitem__(0, False),
    lambda f: f.loaded.__setitem__(0, False),
    lambda f: f.mapped.__setitem__(0, False),
    lambda f: f.window.search_objects.__setitem__("paris:search-1", _source()),
])
def test_old_failure_cannot_replace_another_selection_or_source(
    monkeypatch, change,
):
    f, events = _fallback_fixture(monkeypatch)
    _, _, failure = _start(f)
    change(f)
    failure(SensitiveProviderError("token=PRIVATE"))
    assert events == [] and f.outputs == []


def test_changed_offer_metadata_revokes_fallback_even_with_same_source(monkeypatch):
    f, events = _fallback_fixture(monkeypatch)
    _, _, failure = _start(f)
    f.item.metadata["edition_revision"] = "other"
    failure(RuntimeError("unavailable"))
    assert events == []


def test_known_v2_revision_invalidates_fallback_after_worker(monkeypatch):
    from cifereca.gui import recommendation_exact_detail as port

    f, events = _fallback_fixture(monkeypatch)
    mirror = [{"metadata": {"edition_revision": "v1"}}]
    monkeypatch.setattr(
        port, "_known_item",
        lambda _window, _coords: (f.item, mirror[0]) if mirror[0] else None,
    )
    f.kwargs["known_source"] = True
    _, _, failure = _start(f)
    mirror[0]["metadata"]["edition_revision"] = "v2"
    failure(RuntimeError("unavailable"))
    assert events == []


def test_cancelled_worker_never_turns_into_an_availability_error(monkeypatch):
    f, events = _fallback_fixture(monkeypatch)
    _, _, failure = _start(f)
    failure(Cancelled("stale task"))
    assert events == []


def test_real_public_adapter_fallback_uses_same_core_search_and_context():
    view = _adapter()
    events = []
    panel = NS(
        show_search=lambda item, loading=False: events.append(("search", item, loading)),
        show_item_detail=lambda item, detail: events.append(("detail", item, detail)),
        _group=lambda title, rows, **kwargs: events.append(("context", title, rows)),
    )
    view._page = NS(detail=panel)
    view._window = NS(
        _add_local_book_actions=lambda panel, row: events.append(("actions", panel, row)),
    )
    item, detail = _source(), _detail()
    context = (DetailSection(
        "recommendation", "Recommandation",
        (DetailField("reason", "Pourquoi", "Source attestée"),), 10,
    ),)
    view.show_existing_search_unavailable(item, context_sections=context)
    assert [x[0] for x in events] == ["search", "actions", "context"]
    assert events[0] == ("search", item, False)
    assert events[1][1:] == (panel, result_row(item))
    assert events[2][1:] == ("Recommandation", [("Pourquoi", "Source attestée")])
    assert not any(e[0] == "detail" for e in events)

    before = events[:]
    events.clear()
    view.show_existing_search_detail(item, detail, context_sections=context)
    assert [x[0] for x in events] == ["detail", "actions", "context"]
    assert events[1] == before[1]
    assert events[2] == before[2]


def test_fallback_never_invents_purchase_for_public_search_result():
    view = _adapter()
    rows = []
    view._page = NS(detail=NS(
        show_search=lambda *_args, **_kwargs: None,
        _group=lambda *_args, **_kwargs: None,
    ))
    view._window = NS(_add_local_book_actions=lambda _panel, row: rows.append(row))
    item = replace(_source(), source_credential="card-B", record_id="edition-B")
    view.show_existing_search_unavailable(item)
    assert rows == [result_row(item)]
    assert rows[0]["state"] == "remote_catalog"
    assert rows[0]["source_credential"] == "card-B"
    assert rows[0]["record_id"] == "edition-B"
