"""#6168: Core source read-through cannot hold the N2 lock across viewer events."""
from __future__ import annotations

from threading import Event
from types import SimpleNamespace as NS

import pytest

from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import candidate
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import (
    CoverSourceSnapshot, DocumentCoverService,
)


DOC = CoverDocumentScope(
    kind="public_catalogue", identity_id=None, provider_id="bnf",
    credential_id="__public__", source_key="notice-c:readthrough-lock",
)
A = "https://covers.example.org/a.jpg"
B = "https://covers.example.org/b.jpg"


def snapshot(revision=1, uri=A):
    return CoverSourceSnapshot(
        scope=DOC, revision=revision,
        candidates=(candidate(
            uri, tier="catalog", source="notice-c",
            evidence_id=DOC.source_key,
        ),),
        snapshot_complete=True,
    )


@pytest.fixture
def loader(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    instance = CoverLoader(max_workers=2)
    monkeypatch.setattr(
        instance, "load", lambda *_a, **_k: NS(cancel=lambda: None),
    )
    try:
        yield instance
    finally:
        instance.close()


def test_reader_notifies_both_viewers_without_owning_service_lock(loader):
    entered, release, seen = Event(), Event(), Event()
    def reader(_document):
        entered.set()
        assert release.wait(5)
        return snapshot()

    service = DocumentCoverService(loader, cached_source_reader=reader)
    observed = []

    def notify():
        # RLock is owned by the current reader thread in the old implementation
        # even though register() locally released one reentrant nesting.
        observed.append(service._lock._is_owned())
        if len(observed) == 2:
            seen.set()

    try:
        listing = service.get_cover(
            DOC, "list", lambda _: None, on_source_changed=notify,
        )
        detail = service.get_cover(
            DOC, "detail", lambda _: None, on_source_changed=notify,
        )
        assert entered.wait(5)
        release.set()
        assert seen.wait(5)
        assert observed == [False, False]
        assert not listing.still_current
        assert not detail.still_current
        assert service.stats()["n2_register_new"] == 1
        assert service.get_cover(
            DOC, "detail", lambda _: None,
        ).source_uri == A
    finally:
        release.set()
        service.close()


def test_reader_presence_cas_does_not_replace_concurrent_newer_proof(loader):
    called = Event()
    def reader(_document):
        return snapshot(revision=1)

    service = DocumentCoverService(loader, cached_source_reader=reader)
    original = service._register_snapshot
    notifications = []

    def interleave(cached, *, only_if_absent):
        if only_if_absent:
            # Deterministic competing producer after the reader returned, but
            # before the source worker performs its atomic presence check.
            service.register(snapshot(revision=5, uri=B))
        return original(cached, only_if_absent=only_if_absent)

    service._register_snapshot = interleave
    try:
        service.get_cover(
            DOC, "detail", lambda _: None,
            on_source_changed=lambda: (notifications.append("rebind"), called.set()),
        )
        assert called.wait(5)
        assert notifications == ["rebind"]
        assert service.get_cover(DOC, "detail", lambda _: None).source_uri == B
        assert service._snapshots[DOC].revision == 5
        assert service.stats()["n2_register_new"] == 1
        assert service.stats()["n2_register_changed"] == 0
    finally:
        service.close()


def test_register_outside_worker_still_reuses_same_revision(loader):
    service = DocumentCoverService(loader)
    seen = []
    try:
        first = service.get_cover(
            DOC, "detail", lambda _: None,
            on_source_changed=lambda: seen.append(service._lock._is_owned()),
        )
        service.register(snapshot())
        service.register(snapshot())
        assert seen == [False]
        assert not first.still_current
        assert service.stats()["n2_register_reused"] == 1
    finally:
        service.close()
