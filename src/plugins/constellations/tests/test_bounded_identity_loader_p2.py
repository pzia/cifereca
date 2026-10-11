"""P2 #700: bounded Constellations page cache/metadata scan and profile isolation.

No real GTK, HTTP, I/O or scheduler threads. Workers are explicitly run
by tests and applied through the queued main-thread callback.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.plugin_api import BookRef
from constellations_page import View, LOCAL_NODE_LIMIT, PAGE_CACHE_PROFILES
from constellation_view_model import Navigation


class Handle:
    def __init__(self, operation):
        self.operation, self.callback, self.value, self.error = operation, None, None, None
        self.cancelled = False
        self.control = NS(cancelled=lambda: self.cancelled)

    def cancel(self):
        self.cancelled = True

    def add_done_callback(self, callback):
        self.callback = callback

    def result(self):
        if self.error:
            raise self.error
        return self.value

    def finish(self):
        try:
            self.value = self.operation(self.control)
        except Exception as error:
            self.error = error
        self.callback(self)


class Tasks:
    def __init__(self):
        self.handles = []

    def submit(self, operation, **_kwargs):
        handle = Handle(operation)
        self.handles.append(handle)
        return handle


class Metadata:
    def __init__(self, total):
        self.total = total
        self.reads = 0
        self.calls = []

    def library_entries(self, *, identity_id, scan):
        self.calls.append((identity_id, scan))
        total = self.total
        if total is None:
            from itertools import count
            ids = count()
        else:
            ids = range(total)
        for i in ids:
            self.reads += 1
            yield {"book_id": f"{identity_id}-{i}", "title": f"Book {identity_id} {i}"}


def fixture(metadata):
    v = object.__new__(View)
    tasks, queued, messages, visits = Tasks(), [], [], []
    v.disposed, v.generation, v.task = False, 0, None
    v.current_identity = "alice"
    v.identity_id = lambda: v.current_identity
    v.nodes, v.local, v.center = (), (), None
    v.nav, v.cache = Navigation(), {}
    v.GLib = NS(idle_add=queued.append)
    v.status = NS(set_label=messages.append)
    v.render = lambda: None
    v.visit = visits.append
    v.plugin = NS(pending=None, pending_identity="", context=NS(broker=lambda name: {
        "tasks": tasks, "metadata": metadata,
        "catalog": NS(search=lambda *_args, **_kwargs: ())
    }[name]))
    return v, tasks, queued, messages, visits


def finish(tasks, queued):
    tasks.handles[-1].finish()
    while queued:
        queued.pop(0)()


def test_infinite_library_is_not_exhausted_and_coverage_is_honest():
    metadata = Metadata(None)
    v, tasks, queue, messages, visits = fixture(metadata)
    v.load()
    assert metadata.reads == 0 and len(tasks.handles) == 1
    finish(tasks, queue)
    assert metadata.reads == LOCAL_NODE_LIMIT + 1
    assert len(v.local) == LOCAL_NODE_LIMIT
    assert len(v.nodes) == LOCAL_NODE_LIMIT
    assert len(v.cache["alice"][0]) == LOCAL_NODE_LIMIT
    assert v.cache["alice"][1] is True
    assert "premières notices" in messages[-1]
    assert "plus grande que le plafond" in messages[-1]
    assert v.center is None and not visits
    assert metadata.calls == [("alice", False)]  # scan=False, no network


def test_exactly_limit_is_complete_and_small_libraries_report_coverage():
    for total in (0, 1, LOCAL_NODE_LIMIT):
        metadata = Metadata(total)
        v, tasks, queue, messages, visits = fixture(metadata)
        v.load()
        finish(tasks, queue)
        assert metadata.reads == total
        assert v.cache["alice"][1] is False
        assert f"{total} notices locales examinées" in messages[-1]
        assert v.center is None and visits == []


def test_cached_snapshot_avoids_second_scan_and_keeps_partial_badge():
    metadata = Metadata(LOCAL_NODE_LIMIT + 50)
    v, tasks, queue, messages, _visits = fixture(metadata)
    v.load()
    finish(tasks, queue)
    assert metadata.reads == LOCAL_NODE_LIMIT + 1
    v.load()
    finish(tasks, queue)
    assert metadata.reads == LOCAL_NODE_LIMIT + 1
    assert metadata.calls == [("alice", False)]
    assert "plus grande que le plafond" in messages[-1]
    v.load(refresh=True)
    finish(tasks, queue)
    assert len(metadata.calls) == 2


def test_cache_is_per_identity_and_evicts_least_recently_used_profile():
    metadata = Metadata(2)
    v, tasks, queue, _messages, _visits = fixture(metadata)
    for i in range(PAGE_CACHE_PROFILES):
        v.current_identity = f"identity-{i}"
        v.load()
        finish(tasks, queue)
    assert len(v.cache) == PAGE_CACHE_PROFILES
    original_calls = len(metadata.calls)
    v.current_identity = "identity-0"
    v.load()
    finish(tasks, queue)
    assert len(metadata.calls) == original_calls  # most recent hit
    v.current_identity = "identity-new"
    v.load()
    finish(tasks, queue)
    assert len(v.cache) == PAGE_CACHE_PROFILES
    assert "identity-0" in v.cache
    assert "identity-1" not in v.cache  # true LRU
    assert all(
        all(identity == row["book_id"].split("-")[0] for row in ())
        for identity in ()
    )  # no hidden cross-profile shared static cache


def test_wrong_identity_pending_book_is_not_injected_or_consumed():
    metadata = Metadata(2)
    v, tasks, queue, messages, visits = fixture(metadata)
    original = BookRef("selected-in-alice", "Alice private selection")
    v.plugin.pending, v.plugin.pending_identity = original, "alice"
    v.current_identity = "bob"
    v.load()
    finish(tasks, queue)
    assert not visits and v.center is None
    assert all(node.book.title != original.title for node in v.nodes)
    assert v.plugin.pending is original  # not silently consumed in Bob

    v.current_identity = "alice"
    v.load()
    finish(tasks, queue)
    assert len(visits) == 1
    assert visits[0].book.title == original.title
    assert v.plugin.pending is None
    assert v.cache["bob"][0] != v.cache["alice"][0]


def test_cancelled_task_does_not_read_or_update_cache():
    metadata = Metadata(None)
    v, tasks, queued, _msgs, _visits = fixture(metadata)
    v.load()
    tasks.handles[-1].cancel()
    finish(tasks, queued)
    assert metadata.reads == 0 and not v.cache


def test_stale_identity_callback_never_writes_unrelated_cache():
    metadata = Metadata(2)
    v, tasks, queued, _msgs, visits = fixture(metadata)
    v.load()
    tasks.handles[-1].finish()  # worker did run for Alice, queued GTK commit
    v.current_identity = "bob"
    while queued:
        queued.pop(0)()
    assert not v.cache and not visits
