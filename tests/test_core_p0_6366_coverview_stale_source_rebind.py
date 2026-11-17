"""#6366: stale N3 owner must not relaunch N2 after a source-change idle.

Replays the REAL CoverView.bind_document without GTK, with a genuine N2
DocumentCoverService and a fake public C source notification. It distinguishes
a new N2 revision (old CAS token revoked, owner still valid) from an expired
Search/Library/Loan/History/Recent/Recommendation owner lease.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from tests.test_core_p1_6168_gtk_documentref_binding import method


DOCUMENT = canonical_cover_document_ref("edition:bound-c-source-revision")
URL = "https://covers.example.org/n2-owner-c.jpg"


def _rebind_fixture(monkeypatch):
    loader = CoverLoader(max_workers=1)
    service = DocumentCoverService(loader)
    idle, callbacks, issued, events = [], [], [], []
    glib = NS(
        PRIORITY_DEFAULT=0,
        idle_add=lambda fn, *args, **_kw: idle.append((fn, args)),
    )
    bind = method("CoverView", "bind_document", namespace={"GLib": glib})

    def fake_get_cover(document, variant, received, **kwargs):
        assert document == DOCUMENT and variant == "detail"
        callbacks.append(kwargs["on_source_changed"])
        ticket = NS(
            active=True, still_current=False, source_uri=URL,
            cancel=lambda: None,
        )
        issued.append(ticket)
        return ticket

    monkeypatch.setattr(service, "get_cover", fake_get_cover)

    host = NS(
        _generation=0, _request=None, _url=None, _loader=None,
        _width=115, _height=170, _priority=0, _document_check=None,
        show_loading=lambda: events.append("loading"),
        _show_missing=lambda: events.append("missing"),
        get_scale_factor=lambda: 1,
        _apply_result=lambda *_args: False,
    )

    def reset():
        previous = host._request
        if previous is not None:
            previous.cancel()
        host._generation += 1
        host._request = None
        host._url = None
        events.append("reset")

    host.reset = reset
    host.bind_document = lambda svc, doc, **kwargs: bind(
        host, svc, doc, **kwargs,
    )
    return loader, service, host, idle, callbacks, issued, events


@pytest.mark.parametrize("change", ["revoked", "raised"])
def test_stale_owner_source_notification_never_launches_new_n2(
    monkeypatch, change,
):
    loader, service, host, idle, callbacks, issued, events = (
        _rebind_fixture(monkeypatch)
    )
    try:
        state = {"current": True}
        def check():
            if change == "raised" and not state["current"]:
                raise RuntimeError("previous source no longer exists")
            return state["current"]

        host.bind_document(
            service, DOCUMENT, variant="detail", is_current=check,
        )
        assert len(issued) == len(callbacks) == 1
        original = issued[0]
        state["current"] = False
        callbacks[0]()
        assert len(idle) == 1
        fn, args = idle.pop()
        assert fn(*args) is False
        # A delayed N2 refresh is a documentary event, not proof that the
        # old GTK row/selection still owns the document.
        assert len(issued) == len(callbacks) == 1
        assert host._request is None and host._url is None
        assert host._generation == 2
        assert events[-2:] == ["reset", "missing"]
        assert original.still_current is False

        # The obsolete callback cannot restart N2 after its owner vanished.
        callbacks[0]()
        fn, args = idle.pop()
        assert fn(*args) is False
        assert len(issued) == 1
        assert events[-2:] == ["reset", "missing"]
    finally:
        service.close()
        loader.close()


def test_current_owner_may_refresh_even_though_old_n2_cas_is_stale(monkeypatch):
    loader, service, host, idle, callbacks, issued, events = (
        _rebind_fixture(monkeypatch)
    )
    try:
        host.bind_document(
            service, DOCUMENT, variant="detail",
            is_current=lambda: True,
        )
        assert len(issued) == 1 and issued[0].still_current is False
        callbacks[0]()
        fn, args = idle.pop()
        assert fn(*args) is False
        assert len(issued) == 2
        assert len(callbacks) == 2
        assert host._request is issued[-1]
        assert host._generation == 2
        assert "missing" not in events
    finally:
        service.close()
        loader.close()


def test_newer_gtk_bind_ignores_old_source_idle_without_clearing_image(
    monkeypatch,
):
    loader, service, host, idle, callbacks, issued, events = (
        _rebind_fixture(monkeypatch)
    )
    try:
        host.bind_document(
            service, DOCUMENT, variant="detail",
            is_current=lambda: False,
        )
        assert len(issued) == 1
        callbacks[0]()
        # Another row was bound before GLib got to the old source event.
        host._generation += 1
        original_request = host._request
        fn, args = idle.pop()
        assert fn(*args) is False
        assert host._request is original_request
        assert host._generation == 2
        assert len(issued) == 1
        assert "missing" not in events
    finally:
        service.close()
        loader.close()
