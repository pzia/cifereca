"""#6168: a closing CoreUI N2 service cancels its live GTK viewer handles."""
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


REF = CoverDocumentScope(
    kind="public_catalogue", identity_id=None,
    provider_id="bnf", credential_id="__public__",
    source_key="notice-c:close-cancel-exact",
)
URI = "https://covers.example.org/exact.jpg"


def proof():
    return CoverSourceSnapshot(
        scope=REF, revision=1,
        candidates=(candidate(
            URI, tier="catalog", source="notice-c",
            evidence_id=REF.source_key,
        ),),
        snapshot_complete=True,
    )


@pytest.fixture
def loader(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    instance = CoverLoader(max_workers=2)
    try:
        yield instance
    finally:
        instance.close()


def test_close_revokes_missing_source_list_detail_and_subscriptions(loader):
    svc = DocumentCoverService(loader)
    events = []
    first = svc.get_cover(
        REF, "list", lambda _: None,
        on_source_changed=lambda: events.append("list"),
    )
    second = svc.get_cover(
        REF, "detail", lambda _: None,
        on_source_changed=lambda: events.append("detail"),
    )
    assert first.active and second.active
    svc.close()
    assert not first.active and not second.active
    assert not first.still_current and not second.still_current
    assert svc._source_subscription_count == 0
    assert svc._source_subscriptions == {}
    first.cancel()
    second.cancel()
    svc.close()  # Window teardown is idempotent.
    assert events == []


def test_close_cancels_live_n3_download_requests(loader, monkeypatch):
    children = []
    def fake_load(_uri, _callback, **_kwargs):
        child = NS(cancel=lambda: children.append("canceled"))
        return child
    monkeypatch.setattr(loader, "load", fake_load)
    svc = DocumentCoverService(loader)
    svc.register(proof())
    ref = svc.get_cover(
        REF, "detail", lambda _: None, on_source_changed=lambda: None,
    )
    assert ref.active and ref.source_uri == URI
    svc.close()
    assert not ref.active
    assert children == ["canceled"]


def test_listener_closing_window_drops_later_observers_in_same_publication(loader):
    svc = DocumentCoverService(loader)
    events = []
    first = svc.get_cover(
        REF, "list", lambda _: None,
        on_source_changed=lambda: (events.append("closing"), svc.close()),
    )
    second = svc.get_cover(
        REF, "detail", lambda _: None,
        on_source_changed=lambda: events.append("late-view"),
    )
    svc.register(proof())
    assert events == ["closing"]
    assert not first.active and not second.active
    assert svc._source_subscriptions == {}


def test_close_during_cache_source_read_cancels_pending_waiters(loader):
    entered, release, completed = Event(), Event(), Event()
    events = []
    def reader(_scope):
        entered.set()
        assert release.wait(5)
        completed.set()
        return proof()
    svc = DocumentCoverService(loader, cached_source_reader=reader)
    try:
        first = svc.get_cover(
            REF, "list", lambda _: None,
            on_source_changed=lambda: events.append("list"),
        )
        assert entered.wait(5)
        detail = svc.get_cover(
            REF, "detail", lambda _: None,
            on_source_changed=lambda: events.append("detail"),
        )
        svc.close()
        assert not first.active and not detail.active
        release.set()
        assert completed.wait(5)
        assert events == []
        assert svc._snapshots == {}
    finally:
        release.set()
        svc.close()
