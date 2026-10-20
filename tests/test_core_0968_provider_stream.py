"""#968: optional virtual stream remains additive to Core progressive search."""
from __future__ import annotations

from threading import Event, Thread, current_thread
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import SearchResult
from cifereca.plugin_api import (
    ProviderSearchEvent, ProviderSearchStream, check_service_contract, contract_definition,
)
from cifereca.core.provider_stream import (
    SearchStreamCancelled, SearchStreamFailure, consume_provider_stream,
)
from cifereca.plugins.categories import CategoryRegistry
from cifereca.providers import (
    ProviderCapabilities, ProviderDescriptor, ProviderPlugin, ProviderSession,
)
from cifereca.services import LibraryService
from cifereca.storage import Storage

PARENT = "org.cifereca.integration.cifericaopds"
PID = "cifericaopds.alpha"


def row(name: str, *, source: str = PID) -> SearchResult:
    return SearchResult(
        id=f"{source}:{name}", record_id=name, docbase="BOOK",
        medium="digital", title=name, author="Author", type="ebook",
        format="EPUB", publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="FAKE",
        source_provider=source, source_credential="__public__",
    )


def event(kind: str, seq: int, *, name="a", generation=1, source=PID, **other):
    vals = {
        "provider_id": source, "catalog_generation": generation,
        "sequence": seq, "kind": kind,
    }
    if kind == "upsert":
        vals.update(item_key=f"BOOK:{name}", result=row(name, source=source))
    if kind == "remove":
        vals["item_key"] = f"BOOK:{name}"
    vals.update(other)
    return ProviderSearchEvent(**vals)


def test_contract_is_versioned_and_opt_in_only():
    desc = CategoryRegistry().get("provider.search.stream")
    assert desc.contract == "cifereca.provider.search.stream/1"
    assert desc.resolution == "all"
    assert contract_definition("provider.search.stream").protocol_name == "ProviderSearchStream"
    assert not check_service_contract("provider.search.stream", object()).ok


def test_replay_remove_progress_and_terminal_event():
    emitted = []
    result = consume_provider_stream([
        event("upsert", 1),
        event("upsert", 1, name="ignored-replay"),
        event("upsert", 2, name="b"),
        event("remove", 3, name="a"),
        event("progress", 4, current=3),
        event("done", 5),
    ], provider_id=PID, catalog_generation=1, publish=emitted.append, cancelled=lambda: False)
    assert [r.record_id for r in result.rows] == ["b"]
    assert [x.kind for x in emitted] == ["upsert", "upsert", "remove", "progress", "done"]
    assert emitted[3].total is None  # no invented percentage
    assert not result.warnings


@pytest.mark.parametrize("events", [
    [event("upsert", 2), event("done", 3)],
    [event("upsert", 1, generation=2), event("done", 2)],
    [event("upsert", 1, source="evil.source"), event("done", 2)],
    [event("upsert", 1, item_key="not-a-book"), event("done", 2)],
    [event("progress", 1, current=5, total=3), event("done", 2)],
    [event("progress", 1, current=True, total=2), event("done", 2)],
    [event("remove", 1, item_key="")],
    [event("upsert", 1)],  # no done
])
def test_malformed_or_incomplete_stream_is_rejected(events):
    with pytest.raises(SearchStreamFailure):
        consume_provider_stream(events, provider_id=PID, catalog_generation=1,
                                publish=lambda _e: None, cancelled=lambda: False)


def test_cooperative_cancel_does_not_emit_or_cache():
    emitted = []
    with pytest.raises(SearchStreamCancelled):
        consume_provider_stream([event("upsert", 1), event("done", 2)],
                                provider_id=PID, catalog_generation=1,
                                publish=emitted.append, cancelled=lambda: True)
    assert emitted == []


class StreamSession(ProviderSession):
    def __init__(self, *, descriptor, stream, **kw):
        super().__init__(descriptor=descriptor, **kw)
        self.events = stream
        self.legacy_calls = 0
        self.stream_calls = 0

    def connect(self):
        self.connected = True
        return []

    def close(self):
        self.connected = False

    def search(self, query, scope="all", page=0, field="all"):
        self.legacy_calls += 1
        return [row("fallback")]

    def search_stream(self, query, scope="all", *, field="all", content_types=(), control=None):
        self.stream_calls += 1
        return iter(self.events)


class StreamPlugin(ProviderPlugin):
    def __init__(self, events):
        self.events = events
        self.session = None
        self.descriptor = ProviderDescriptor(
            id=PID, display_name="Alpha", description="stream fixture",
            state="ready", capabilities=ProviderCapabilities(search=True),
        )

    def create_session(self, *, credential_id, label, credentials, options, config, storage, diagnostics):
        self.session = StreamSession(
            descriptor=self.descriptor, credential_id=credential_id, label=label,
            credentials=credentials, options=options, stream=self.events,
        )
        return self.session


