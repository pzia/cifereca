"""P1 #741/#841: real source adapters through the YouthView collection pipeline.

No GTK renderer and no external network are needed here: this verifies the
plugin/service/task boundary. The independent UX role owns visual E2E proofs.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace as NS

import httpx

PLUGINS = Path(__file__).resolve().parents[2]
for name in ("youth-source-booktrust", "youth-source-milan"):
    path = str(PLUGINS / name)
    if path not in sys.path:
        sys.path.insert(0, path)

from booktrust_youth import BookTrustSource
from milan_youth import MilanYouthSource
from youth_catalog import YouthCatalogPlugin
from youth_page import FilterState, YouthView, source_info


class Picker:
    def __init__(self):
        self.selected = 0
        self.model = ()

    def get_selected(self):
        return self.selected

    def set_selected(self, value):
        self.selected = value

    def set_model(self, values):
        self.model = tuple(values)
        self.selected = 0


class Label:
    def __init__(self):
        self.value = ""

    def set_label(self, value):
        self.value = value


class Master:
    def __init__(self):
        self.entries = ()
        self.state = None

    def show_rows(self, entries, *, total_count):
        self.entries = tuple(entries)
        assert total_count == len(self.entries)

    def set_list_state(self, *args):
        self.state = args

    def empty_detail(self):
        pass


class DeferredHandle:
    def __init__(self, operation):
        self.operation = operation
        self.callback = None
        self.was_cancelled = False

    def cancel(self):
        self.was_cancelled = True

    def add_done_callback(self, callback):
        self.callback = callback

    def result(self):
        if self.error is not None:
            raise self.error
        return self.value

    def finish(self):
        try:
            self.value = self.operation(NS(cancelled=lambda: self.was_cancelled))
            self.error = None
        except Exception as exc:
            self.error = exc
        self.callback(self)


class TaskBroker:
    def __init__(self):
        self.handles = []

    def submit(self, operation, **kwargs):
        assert kwargs.get("kind") == "youth"
        handle = DeferredHandle(operation)
        self.handles.append(handle)
        return handle

    def finish(self, callbacks):
        self.handles[-1].finish()
        while callbacks:
            callbacks.pop(0)()


def build_view(services):
    """Connect the actual non-rendering YouthView methods to active services."""
    pending = []
    tasks = TaskBroker()
    label = Label()
    picker = Picker()
    master = Master()
    user = ["camille"]
    catalog = YouthCatalogPlugin()
    context = NS(
        services=lambda category, **kwargs: tuple(services) if category == "youth.source" else (),
        broker=lambda category: tasks if category == "tasks" else None,
    )
    view = object.__new__(YouthView)
    view.plugin = NS(context=context)
    view.catalog = catalog
    view.source = picker
    view.status = label
    view.master = master
    view.Gtk = NS(StringList=NS(new=lambda items: tuple(items)))
    view.GLib = NS(idle_add=lambda callback: pending.append(callback))
    view.identity_id = lambda: user[0]
    view.filter_state = lambda: FilterState(
        age=8,
        source_id=source_info(view.selected_source())[0] if view.selected_source() else "",
    )
    view.rows = {}
    view.next_cursor = ""
    view.next_source_plugin = ""
    view.generation = 0
    view.task = None
    view.disposed = False
    view.reload_sources()
    return view, catalog, tasks, pending, user, label, picker, master


def test_active_booktrust_and_milan_ui_collection_cache_outage_and_recovery():
    requests = []
    state = {"milan": "ok"}
    booktrust_list = """
    <article><h3><a href="/book-recommendations/bookfinder/forest/">
    Forest</a></h3><p>by Anne 2022</p><p>For ages 8 to 12</p></article>
    """
    milan_listing = (
        '<a href="/livres/63047-moqueuse/">Moqueuse</a>'
        '<a href="/livres/page/2/">Suivant</a>'
    )
    milan_second = '<a href="/livres/77561-lescargot/">Escargot</a>'
    milan_detail = (
        '<h1>Moqueuse</h1><div>Auteur</div><div>Nathalie Somers</div>'
        '<div>Âge</div><div>À partir de 8 ans</div>'
        '<div>Code ISBN</div><div>978-2-408-02080-4</div>'
    )
    milan_detail_second = (
        '<h1>Escargot</h1><div>Âge</div><div>À partir de 1 ans</div>'
        '<div>Code ISBN</div><div>978-2-408-04507-4</div>'
    )

    def handler(request):
        requests.append((request.url.host, request.url.path))
        if request.url.host == "www.booktrust.org.uk":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=booktrust_list)
        assert request.url.host == "www.editionsmilan.com"
        if state["milan"] == "http503":
            return httpx.Response(503, headers={"content-type": "text/html"}, text="down")
        if state["milan"] == "timeout":
            raise httpx.ReadTimeout("unavailable", request=request)
        if request.url.path == "/livres/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=milan_listing)
        if request.url.path == "/livres/page/2/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=milan_second)
        if request.url.path == "/livres/63047-moqueuse/":
            html = milan_detail.replace("Moqueuse</h1>", "Moqueuse retrouvée</h1>") if state["milan"] == "recovered" else milan_detail
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
        if request.url.path == "/livres/77561-lescargot/":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=milan_detail_second)
        raise AssertionError(f"unexpected HTTP URL: {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    booktrust = BookTrustSource(client=client)
    milan = MilanYouthSource(client=client)
    services = [
        NS(plugin_id="youth.source.booktrust", service=booktrust),
        NS(plugin_id="youth.source.milan", service=milan),
    ]
    view, catalog, tasks, pending, user, label, picker, master = build_view(services)
    assert tuple(picker.model) == ("Toutes les sources", "BookTrust Bookfinder", "Éditions Milan")
    assert requests == []  # activating services and rendering cache must not fetch

    picker.set_selected(1)
    view.filters_changed()
    assert requests == []
    view.collect()
    assert requests == []  # an explicit click schedules work, not synchronous HTTP
    tasks.finish(pending)
    assert [book.title for book in catalog.search_cached("camille", age=8)] == ["Forest"]
    assert [row.title for row in master.entries] == ["Forest"]
    assert catalog.search_cached("alex") == ()
    assert len(requests) == 1

    picker.set_selected(2)
    view.filters_changed()
    assert len(requests) == 1
    view.collect()
    tasks.finish(pending)
    assert view.next_cursor == "2"
    assert view.next_source_plugin == "youth.source.milan"
    assert [book.title for book in master.entries] == ["Moqueuse"]
    milan_record = catalog.search_cached("camille", age=8, source_id="milan")[0]
    assert milan_record.identifiers["isbn"] == "9782408020804"
    assert milan_record.age_ranges[0].source_label == "À partir de 8 ans"
    assert milan_record.provenance == ("milan",)

    view.more()
    tasks.finish(pending)
    assert view.next_cursor == ""
    assert {book.title for book in catalog.search_cached("camille", source_id="milan")} == {"Moqueuse", "Escargot"}
    snapshot = catalog.search_cached("camille")
    fresh = catalog.index.fresh_until("camille", "milan")
    assert fresh and len(snapshot) == 3
    before = len(requests)
    view.render_cached()
    assert len(requests) == before

    for fault in ("http503", "timeout"):
        state["milan"] = fault
        view.collect()
        tasks.finish(pending)
        assert label.value == "Échec de la source. Le cache précédent est conservé."
        assert catalog.search_cached("camille") == snapshot
        assert catalog.index.fresh_until("camille", "milan") == fresh
        assert catalog.search_cached("alex") == ()

    state["milan"] = "recovered"
    view.collect()
    tasks.finish(pending)
    assert [b.title for b in catalog.search_cached("camille", source_id="milan")] == ["Moqueuse retrouvée"]
    assert [b.title for b in catalog.search_cached("camille", source_id="booktrust")] == ["Forest"]
    assert catalog.search_cached("alex") == ()
    assert "notice(s) reçue(s)" in label.value

    user[0] = "alex"
    view.filters_changed()
    assert master.entries == ()
    assert catalog.search_cached("camille")


def test_unselected_or_deactivated_source_cannot_start_http_collection():
    calls = []
    source = NS(source_info=lambda: {"source_id": "booktrust", "display_name": "BookTrust"},
                browse=lambda **kwargs: calls.append(kwargs) or {"items": []})
    services = [NS(plugin_id="youth.source.booktrust", service=source)]
    view, catalog, tasks, pending, user, label, picker, master = build_view(services)
    view.collect()
    assert label.value == "Choisissez une source Jeunesse avant de collecter."
    assert calls == [] and tasks.handles == []

    picker.set_selected(1)
    view.filters_changed()
    services.clear()  # dynamically deactivate the source
    view.reload_sources()
    assert view.selected_source() is None
    view.collect()
    assert not tasks.handles and not calls
    assert catalog.search_cached("camille") == ()
