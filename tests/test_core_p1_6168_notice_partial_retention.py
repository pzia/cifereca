"""#6168/#4552: Notice C partial reads never tombstone a complete cover."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.core.notice_source_observation import (
    NoticeSourceKey, admit_source_notice,
)
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.notice_cover_n2_adapter import NoticeCoverN2Adapter


A = "https://covers.example.org/a.jpg"
B = "https://covers.example.org/b.jpg"
KEY = NoticeSourceKey(
    provider_id="bnf", visibility="public", identity_id=None,
    credential_id=None, account_id=None, branch_id="catalog",
    record_id="ark:123", edition_id="edition:1",
    manifestation_role="digital",
)


def observed(value, at: int, *, status="complete"):
    return admit_source_notice(
        key=KEY, parser_id="parser", parser_version="1",
        observed_at=at, status=status,
        raw_fields={"media": {"cover": value}},
    )


def publish(service, adapter, value, at, *, status="complete", complete=True):
    return adapter.register(
        service, observed(value, at, status=status),
        field_path=("media", "cover"),
        cover_inventory_complete=complete,
    )


@pytest.fixture
def bound(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    loader = CoverLoader(max_workers=2)
    loads = []

    def load(uri, callback, **kwargs):
        loads.append(uri)
        return SimpleNamespace(cancel=lambda: None)

    monkeypatch.setattr(loader, "load", load)
    service = DocumentCoverService(loader)
    adapter = NoticeCoverN2Adapter()
    try:
        yield service, adapter, loads
    finally:
        adapter.close()
        service.close()
        loader.close()


def test_partial_and_malformed_source_do_not_tombstone_complete_cover(bound):
    service, adapter, loads = bound
    document = publish(service, adapter, A, 10)
    events = []
    old = service.get_cover(
        document, "detail", lambda _: None,
        on_source_changed=lambda: events.append("changed"),
    )
    before = adapter._published[document].revision
    assert old.source_uri == A

    assert publish(service, adapter, B, 20, status="partial") == document
    assert publish(service, adapter, "http://covers.example.org/unsafe.jpg", 21) == document
    assert publish(service, adapter, B, 22, complete=False) == document
    assert events == []
    assert old.still_current
    assert adapter._published[document].revision == before
    assert adapter._published[document].observed_at == 22
    assert service.get_cover(document, "detail", lambda _: None).source_uri == A
    assert loads == [A, A]


def test_out_of_order_replay_cannot_revoke_newest_complete_source(bound):
    service, adapter, _ = bound
    document = publish(service, adapter, A, 10)
    events = []
    old = service.get_cover(
        document, "detail", lambda _: None,
        on_source_changed=lambda: events.append("refresh"),
    )
    # An incomplete source at t=20 does not invalidate A, but prevents
    # delayed complete t=15 from racing the newer observation.
    publish(service, adapter, B, 20, status="partial")
    publish(service, adapter, B, 15)
    assert old.still_current
    assert events == []
    assert adapter._published[document].revision == 1

    publish(service, adapter, B, 21)
    assert events == ["refresh"]
    assert not old.still_current
    new = service.get_cover(document, "detail", lambda _: None)
    assert new.source_uri == B
    rev = adapter._published[document].revision

    publish(service, adapter, A, 11)
    assert new.still_current
    assert events == ["refresh"]
    assert adapter._published[document].revision == rev
    assert service.get_cover(document, "detail", lambda _: None).source_uri == B


def test_first_partial_remains_pending_until_complete_source_arrives(bound):
    service, adapter, loads = bound
    document = publish(service, adapter, B, 1, status="partial")
    events = []
    first = service.get_cover(
        document, "list", lambda _: None,
        on_source_changed=lambda: events.append("refresh"),
    )
    assert first.source_uri is None
    publish(service, adapter, A, 2, status="partial")
    assert events == ["refresh"]
    assert service.get_cover(document, "detail", lambda _: None).source_uri is None
    publish(service, adapter, B, 3)
    assert events == ["refresh", "refresh"]
    assert not first.still_current
    assert service.get_cover(document, "detail", lambda _: None).source_uri == B
    assert loads == [B]


def test_complete_missing_is_real_change_but_partial_cannot_erase_it(bound):
    service, adapter, _ = bound
    document = publish(service, adapter, A, 1)
    events = []
    old = service.get_cover(
        document, "detail", lambda _: None,
        on_source_changed=lambda: events.append("update"),
    )
    publish(service, adapter, None, 2)
    assert not old.still_current
    assert events == ["update"]
    empty = service.get_cover(document, "detail", lambda _: None)
    assert empty.source_uri is None
    missing_revision = adapter._published[document].revision
    publish(service, adapter, B, 3, status="partial")
    assert adapter._published[document].revision == missing_revision
    assert events == ["update"]
    assert service.get_cover(document, "detail", lambda _: None).source_uri is None


def test_same_observation_later_advances_watermark_without_n2_invalidation(bound):
    service, adapter, _ = bound
    document = publish(service, adapter, A, 5)
    old = service.get_cover(document, "detail", lambda _: None)
    publish(service, adapter, A, 9)
    assert old.still_current
    assert adapter._published[document].revision == 1
    assert adapter._published[document].observed_at == 9
    publish(service, adapter, B, 7)
    assert old.still_current
    assert service.get_cover(document, "detail", lambda _: None).source_uri == A