class Collection:
    def __init__(self, events, *, stream_capability=True):
        self.plugin = StreamPlugin(events)
        self.stream_capability = stream_capability

    def snapshot(self):
        caps = ["catalog.search"]
        if self.stream_capability:
            caps.append("catalog.search.stream")
        return {
            "api_major": 1, "discovery_generation": 1,
            "parent_plugin_id": PARENT,
            "children": [{
                "plugin_id": PID, "source_id": "alpha",
                "capabilities": caps, "source_revision": "r1",
                "catalog_generation": 1, "status": "ready",
                "kind": "catalog", "instantiation": "singleton_public",
                "parent_plugin_id": PARENT, "is_virtual": True,
            }],
        }

    def refresh(self, *, explicit=True):
        raise AssertionError("never network refresh")

    def create_provider(self, source_id):
        assert source_id == "alpha"
        return self.plugin

    def subscribe(self, callback):
        return lambda: None


class Manager:
    def __init__(self, collection):
        self.collection = collection
        self.state = NS(identity_enabled_override=lambda provider_id, identity_id: None)

    def candidates(self, category):
        if category == "provider.collection":
            return [NS(descriptor=NS(id=PARENT))]
        return []

    def service(self, parent):
        return self.collection

    def status(self, parent):
        return NS(enabled=True, state="ready", descriptor=NS(id=PARENT, instantiation="multiple"))

    def enabled_for_identity(self, parent, identity):
        return True

    def subscribe_services(self, _category, _callback):
        return lambda: None


def _library(tmp_path, collection):
    cfg = Config(tmp_path / "config.json")
    lib = LibraryService(cfg, Storage(tmp_path / "db.sqlite3"), Diagnostics())
    lib.attach_plugin_manager(Manager(collection))
    lib.connect_configured()
    # Deterministic tests need no index mirroring.
    lib._mirror_remote_results = lambda _rows, **kw: 0
    return lib


def test_stream_events_are_on_search_caller_and_final_batch_is_legacy_compatible(tmp_path):
    collection = Collection([
        event("upsert", 1),
        event("upsert", 2, name="b"),
        event("remove", 3, name="a"),
        event("progress", 4, current=1),
        event("done", 5),
    ])
    lib = _library(tmp_path, collection)
    try:
        callback_threads = []
        batches, events = [], []
        def on_event(update):
            callback_threads.append(current_thread().ident)
            events.append(update)
        summary = lib.search_progressive(
            "book", force=True, on_batch=batches.append, on_event=on_event,
        )
        assert not summary.cancelled
        assert summary.result_count == 1
        assert collection.plugin.session.stream_calls == 1
        assert collection.plugin.session.legacy_calls == 0
        assert [e.kind for e in events] == [
            "upsert", "upsert", "remove", "progress", "done",
        ]
        assert all(x == current_thread().ident for x in callback_threads)
        assert len(batches) == 1 and [r.record_id for r in batches[0].results] == ["b"]
    finally:
        lib.close()


def test_gap_triggers_reset_and_safe_synchronous_resync(tmp_path):
    collection = Collection([event("upsert", 1), event("upsert", 3), event("done", 4)])
    lib = _library(tmp_path, collection)
    try:
        events = []
        batches = []
        summary = lib.search_progressive(
            "book", force=True, on_batch=batches.append, on_event=events.append,
        )
        assert summary.result_count == 1
        assert [x.kind for x in events] == ["upsert", "reset"]
        assert batches[0].results[0].record_id == "fallback"
        assert collection.plugin.session.legacy_calls >= 1
    finally:
        lib.close()


def test_optional_capability_off_keeps_synchronous_search(tmp_path):
    collection = Collection([event("upsert", 1), event("done", 2)], stream_capability=False)
    lib = _library(tmp_path, collection)
    try:
        events = []
        result = lib.search_progressive("book", force=True, on_event=events.append)
        assert result.result_count == 1
        assert collection.plugin.session.stream_calls == 0
        assert collection.plugin.session.legacy_calls >= 1
        assert events == []
    finally:
        lib.close()


def test_stream_cancelled_midflight_never_delivers_late_events(tmp_path):
    started = Event()
    release = Event()
    class BlockingEvents:
        def __iter__(self):
            yield event("upsert", 1)
            started.set()
            release.wait(timeout=2)
            yield event("upsert", 2, name="late")
            yield event("done", 3)

    collection = Collection(BlockingEvents())
    lib = _library(tmp_path, collection)
    cancelled = Event()
    control = NS(
        cancelled=cancelled.is_set,
        check_cancelled=lambda: None,
        report=lambda **kw: None,
    )
    events = []
    result = {}
    def run():
        result["summary"] = lib.search_progressive(
            "book", force=True, control=control,
            on_event=lambda x: (events.append(x), cancelled.set()),
        )
    thread = Thread(target=run, daemon=True)
    try:
        thread.start()
        assert started.wait(2)
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert result["summary"].cancelled
        assert [e.item_key for e in events] == ["BOOK:a"]
        assert "late" not in [x.record_id for x in lib.last_search]
    finally:
        release.set()
        thread.join(timeout=2)
        lib.close()



