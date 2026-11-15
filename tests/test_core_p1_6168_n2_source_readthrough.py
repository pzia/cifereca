"""#6168: Core-only N2 read-through is asynchronous, bounded and source-exact."""
from __future__ import annotations

from dataclasses import replace
from threading import Event, Lock

import pytest

from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import candidate
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import (
    CoverSourceSnapshot, DocumentCoverService,
)


URL = "https://covers.example.org/exact-edition.jpg"
OTHER = "https://covers.example.org/other-edition.jpg"
REF = CoverDocumentScope(
    kind="public_catalogue", identity_id=None, provider_id="bnf",
    credential_id="__public__", source_key="notice-c:source-exact-a",
)
REF_B = replace(REF, source_key="notice-c:source-exact-b")


def proof(ref=REF, *, revision=1, url=URL):
    return CoverSourceSnapshot(
        scope=ref, revision=revision, candidates=(
            candidate(url, tier="catalog", source="notice-c",
                      evidence_id=ref.source_key),
        ), snapshot_complete=True,
    )


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    calls = []
    monkeypatch.setattr(loader, "load", lambda uri, cb, **kw: (
        calls.append(("detail", uri)) or type("Request", (), {
            "cancel": lambda self: None,
        })()
    ))
    monkeypatch.setattr(loader, "load_preview", lambda uri, cb, **kw: (
        calls.append(("list", uri)) or type("Request", (), {
            "cancel": lambda self: None,
        })()
    ))
    try:
        yield loader, calls
    finally:
        loader.close()


def test_missing_n2_proof_is_fetched_once_for_list_and_detail(gateway):
    loader, calls = gateway
    entered = Event()
    release = Event()
    readers = []
    lock = Lock()

    def read(ref):
        with lock:
            readers.append(ref)
        entered.set()
        assert release.wait(5)
        return proof(ref)

    service = DocumentCoverService(loader, cached_source_reader=read)
    try:
        callbacks = []
        first = service.get_cover(
            REF, "list", lambda path: callbacks.append(("list", path)),
            on_source_changed=lambda: callbacks.append(("list", "rebind")),
        )
        second = service.get_cover(
            REF, "detail", lambda path: callbacks.append(("detail", path)),
            on_source_changed=lambda: callbacks.append(("detail", "rebind")),
        )
        assert entered.wait(5)
        assert readers == [REF]
        assert calls == []
        assert callbacks == [("list", None), ("detail", None)]
        release.set()
        for _ in range(5):
            if ("list", "rebind") in callbacks and ("detail", "rebind") in callbacks:
                break
            Event().wait(0.05)
        assert ("list", "rebind") in callbacks
        assert ("detail", "rebind") in callbacks
        assert not first.still_current
        assert not second.still_current
        assert service.get_cover(REF, "list", lambda _: None).source_uri == URL
        assert service.get_cover(REF, "detail", lambda _: None).source_uri == URL
        assert calls == [("list", URL), ("detail", URL)]
        assert readers == [REF]
    finally:
        release.set()
        service.close()


def test_none_is_not_missing_and_cooldown_bounds_retry(gateway):
    loader, calls = gateway
    seen = []
    done = Event()
    clock = [100.0]

    def reader(ref):
        seen.append(ref)
        done.set()
        return None

    service = DocumentCoverService(
        loader, cached_source_reader=reader, clock=lambda: clock[0],
    )
    try:
        assert service.get_cover(REF, "detail", lambda _: None).source_uri is None
        assert done.wait(5)
        for _ in range(20):
            if REF not in service._read_active:
                break
            Event().wait(0.01)
        assert service.decisions.peek(REF, revision=0) is None
        service.get_cover(REF, "list", lambda _: None)
        assert seen == [REF]
        clock[0] += 16
        done.clear()
        service.get_cover(REF, "detail", lambda _: None)
        assert done.wait(5)
        assert seen == [REF, REF]
        assert not calls
    finally:
        service.close()


