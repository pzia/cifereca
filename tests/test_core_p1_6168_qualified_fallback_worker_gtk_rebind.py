"""#6168: actual worker qualification -> N2 CAS -> GTK DocumentRef rebind.

A physical N3 failure is never N1 evidence. Only the bounded worker-side
qualifier can authorize an already-attested, unique origin/catalogue fallback.
The old embedded request's N0 source URI must never be used for URL painting.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace as NS

import pytest

from cifereca import epub_metadata
from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import candidate
from cifereca.core.epub_cover_source import project_exact_embedded_cover
from cifereca.core.epub_cover_failure_qualification import (
    EpubCoverPhysicalAssessment,
)
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import (
    CoverSourceSnapshot, DocumentCoverService,
)
from tests.test_epub_cover_contract import _write_epub


ORIGIN = "https://public.example.org/exact-loan-cover.png"


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "exact.epub"
    contents = _write_epub(book, epub3=True)
    sha = hashlib.sha256(contents).hexdigest()
    scope = CoverDocumentScope(
        kind="placement", identity_id="alice",
        source_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="placement:alice",
    )
    embedded = project_exact_embedded_cover(
        scope, revision=7, artifact_sha256=sha,
        artifact_metadata=epub_metadata.inspect_epub(book),
    )
    assert embedded is not None
    origin = candidate(
        ORIGIN, tier="origin", source="verified-origin", evidence_id="edition",
    )
    assert origin is not None
    loader = CoverLoader(max_workers=2)
    svc = DocumentCoverService(loader)
    try:
        # Simulate a real N3 failure without depending on ZIP worker timings.
        # The *subsequent* dedicated qualifier still reads exact EPUB bytes.
        def n3_failed(_resource, *, source_path, callback, **_kwargs):
            assert source_path == book
            callback(None)
            return NS(cancel=lambda: None)
        monkeypatch.setattr(loader, "load_exact_epub", n3_failed)
        monkeypatch.setattr(
            loader, "load",
            lambda _url, callback, **_kw: (
                callback(None) or NS(cancel=lambda: None)
            ),
        )
        yield book, scope, embedded, origin, loader, svc
    finally:
        svc.close()
        loader.close()


def _register(state, *, member=None, candidates=None, revision=7):
    book, scope, embedded, origin, loader, svc = state
    current = replace(embedded, resource=replace(
        embedded.resource, source_revision=revision,
        **({"member": member} if member else {}),
    ))
    svc.register(CoverSourceSnapshot(
        scope=scope, revision=revision, embedded=current,
        verified_epub_path=book,
        candidates=(origin,) if candidates is None else candidates,
    ))
    return current


def test_definitive_worker_failure_rebinds_to_new_n2_origin_not_old_token(prepared):
    state = prepared
    _, scope, embedded, origin, loader, svc = state
    image = epub_metadata.cover_cache_dir() / embedded.resource.cache_name
    image.unlink(missing_ok=True)
    broken = _register(state, member="OPS/no-such-cover.png")
    notices, paths = [], []
    rebinding = Event()

    old = svc.get_cover(
        scope, "detail", paths.append,
        on_source_changed=lambda: (notices.append("source-change"), rebinding.set()),
    )
    assert old.source_uri == broken.candidate.url
    assert rebinding.wait(10)
    assert notices == ["source-change"]
    assert paths == [None]  # No URL bytes delivered with the old EPUB URI.
    assert not old.still_current
    chosen = svc.decisions.peek(scope, revision=7)
    assert (chosen.tier, chosen.uri) == ("origin", ORIGIN)

    fresh = svc.get_cover(scope, "detail", paths.append)
    assert fresh.source_uri == ORIGIN and fresh.still_current
    assert paths == [None, None]


def test_transient_n3_miss_cannot_promote_hot_origin(prepared):
    state = prepared
    _, scope, embedded, origin, loader, svc = state
    image = epub_metadata.cover_cache_dir() / embedded.resource.cache_name
    image.unlink(missing_ok=True)
    _register(state)
    notices = []
    old = svc.get_cover(
        scope, "detail", lambda _p: None,
        on_source_changed=lambda: notices.append("wrong-fallback"),
    )
    assert old.source_uri == embedded.candidate.url
    deadline = time.monotonic() + 10
    while svc._qualify_active and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not notices
    assert svc.decisions.peek(scope, revision=7).tier == "embedded"
    assert old.still_current


def test_old_generation_failure_must_not_launch_a_new_qualification(prepared):
    state = prepared
    _, scope, embedded, origin, loader, svc = state
    _register(state)
    callbacks = []
    def pending(_resource, *, source_path, callback, **_kw):
        callbacks.append(callback)
        return NS(cancel=lambda: None)
    loader.load_exact_epub = pending
    changed = Event()
    notified = []
    old = svc.get_cover(
        scope, "detail", lambda _path: None,
        on_source_changed=lambda: (notified.append("source"), changed.set()),
    )
    _register(state, revision=8)
    # #6325: a live subscriber MUST be told immediately about a newer
    # source revision, independently of the old EPUB materialization result.
    assert changed.wait(1)
    assert notified == ["source"]
    callbacks[0](None)
    assert not old.still_current
    # The late callback from the now-stale revision 7 must NOT issue a second
    # rebind or start its old physical fallback under revision 8.
    assert notified == ["source"]
    assert not svc._qualify_active
    assert svc.decisions.peek(scope, revision=8) is None


def test_single_flight_for_two_list_detail_failed_epub_requests(prepared, monkeypatch):
    import cifereca.gui.document_cover_service as module
    state = prepared
    _, scope, embedded, origin, loader, svc = state
    image = epub_metadata.cover_cache_dir() / embedded.resource.cache_name
    image.unlink(missing_ok=True)
    _register(state, member="OPS/no-such-cover.png")
    started, release = Event(), Event()
    notified_list, notified_detail = Event(), Event()
    calls = []
    def verifier(resource, *, source_path):
        calls.append((resource, source_path))
        started.set()
        release.wait(5)
        return EpubCoverPhysicalAssessment(
            resource, "definitive_unusable", "member_missing_or_ambiguous",
        )
    monkeypatch.setattr(module, "qualify_failed_epub_cover", verifier)
    first = svc.get_cover(
        scope, "list", lambda _p: None, on_source_changed=notified_list.set,
    )
    assert started.wait(5)
    second = svc.get_cover(
        scope, "detail", lambda _p: None, on_source_changed=notified_detail.set,
    )
    assert first.source_uri == second.source_uri
    assert len(calls) == 1  # One physical verifier for both N4 consumers.
    release.set()
    assert notified_list.wait(5)
    assert notified_detail.wait(5)
    assert svc.decisions.peek(scope, revision=7).uri == ORIGIN
    assert not first.still_current and not second.still_current
    assert len(calls) == 1


def test_gtk_rebind_stays_on_main_and_never_reuses_old_uri():
    source = (
        Path(__file__).resolve().parents[1] /
        "src/cifereca/gui/book_widgets.py"
    ).read_text(encoding="utf-8")
    begin = source.index("    def bind_document(")
    end = source.index("    def bind_url(", begin)
    body = source[begin:end]
    assert "on_source_changed=source_changed" in body
    assert "GLib.idle_add(" in body
    assert "rebind_after_qualified_source" in body
    assert "generation != self._generation" in body
    assert "self.bind_document(" in body
    assert "cover.bind_url(" not in body
    assert "self._url = request.source_uri" in body


def test_actual_coverview_bind_restarts_on_gtk_main_with_new_origin_token(prepared):
    """Replay the real GTK binding method with an event-loop queue, no GI."""
    from queue import Empty, Queue
    from tests.test_core_p1_6168_gtk_documentref_binding import method

    state = prepared
    _, scope, embedded, origin, loader, svc = state
    image = epub_metadata.cover_cache_dir() / embedded.resource.cache_name
    image.unlink(missing_ok=True)
    _register(state, member="OPS/no-such-cover.png")
    queue = Queue()
    events = []
    GLib = NS(
        PRIORITY_DEFAULT=0,
        idle_add=lambda fn, *args, **kw: queue.put((fn, args)),
    )
    bind = method("CoverView", "bind_document", namespace={"GLib": GLib})
    host = NS(
        _generation=0, _request=None, _url=None, _loader=None,
        _width=112, _height=168, _priority=0,
        show_loading=lambda: None,
        get_scale_factor=lambda: 1,
        _apply_result=lambda _generation, _loader, _path: False,
        _show_missing=lambda: events.append("missing"),
    )

    def reset():
        host._generation += 1
        if host._request is not None:
            host._request.cancel()
        host._request = None
        host._url = None

    host.reset = reset
    host.bind_document = lambda service, doc, **kw: bind(host, service, doc, **kw)
    host.bind_document(svc, scope, variant="detail")
    original = host._request
    assert original.source_uri == embedded.candidate.url

    deadline = time.monotonic() + 10
    while host._url != ORIGIN and time.monotonic() < deadline:
        try:
            fn, args = queue.get(timeout=0.2)
        except Empty:
            continue
        fn(*args)  # Exactly the GTK idle-main-context boundary.
    assert host._url == ORIGIN
    assert not original.still_current
    assert host._generation == 2
    assert host._request.source_uri == ORIGIN
    assert host._request.still_current


@pytest.mark.parametrize("second_state", ["pending", "cancelled", "waiting"])
def test_qualified_fallback_notifies_old_token_before_late_n3_callback(
        prepared, monkeypatch, second_state):
    import cifereca.gui.document_cover_service as module

    _, scope, embedded, origin, loader, svc = prepared
    image = epub_metadata.cover_cache_dir() / embedded.resource.cache_name
    image.unlink(missing_ok=True)
    _register(prepared, member="OPS/no-such-cover.png")
    callbacks, calls = [], []
    first_notified, second_notified = Event(), Event()
    release, started = Event(), Event()
    changes, paths = [], []

    def pending(_resource, *, source_path, callback, **_kw):
        callbacks.append(callback)
        return NS(cancel=lambda: None)

    def verifier(resource, *, source_path):
        calls.append(resource)
        started.set()
        assert release.wait(5)
        return EpubCoverPhysicalAssessment(
            resource, "definitive_unusable", "member_missing_or_ambiguous",
        )

    def notify_first():
        changes.append("list")
        first_notified.set()

    def notify_second():
        changes.append("detail")
        second_notified.set()

    monkeypatch.setattr(loader, "load_exact_epub", pending)
    monkeypatch.setattr(module, "qualify_failed_epub_cover", verifier)
    first = svc.get_cover(scope, "list", paths.append,
                          on_source_changed=notify_first)
    second = svc.get_cover(scope, "detail", paths.append,
                           on_source_changed=notify_second)
    assert len(callbacks) == 2
    try:
        callbacks[0](None)
        assert started.wait(5)
        if second_state == "cancelled":
            second.cancel()
        elif second_state == "waiting":
            callbacks[1](None)
    finally:
        release.set()
    assert first_notified.wait(5)
    if second_state != "cancelled":
        assert second_notified.wait(5)
    if second_state != "waiting":
        callbacks[1](None)
    assert sorted(changes) == (["list"] if second_state == "cancelled"
                               else ["detail", "list"])
    assert len(calls) == 1
    assert not first.still_current and not second.still_current
    assert svc.decisions.peek(scope, revision=7).uri == ORIGIN
    assert paths == ([None, None] if second_state == "waiting" else [None])
    fresh = svc.get_cover(scope, "detail", paths.append)
    assert fresh.source_uri == ORIGIN and fresh.still_current
