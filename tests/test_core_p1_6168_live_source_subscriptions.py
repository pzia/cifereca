"""#6168: a visible GTK DocumentRef resubscribes on exact Notice C N2 changes."""
from __future__ import annotations

from dataclasses import replace
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
    kind="public_catalogue", identity_id=None,
    provider_id="bnf", credential_id="__public__",
    source_key="notice-c:exact-edition-a",
)
FOREIGN = replace(DOC, source_key="notice-c:exact-edition-b")
URL = "https://covers.example.org/edition-a.jpg"
OTHER = "https://covers.example.org/edition-b.jpg"


def proof(ref=DOC, revision=1, url=URL, *, complete=True):
    return CoverSourceSnapshot(
        scope=ref, revision=revision, snapshot_complete=complete,
        candidates=(candidate(
            url, tier="catalog", source="notice-c",
            evidence_id=ref.source_key,
        ),),
    )


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    requested = []
    monkeypatch.setattr(
        loader, "load", lambda url, callback, **kwargs: (
            requested.append(("detail", url)) or NS(cancel=lambda: None)
        ),
    )
    monkeypatch.setattr(
        loader, "load_preview", lambda url, callback, **kwargs: (
            requested.append(("list", url)) or NS(cancel=lambda: None)
        ),
    )
    service = DocumentCoverService(loader)
    try:
        yield service, requested
    finally:
        service.close()
        loader.close()


def test_first_notice_c_source_admission_rebinds_existing_list_and_detail(gateway):
    service, requests = gateway
    events = []
    listing = service.get_cover(
        DOC, "list", lambda path: events.append(("list", path)),
        on_source_changed=lambda: events.append(("list", "rebind")),
    )
    detail = service.get_cover(
        DOC, "detail", lambda path: events.append(("detail", path)),
        on_source_changed=lambda: events.append(("detail", "rebind")),
    )
    assert events == [("list", None), ("detail", None)]
    assert requests == []
    assert len(service._source_subscriptions[DOC]) == 2
    service.register(proof())

    # N2 documentary publication notifies both UI consumers WITHOUT a
    # background provider GET, image parsing, or a forced Search reselection.
    assert events == [
        ("list", None), ("detail", None),
        ("list", "rebind"), ("detail", "rebind"),
    ]
    assert not listing.still_current and not detail.still_current
    assert requests == []
    assert service.get_cover(DOC, "list", lambda _: None).source_uri == URL
    assert service.get_cover(DOC, "detail", lambda _: None).source_uri == URL
    assert requests == [("list", URL), ("detail", URL)]
    listing.cancel()
    detail.cancel()
    assert DOC not in service._source_subscriptions


def test_same_evidence_register_is_noop_then_changed_proof_notifies(gateway):
    service, requests = gateway
    service.register(proof())
    notified = []
    handle = service.get_cover(
        DOC, "detail", lambda _: None,
        on_source_changed=lambda: notified.append("changed"),
    )
    service.register(proof())  # Same source revision and proof: N2 HIT.
    assert notified == []
    service.register(proof(revision=2, url=OTHER))
    assert notified == ["changed"]
    assert not handle.still_current
    assert requests == [("detail", URL)]
    service.get_cover(DOC, "detail", lambda _: None)
    assert requests[-1] == ("detail", OTHER)


def test_source_replacement_stale_aba_callbacks_stay_invalid(gateway):
    service, _ = gateway
    service.register(proof())
    callbacks = []
    old = service.get_cover(
        DOC, "detail", lambda _: None,
        on_source_changed=lambda: callbacks.append("next"),
    )
    service.register(proof(revision=2, url=OTHER))
    service.register(proof(revision=3, url=URL))
    assert callbacks == ["next", "next"]
    assert not old.still_current
    assert service.get_cover(DOC, "detail", lambda _: None).source_uri == URL
    old.cancel()
    service.register(proof(revision=4, url=OTHER))
    assert callbacks == ["next", "next"]


def test_recycled_widget_cancel_and_foreign_edition_do_not_notify(gateway):
    service, _ = gateway
    events = []
    old = service.get_cover(
        DOC, "list", lambda _: None,
        on_source_changed=lambda: events.append("wrong"),
    )
    old.cancel()
    service.register(proof(ref=FOREIGN))
    service.register(proof())
    assert events == []
    assert DOC not in service._source_subscriptions


def test_one_disposed_observer_does_not_strand_other_consumer(gateway):
    service, _ = gateway
    notifications = []
    service.get_cover(
        DOC, "detail", lambda _: None,
        on_source_changed=lambda: (_ for _ in ()).throw(RuntimeError("old pane")),
    )
    other = service.get_cover(
        DOC, "list", lambda _: None,
        on_source_changed=lambda: notifications.append("list"),
    )
    service.register(proof())
    assert notifications == ["list"]
    other.cancel()


def test_partial_notice_then_complete_source_triggers_one_rebind(gateway):
    service, requests = gateway
    events = []
    service.register(proof(complete=False))
    handle = service.get_cover(
        DOC, "detail", lambda path: events.append(("media", path)),
        on_source_changed=lambda: events.append(("source", "changed")),
    )
    assert events == [("media", None)]
    assert requests == []
    service.register(proof(revision=2))
    assert events == [("media", None), ("source", "changed")]
    assert not handle.still_current


def test_core_readthrough_registration_notifies_subscriber_once(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    monkeypatch.setattr(
        loader, "load", lambda *_a, **_k: NS(cancel=lambda: None),
    )
    entered, release, seen = Event(), Event(), Event()
    calls = []

    def reader(ref):
        entered.set()
        assert release.wait(10)
        return proof(ref)

    svc = DocumentCoverService(loader, cached_source_reader=reader)
    try:
        initial = svc.get_cover(
            DOC, "detail", lambda _: None,
            on_source_changed=lambda: (calls.append("rebind"), seen.set()),
        )
        assert entered.wait(10)
        release.set()
        assert seen.wait(10)
        assert calls == ["rebind"]  # No duplicate worker+register event.
        assert not initial.still_current
    finally:
        release.set()
        svc.close()
        loader.close()


def test_close_clears_bounded_source_subscribers(gateway):
    service, _ = gateway
    old = service.get_cover(
        DOC, "list", lambda _: None, on_source_changed=lambda: None,
    )
    assert service._source_subscription_count == 1
    service.close()
    assert service._source_subscriptions == {}
    assert service._source_subscription_count == 0
    old.cancel()  # Cancellation after window close remains idempotent.
    old.cancel()
    assert service._source_subscription_count == 0
