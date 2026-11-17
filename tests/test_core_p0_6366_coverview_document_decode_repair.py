"""#6366: GDK failure repairs the exact N2 binding, not a raw N3 URL.

Replay both real GTK methods with a synthetic GDK failure and the shared
DocumentCoverService type. No GTK, provider requests or actual image decode.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest

from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from tests.test_core_p1_6168_gtk_documentref_binding import method
from tests.test_core_p1_6168_gtk_texture_applied_hook import (
    env, production_method,
)


DOC = canonical_cover_document_ref("edition:document-decode-repair")
URI = "https://covers.example.org/attested.jpg"
FILE = Path("/tmp/synthetic-corrupt-n3.png")


@pytest.mark.parametrize("variant", ["list", "detail"])
@pytest.mark.parametrize("change", ["none", "owner", "cas"])
def test_real_document_bind_decode_repair_is_scoped_and_bounded(
    monkeypatch, variant, change,
):
    loader = CoverLoader(max_workers=1)
    service = DocumentCoverService(loader)
    host, _fake_loader, picture, events, _old_apply = env()
    calls, idle, received, handles = [], [], [], []
    state = {"owner": True}

    def get_cover(document, kind, callback, **kwargs):
        calls.append((document, kind, kwargs))
        received.append(callback)
        handle = NS(
            active=True, still_current=True, source_uri=URI,
            cancel=lambda: events.append("cancelled"),
        )
        handles.append(handle)
        return handle

    def decode(_file):
        if change == "owner":
            state["owner"] = False
        elif change == "cas":
            handles[-1].still_current = False
        raise ValueError("synthetic undecodable texture")

    monkeypatch.setattr(service, "get_cover", get_cover)
    monkeypatch.setattr(loader, "invalidate",
                        lambda path, **kw: events.append(("invalidated", path)))
    monkeypatch.setattr(loader, "note_ui_stale", lambda: events.append("stale"))
    monkeypatch.setattr(loader, "note_ui_failed", lambda: events.append("failed"))
    host._request = None
    host.get_scale_factor = lambda: 1
    host.show_loading = lambda: events.append("loading")
    reset = method("CoverView", "reset")
    host.reset = lambda: reset(host)
    host._start_cover_request = lambda **kwargs: events.append(("RAW", kwargs))
    glib = NS(
        PRIORITY_DEFAULT=0,
        idle_add=lambda fn, *args, **kw: idle.append((fn, args)),
    )
    bind = method("CoverView", "bind_document", namespace={"GLib": glib})
    apply = production_method("_apply_result", namespace={
        "time": time,
        "Gdk": NS(Texture=NS(new_from_file=decode)),
        "Gio": NS(File=NS(new_for_path=lambda path: path)),
    })
    host._apply_result = lambda *args: apply(host, *args)
    host.bind_document = lambda svc, doc, **kw: bind(host, svc, doc, **kw)
    observer = lambda *_args: events.append("paint receipt")
    try:
        host.bind_document(
            service, DOC, variant=variant, priority=0,
            is_current=lambda: state["owner"],
            on_texture_applied=observer,
        )
        first_generation = host._generation
        received[0](FILE)
        fn, args = idle.pop(0)
        assert fn(*args) is False
        assert ("invalidated", FILE) in events
        assert "paint receipt" not in events
        if change == "none":
            assert len(calls) == 2
            assert calls[0][0:2] == calls[1][0:2] == (DOC, variant)
            assert calls[0][2]["width"] == calls[1][2]["width"]
            assert calls[0][2]["height"] == calls[1][2]["height"]
            assert host._generation == first_generation + 1
            assert host._request is handles[-1]
            assert host._on_texture_applied is observer
            assert host._decode_retry is True
            assert "cancelled" in events
            # A repeated decode failure in the repaired binding is final.
            received[1](FILE)
            fn, args = idle.pop(0)
            assert fn(*args) is False
            assert len(calls) == 2
            assert "missing" in events
        else:
            assert len(calls) == 1
            assert "stale" in events and "missing" in events
            assert host._generation == first_generation
        assert not any(isinstance(e, tuple) and e[0] == "RAW" for e in events)
        assert "paint receipt" not in events
        assert picture.paintable is None
    finally:
        service.close()
        loader.close()


def test_legacy_url_keeps_its_single_forced_decode_retry():
    host, loader, _picture, events, apply = env(fail_decode=True)
    host._document_check = None
    host._decode_retry = False
    host._start_cover_request = lambda **kwargs: events.append(("RAW", kwargs))
    assert apply(host, host._generation, loader, FILE) is False
    assert ("RAW", {"force": True}) in events
    assert host._decode_retry is True
    assert apply(host, host._generation, loader, FILE) is False
    assert events.count(("RAW", {"force": True})) == 1
    assert "missing" in events


def test_document_without_repair_callback_never_downgrades_to_url():
    host, loader, _picture, events, apply = env(fail_decode=True)
    host._document_check = lambda: True
    host._decode_retry = False
    host._document_decode_retry = None
    host._start_cover_request = lambda **kwargs: events.append(("RAW", kwargs))
    assert apply(host, host._generation, loader, FILE) is False
    assert "missing" in events
    assert not any(isinstance(e, tuple) and e[0] == "RAW" for e in events)


@pytest.mark.parametrize("documentary", [True, False])
def test_raw_url_bind_cannot_coalesce_with_pending_document_request(documentary):
    host, loader, _picture, events, _apply = env()
    host._url = URI
    host._priority = 10
    host._document_check = (lambda: True) if documentary else None
    host._document_decode_retry = (lambda: None) if documentary else None
    host.spinner.get_visible = lambda: True
    host._request = NS(cancel=lambda: events.append("cancelled"))
    reset = method("CoverView", "reset")
    host.reset = lambda: reset(host)
    host._start_cover_request = lambda **kw: events.append(("RAW", kw))
    bind_url = method("CoverView", "bind_url")
    initial_generation = host._generation
    bind_url(host, loader, URI, priority=10)
    if documentary:
        # Same physical URI, different authority. Reuse of the old
        # DocumentRef's pending request/observer would mix binding modes.
        assert host._generation == initial_generation + 1
        assert host._document_check is None
        assert host._document_decode_retry is None
        assert "cancelled" in events
        assert ("RAW", {"force": False}) in events
    else:
        # Pure URL bindings retain the pre-existing in-flight optimization.
        assert host._generation == initial_generation
        assert "cancelled" not in events
        assert ("RAW", {"force": False}) not in events