def test_public_error_code_and_messages_are_sanitized_before_callback():
    secret = "Bearer user-private-session-token"
    emitted = []
    result = consume_provider_stream([
        event("error", 1, error_code="api_key=" + secret,
              message="GET https://service.invalid?auth=" + secret,
              resume_token=secret,
              item_key=secret, result=row("private")),
        event("progress", 2, current=3, total=10,
              message=secret, resume_token=secret,
              item_key=secret, result=row("private")),
        event("done", 3, message=secret, resume_token=secret,
              item_key=secret, result=row("private"),
              error_code=secret),
    ], provider_id=PID, catalog_generation=1,
        publish=emitted.append, cancelled=lambda: False)
    assert result.error_code == "StreamPartialError"
    assert result.warnings == (f"{PID}: provider_error",)
    assert result.error_message == f"{PID}: provider_error"
    assert [x.kind for x in emitted] == ["error", "progress", "done"]
    assert emitted[0].error_code == "provider_error"
    assert (emitted[1].current, emitted[1].total) == (3, 10)
    for published in emitted:
        assert published.message == ""
        assert published.resume_token == ""
        assert published.item_key == ""
        assert published.result is None
        assert secret not in repr(published)


def test_known_public_error_code_survives_without_upstream_extras():
    emitted = []
    result = consume_provider_stream([
        event("error", 1, error_code="rate_limited", message="private book title",
              resume_token="private cursor"),
        event("done", 2),
    ], provider_id=PID, catalog_generation=1,
        publish=emitted.append, cancelled=lambda: False)
    assert result.warnings == (f"{PID}: rate_limited",)
    assert emitted[0].error_code == "rate_limited"
    assert emitted[0].message == "" and emitted[0].resume_token == ""


def test_upsert_public_projection_keeps_row_and_key_but_not_private_transport():
    emitted = []
    result = consume_provider_stream([
        event("upsert", 1, message="private upstream text",
              resume_token="private resume token", error_code="private",
              current=3000, total=5000),
        event("done", 2),
    ], provider_id=PID, catalog_generation=1,
        publish=emitted.append, cancelled=lambda: False)
    assert result.rows[0].record_id == "a"
    assert emitted[0].result == result.rows[0]
    assert emitted[0].item_key == "BOOK:a"
    assert emitted[0].error_code == ""
    assert emitted[0].current is None and emitted[0].total is None
    assert emitted[0].message == "" and emitted[0].resume_token == ""


def test_unknown_remove_cannot_publish_arbitrary_upstream_item_key():
    secret = "https://private.invalid/notice?token=secret"
    emitted = []
    with pytest.raises(SearchStreamFailure, match="remove references unknown item"):
        consume_provider_stream([
            event("upsert", 1),
            event("remove", 2, item_key=secret, message=secret),
            event("done", 3),
        ], provider_id=PID, catalog_generation=1,
            publish=emitted.append, cancelled=lambda: False)
    assert [x.kind for x in emitted] == ["upsert"]
    assert secret not in repr(emitted)


def test_public_stream_sanitization_applies_through_library_service(tmp_path):
    secret = "https://upstream.invalid?private-session=secret"
    collection = Collection([
        event("progress", 1, current=1, message=secret, resume_token=secret),
        event("error", 2, error_code=secret, message=secret, resume_token=secret),
        event("done", 3, message=secret),
    ])
    lib = _library(tmp_path, collection)
    try:
        events = []
        batches = []
        summary = lib.search_progressive(
            "book", force=True, on_event=events.append, on_batch=batches.append,
        )
        assert not summary.cancelled
        assert len(events) == 3
        assert [x.kind for x in events] == ["progress", "error", "done"]
        assert all(secret not in repr(x) for x in events)
        assert events[1].error_code == "provider_error"
    finally:
        lib.close()


def test_stream_upsert_cannot_impersonate_core_offline_fallback(tmp_path):
    from dataclasses import replace

    original = row("forged")
    poisoned = replace(
        original,
        metadata={"search_provenance": "known_catalog_stale", "publisher": "Public"},
    )
    collection = Collection([
        event("upsert", 1, name="forged", result=poisoned),
        event("done", 2),
    ])
    lib = _library(tmp_path, collection)
    try:
        events, batches = [], []
        result = lib.search_progressive(
            "forged", force=True, on_event=events.append, on_batch=batches.append,
        )
        assert result.result_count == 1
        assert len(events) == 2 and events[0].kind == "upsert"
        assert events[0].result.metadata == {"publisher": "Public"}
        assert len(batches) == 1
        assert batches[0].result_status == "live"
        assert batches[0].results[0].metadata == {"publisher": "Public"}
        assert poisoned.metadata["search_provenance"] == "known_catalog_stale"
    finally:
        lib.close()
