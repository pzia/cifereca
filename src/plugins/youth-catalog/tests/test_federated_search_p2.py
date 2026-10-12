"""P2 #1028: federated Youth sources are explicit, bounded and isolated."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep
from types import SimpleNamespace as NS

from youth_catalog import _extract_page, YouthCatalogPlugin
from youth_federation import (
    PAGE_LIMIT, MAX_SOURCES, MAX_CONCURRENCY, MAX_PAGES,
    FederatedSource, collect_federated, prepare_sources,
)
from youth_federation_ui import run_federated_action
from youth_page import YouthView


class Service:
    def __init__(self, source_id, *, failure=False):
        self.sid = source_id
        self.failure = failure
        self.calls = []

    def source_info(self):
        return {"source_id": self.sid, "display_name": "Source " + self.sid}

    def search(self, query, *, cursor=None, limit=50):
        self.calls.append((query, cursor, limit))
        if self.failure:
            raise ValueError("SECRET_TOKEN=private")
        if cursor is None:
            return {"items": [{"title": f"Book {self.sid}", "record_id": f"id-{self.sid}"}],
                    "next_cursor": "page-2"}
        return {"items": [{"title": f"Suite {self.sid}", "record_id": f"next-{self.sid}"}],
                "next_cursor": ""}


def fetch(service, query, *, age, themes, cursor, limit, cancelled):
    return service.search(query, cursor=cursor, limit=limit)


def source_list(*services):
    return tuple(NS(plugin_id=f"youth.source.{s.sid}", service=s) for s in services)


def test_registry_ignores_disabled_bad_incompatible_and_duplicates():
    one, same, two = Service("one"), Service("one"), Service("two")
    items = source_list(one, same, two) + (
        NS(plugin_id="disabled", service=Service("disabled"), enabled=False),
        NS(plugin_id="missing", service=NS(source_info=lambda: {"source_id": "missing"})),
    )
    chosen, skipped = prepare_sources(items, info=lambda item: (
        item.service.source_info()["source_id"], item.service.source_info()["display_name"]
    ))
    assert tuple(item.source_id for item in chosen) == ("one", "two")
    assert skipped == 3


def test_three_sources_one_fails_without_exposing_vendor_error():
    a, b, c = Service("a"), Service("b", failure=True), Service("c")
    sources, skipped = prepare_sources(
        source_list(a, b, c),
        info=lambda item: (item.service.source_info()["source_id"], item.plugin_id),
    )
    callbacks = []
    result = collect_federated(
        sources, query="quête", age=None, themes=(),
        fetch=fetch, extract=_extract_page, cancelled=lambda: False,
        on_source=callbacks.append, skipped=skipped,
    )
    assert result.eligible == 3 and result.succeeded == 2
    assert result.unavailable == 1 and len(result.results) == 3
    assert sorted(row.source_id for row in callbacks) == ["a", "b", "c"]
    assert a.calls == [("quête", None, 50), ("quête", "page-2", 50)]
    assert c.calls == [("quête", None, 50), ("quête", "page-2", 50)]
    assert b.calls == [("quête", None, 50)]
    assert all("SECRET" not in repr(row) for row in result.results)
    assert all(len(row.rows) <= PAGE_LIMIT * MAX_PAGES for row in result.results)


def test_concurrency_is_at_most_two_and_source_cap_eight():
    lock = Lock()
    running = [0]
    maximum = [0]
    calls = []

    def slow_fetch(service, query, **kwargs):
        with lock:
            running[0] += 1
            maximum[0] = max(maximum[0], running[0])
        sleep(0.003)
        with lock:
            calls.append(service)
            running[0] -= 1
        return {"items": [{"title": str(service)}], "next_cursor": ""}

    sources = tuple(FederatedSource(str(n), str(n), n) for n in range(12))
    report = collect_federated(
        sources, query="roman", age=None, themes=(),
        fetch=slow_fetch, extract=_extract_page, cancelled=lambda: False,
    )
    assert 1 <= maximum[0] <= MAX_CONCURRENCY
    assert len(calls) == MAX_SOURCES
    assert report.eligible == 8 and report.skipped == 4


def test_infinite_page_limited_to_n_plus_one_and_discarded_atomically():
    observations = [0]

    def infinite(_service, _query, **_kwargs):
        def generate():
            while True:
                observations[0] += 1
                yield {"title": "Endless"}
        return {"items": generate(), "next_cursor": "again"}

    result = collect_federated(
        (FederatedSource("id", "name", object()),),
        query="roman", age=None, themes=(), fetch=infinite,
        extract=_extract_page, cancelled=lambda: False,
    )
    assert result.results[0].error == "limit_exceeded"
    assert result.results[0].rows == ()
    assert observations[0] == PAGE_LIMIT + 1


def test_cursor_loop_or_invalid_second_page_never_commits_partial_rows():
    svc = object()

    def loop(_service, query, **kwargs):
        return {"items": [{"title": "Book"}], "next_cursor": "cycle"}

    result = collect_federated(
        (FederatedSource("src", "src", svc),), query="roman", age=None,
        themes=(), fetch=loop, extract=_extract_page, cancelled=lambda: False,
    )
    assert result.results[0].error == "cursor_loop"
    assert not result.results[0].rows


def test_empty_and_cancelled_queries_never_use_network():
    calls = []
    source = (FederatedSource("src", "source", object()),)
    for query, stop in (("", False), ("a", False), ("valid", True)):
        report = collect_federated(
            source, query=query, age=None, themes=(),
            fetch=lambda *_a, **_kw: calls.append(1),
            extract=_extract_page, cancelled=lambda: stop,
        )
        assert not report.results
    assert calls == []


class Handle:
    def __init__(self, operation):
        self.operation = operation
        self.control = NS(cancelled=lambda: self.was_cancelled)
        self.was_cancelled = False
        self.callback = None

    def cancel(self):
        self.was_cancelled = True

    def add_done_callback(self, cb):
        self.callback = cb

    def result(self):
        return self.value

    def finish(self):
        self.value = self.operation(self.control)
        self.callback(self)


class Tasks:
    def __init__(self):
        self.handles = []

    def submit(self, operation, **options):
        handle = Handle(operation)
        self.handles.append(handle)
        return handle


class Label:
    def __init__(self):
        self.text = ""

    def set_label(self, value):
        self.text = str(value)


def fixture_view():
    tasks, idle = Tasks(), []
    view = object.__new__(YouthView)
    view.disposed, view.generation, view.task = False, 0, None
    view._remote_running_token = None
    view.next_cursor, view.next_source_plugin = "", ""
    identity = ["alice"]
    state = NS(query="romans", age=None, themes=())
    view.identity_id = lambda: identity[0]
    view.filter_state = lambda: state
    view.federated_status = Label()
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda callback: idle.append(callback))
    view.catalog = YouthCatalogPlugin()
    view.render_cached = lambda: None
    view.refresh_source_diagnostics = lambda: None
    view.update_action_availability = lambda: None
    a, b, c = Service("a"), Service("b", failure=True), Service("c")
    view.sources = source_list(a, b, c)
    return view, identity, state, tasks, idle, (a, b, c)


def test_click_delivers_progressively_to_one_identity_without_implicit_network():
    view, identity, state, tasks, idle, services = fixture_view()
    assert not any(x.calls for x in services)
    run_federated_action(view)
    assert len(tasks.handles) == 1
    assert not any(x.calls for x in services)  # before the worker executes
    run_federated_action(view)  # double click does not queue
    assert len(tasks.handles) == 1
    tasks.handles[0].finish()
    assert len(idle) == 4  # three sources, one completion
    assert not view.catalog.index.entries("alice")
    for callback in idle:
        callback()
    assert len(view.catalog.index.entries("alice")) == 4
    assert len(view.catalog.index.entries("bob")) == 0
    assert "3/3" in view.federated_status.text
    assert "1 indisponible" in view.federated_status.text
    assert view._remote_running_token is None


def test_changed_identity_or_query_discard_all_late_source_responses():
    for edit in ("identity", "query"):
        view, identity, state, tasks, idle, services = fixture_view()
        run_federated_action(view)
        tasks.handles[0].finish()
        if edit == "identity":
            identity[0] = "bob"
        else:
            view.filter_state = lambda: NS(query="autre", age=None, themes=())
        view.generation += 1
        for cb in idle:
            cb()
        assert not view.catalog.index.entries("alice")
        assert not view.catalog.index.entries("bob")


def test_cancelled_in_flight_discards_queued_results():
    view, _identity, _state, tasks, idle, _services = fixture_view()
    run_federated_action(view)
    tasks.handles[0].finish()
    tasks.handles[0].cancel()
    for cb in idle:
        cb()
    assert not view.catalog.index.entries("alice")