def test_foreign_document_or_wrong_shape_never_enters_n2(gateway):
    loader, calls = gateway
    done = Event()

    def reader(ref):
        done.set()
        return proof(REF_B)

    service = DocumentCoverService(loader, cached_source_reader=reader)
    try:
        touched = []
        service.get_cover(
            REF, "detail", lambda path: touched.append(path),
            on_source_changed=lambda: touched.append("rebind"),
        )
        assert done.wait(5)
        for _ in range(20):
            if REF not in service._read_active:
                break
            Event().wait(0.01)
        assert touched == [None]
        assert service.decisions.peek(REF, revision=1) is None
        assert REF not in service._snapshots
        assert REF_B not in service._snapshots
        assert not calls
    finally:
        service.close()


def test_newer_core_source_wins_race_against_old_reader(gateway):
    loader, calls = gateway
    entered = Event()
    release = Event()
    notified = Event()

    def read(ref):
        entered.set()
        assert release.wait(5)
        return proof(ref, revision=1)

    service = DocumentCoverService(loader, cached_source_reader=read)
    try:
        old = service.get_cover(
            REF, "detail", lambda _: None, on_source_changed=notified.set,
        )
        assert entered.wait(5)
        service.register(proof(REF, revision=5, url=OTHER))
        assert not old.still_current
        release.set()
        assert notified.wait(5)
        assert service.get_cover(REF, "detail", lambda _: None).source_uri == OTHER
        assert service._snapshots[REF].revision == 5
        assert calls == [("detail", OTHER)]
    finally:
        release.set()
        service.close()


def test_closed_widget_and_service_drop_late_source(gateway):
    loader, calls = gateway
    entered = Event()
    release = Event()
    callbacks = []

    def read(ref):
        entered.set()
        assert release.wait(5)
        return proof(ref)

    service = DocumentCoverService(loader, cached_source_reader=read)
    request = service.get_cover(
        REF, "detail", lambda _: None,
        on_source_changed=lambda: callbacks.append("rebind"),
    )
    assert entered.wait(5)
    request.cancel()
    service.close()
    release.set()
    Event().wait(0.1)
    assert not request.still_current
    assert callbacks == []
    assert service._snapshots == {}
    assert calls == []


def test_reader_exception_does_not_become_negative_evidence(gateway):
    loader, calls = gateway
    done = Event()
    def bad(_ref):
        done.set()
        raise RuntimeError("private account failure")
    service = DocumentCoverService(loader, cached_source_reader=bad)
    try:
        assert service.get_cover(REF, "list", lambda _: None).source_uri is None
        assert done.wait(5)
        assert service.decisions.peek(REF, revision=1) is None
        assert not calls
    finally:
        service.close()


def test_no_reader_preserves_existing_lazy_manual_registration(gateway):
    loader, calls = gateway
    service = DocumentCoverService(loader)
    try:
        assert service.get_cover(REF, "list", lambda _: None).source_uri is None
        assert not service._read_active
        service.register(proof())
        assert service.get_cover(REF, "detail", lambda _: None).source_uri == URL
        assert calls == [("detail", URL)]
    finally:
        service.close()


def test_registration_in_gap_between_n2_miss_and_worker_notifies_view(gateway):
    loader, calls = gateway
    reader_calls = []
    service = DocumentCoverService(
        loader, cached_source_reader=lambda ref: reader_calls.append(ref),
    )
    original = service._read_source_on_n2_miss

    def admit_concurrent_source(document, handle, notify):
        # Deterministic interleaving after the N2 MISS has been observed,
        # before its asynchronous reader could be scheduled.
        service.register(proof(document, revision=5))
        original(document, handle, notify)

    service._read_source_on_n2_miss = admit_concurrent_source
    try:
        callbacks = []
        old = service.get_cover(
            REF, "detail", lambda path: callbacks.append(("neutral", path)),
            on_source_changed=lambda: callbacks.append(("source", "rebind")),
        )
        assert callbacks == [("source", "rebind"), ("neutral", None)]
        assert not old.still_current
        assert not reader_calls
        assert service.get_cover(REF, "detail", lambda _: None).source_uri == URL
        assert calls == [("detail", URL)]
    finally:
        service.close()


def test_reader_is_not_used_on_registered_n2_hit(gateway):
    loader, calls = gateway
    reader_calls = []
    service = DocumentCoverService(
        loader, cached_source_reader=lambda ref: reader_calls.append(ref),
    )
    try:
        service.register(proof())
        request = service.get_cover(REF, "detail", lambda _: None)
        assert request.source_uri == URL
        assert reader_calls == []
        assert calls == [("detail", URL)]
    finally:
        service.close()
