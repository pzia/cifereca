"""#6168: independent documentary N2 metrics, never physical N3 facts."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import candidate
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import (
    CoverSourceSnapshot, DocumentCoverService,
)


URL = "https://cover.example.org/known.png"
URL2 = "https://cover.example.org/updated.png"
DOCUMENT = CoverDocumentScope(
    kind="public_catalogue", identity_id=None,
    provider_id="bnf", credential_id="__public__",
    source_key="notice-c:exact-edition",
)


def proof(revision=1, url=URL, *, complete=True):
    return CoverSourceSnapshot(
        scope=DOCUMENT, revision=revision,
        candidates=(candidate(
            url, tier="catalog", source="notice-c",
            evidence_id=DOCUMENT.source_key,
        ),),
        snapshot_complete=complete,
    )


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loader = CoverLoader(max_workers=2)
    calls = []
    monkeypatch.setattr(
        loader, "load", lambda uri, callback, **kwargs: (
            calls.append(uri) or NS(cancel=lambda: None)
        ),
    )
    service = DocumentCoverService(loader)
    try:
        yield service, loader, calls
    finally:
        service.close()
        loader.close()


def test_n2_counts_one_hit_or_miss_per_visible_document_request(prepared):
    service, loader, calls = prepared
    assert service.stats()["n2_registered_documents"] == 0
    first = service.get_cover(DOCUMENT, "detail", lambda _: None)
    assert first.source_uri is None
    assert service.stats()["n2_miss"] == 1
    assert service.stats()["n2_source_unknown"] == 1
    assert service.stats()["n2_hit"] == 0
    assert calls == []  # N2 absence cannot trigger an arbitrary URL GET.

    service.register(proof())
    assert service.stats()["n2_register_new"] == 1
    cold = service.get_cover(DOCUMENT, "detail", lambda _: None)
    assert cold.source_uri == URL
    assert service.stats()["n2_miss"] == 2
    assert service.stats()["n2_resolution_miss"] == 1
    hot = service.get_cover(DOCUMENT, "detail", lambda _: None)
    assert hot.source_uri == URL
    assert service.stats()["n2_hit"] == 1
    assert service.stats()["n2_miss"] == 2
    assert calls == [URL, URL]
    assert service.stats()["n2_registered_documents"] == 1
    assert "bnf" not in str(service.stats())


def test_public_n2_warm_hit_has_no_viewer_partition(prepared):
    service, _, calls = prepared
    service.register(proof())
    service.get_cover(DOCUMENT, "detail", lambda _: None)
    source = service.get_cover(DOCUMENT, "detail", lambda _: None)
    assert source.source_uri == URL
    assert service.stats()["n2_hit"] == 1
    service.register(proof())
    assert service.stats()["n2_register_reused"] == 1
    assert service.get_cover(DOCUMENT, "detail", lambda _: None).source_uri == URL
    assert service.stats()["n2_hit"] == 2
    assert service.stats()["n2_register_changed"] == 0
    assert calls == [URL, URL, URL]


def test_source_changed_invalidates_n2_only_not_n3_physical_path(prepared):
    service, loader, calls = prepared
    service.register(proof())
    original = service.get_cover(DOCUMENT, "detail", lambda _: None)
    assert original.source_uri == URL
    physical = loader._path(URL)
    service.register(proof(2, URL2))
    assert service.stats()["n2_register_changed"] == 1
    assert not original.still_current
    newer = service.get_cover(DOCUMENT, "detail", lambda _: None)
    assert newer.source_uri == URL2
    assert loader._path(URL) == physical
    assert service.stats()["n2_miss"] == 2
    assert service.stats()["n2_hit"] == 0

    service.register(proof(3, URL))
    assert service.get_cover(DOCUMENT, "detail", lambda _: None).source_uri == URL
    assert service.stats()["n2_register_changed"] == 2
    assert not original.still_current
    assert loader._path(URL) == physical
    assert calls == [URL, URL2, URL]


def test_pending_source_is_not_misreported_as_documentary_missing(prepared):
    service, _, calls = prepared
    service.register(proof(complete=False))
    request = service.get_cover(DOCUMENT, "detail", lambda _: None)
    assert request.source_uri is None
    assert service.decisions.peek(DOCUMENT, revision=1).status == "pending"
    service.get_cover(DOCUMENT, "detail", lambda _: None)
    assert service.stats()["n2_miss"] == 1
    assert service.stats()["n2_hit"] == 1
    assert not calls


def test_invalid_registration_and_invalid_request_do_not_pollute_counters(prepared):
    service, _, _ = prepared
    with pytest.raises(TypeError):
        service.register("unsafe")
    with pytest.raises(ValueError):
        service.get_cover(DOCUMENT, "wrong", lambda _: None)
    assert service.stats()["n2_register_new"] == 0
    assert service.stats()["n2_miss"] == 0

    service.register(proof())
    service.register(proof())
    with pytest.raises(ValueError, match="changed cover evidence requires revision"):
        service.register(proof(url=URL2))
    assert service.stats()["n2_register_new"] == 1
    assert service.stats()["n2_register_reused"] == 1
    assert service.stats()["n2_register_changed"] == 0
