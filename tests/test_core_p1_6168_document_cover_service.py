"""#6168: DocumentRef->N2 decision->N3 bytes->GTK callback, not URL->book."""
from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from threading import Event

import pytest

from cifereca import epub_metadata
from cifereca.core.book_cover_cache import CoverDecisionCache
from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import candidate
from cifereca.core.epub_cover_source import project_exact_embedded_cover
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import (
    CoverSourceSnapshot, DocumentCoverService,
)
from tests.test_epub_cover_contract import _write_epub


ORIGIN = "https://public.example.org/edition-cover.jpg"
CATALOG = "https://catalog.example.org/edition-cover.jpg"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "source.epub"
    raw = _write_epub(book, epub3=True)
    sha = hashlib.sha256(raw).hexdigest()
    metadata = epub_metadata.inspect_epub(book)
    scope = CoverDocumentScope(
        kind="placement", identity_id="alice",
        source_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha,
        placement_id="placement:alice-exact",
    )
    embedded = project_exact_embedded_cover(
        scope, revision=7, artifact_sha256=sha, artifact_metadata=metadata,
    )
    assert embedded is not None
    physical = epub_metadata.cover_cache_dir() / embedded.resource.cache_name
    origin = candidate(
        ORIGIN, tier="origin", source="publisher", evidence_id="edition",
    )
    catalog = candidate(
        CATALOG, tier="catalog", source="catalogue", evidence_id="notice",
    )
    loader = CoverLoader(max_workers=2)
    decisions = CoverDecisionCache()
    service = DocumentCoverService(loader, decisions=decisions)
    try:
        yield {
            "book": book, "scope": scope, "embedded": embedded,
            "physical": physical, "origin": origin, "catalog": catalog,
            "loader": loader, "service": service, "decisions": decisions,
        }
    finally:
        service.close()
        loader.close()


def _register(book, *, revision=7, embedded=True, complete=True,
              candidates=None):
    current = book["embedded"]
    if embedded and revision != 7:
        current = replace(current, resource=replace(
            current.resource, source_revision=revision,
        ))
    snapshot = CoverSourceSnapshot(
        scope=book["scope"], revision=revision,
        candidates=tuple(candidates if candidates is not None else (
            book["catalog"], book["origin"],
        )),
        embedded=current if embedded else None,
        verified_epub_path=book["book"] if embedded else None,
        snapshot_complete=complete,
    )
    book["service"].register(snapshot)
    return snapshot


def test_n2_prefers_epub_even_when_n3_absent_and_url_image_is_hot(prepared):
    b = prepared
    b["physical"].unlink()
    b["loader"]._path(ORIGIN).write_bytes(PNG)  # warm N3 remote is irrelevant.
    _register(b)
    events, result = [], []
    ready = Event()
    b["decisions"].subscribe(b["scope"], events.append)
    b["service"].get_cover(
        b["scope"], "detail",
        lambda path: (result.append(path), ready.set()),
    )
    assert ready.wait(10)
    assert result == [b["physical"]]
    assert [event.kind for event in events] == ["refresh", "resolved"]
    assert events[-1].decision.tier == "embedded"
    assert b["decisions"].peek(b["scope"], revision=7).uri == b["physical"].as_uri()
    assert b["loader"].stats()["download_ok"] == 0

    # A second detail request must reuse N2 and the N3 hit, not re-resolve.
    another = []
    b["service"].get_cover(b["scope"], "detail", another.append)
    assert another == [b["physical"]]
    assert [event.kind for event in events] == ["refresh", "resolved"]


def test_same_document_list_and_detail_get_exact_same_n2_source(prepared):
    b = prepared
    _register(b)
    events, listing, detail = [], [], []
    list_ready = Event()
    b["decisions"].subscribe(b["scope"], events.append)
    b["service"].get_cover(
        b["scope"], "list",
        lambda path: (listing.append(path), list_ready.set()),
        width=180, height=270,
    )
    b["service"].get_cover(b["scope"], "detail", detail.append)
    assert list_ready.wait(10)
    assert len(listing) == len(detail) == 1
    assert listing[0] is not None and detail == [b["physical"]]
    assert len([e for e in events if e.kind == "resolved"]) == 1
    assert b["decisions"].peek(b["scope"], revision=7).tier == "embedded"


def test_catalogue_without_embedded_proof_can_use_its_own_n3_hit(prepared):
    b = prepared
    _register(b, embedded=False, candidates=(b["catalog"],))
    cached = b["loader"]._path(CATALOG)
    cached.write_bytes(PNG)
    result = []
    b["service"].get_cover(b["scope"], "detail", result.append)
    assert result == [cached]
    assert b["decisions"].peek(b["scope"], revision=7).tier == "catalog"
    assert b["loader"].stats()["download_ok"] == 0


def test_document_reference_never_resolves_a_second_placement_from_same_image(prepared):
    b = prepared
    _register(b)
    other_scope = replace(b["scope"], identity_id="bob", placement_id="other")
    result = []
    b["service"].get_cover(other_scope, "detail", result.append)
    assert result == [None]
    assert b["decisions"].peek(other_scope, revision=7) is None


def test_replacement_of_evidence_rejects_old_async_gtk_completion(prepared, monkeypatch):
    b = prepared
    b["physical"].unlink()
    _register(b)
    started, release, delivered = Event(), Event(), Event()
    original_restore = b["loader"]._epub_physical._restore
    original_load = b["loader"].load_exact_epub

    def gated(resource, path):
        started.set()
        assert release.wait(10)
        return original_restore(resource, path)

    def witness(resource, *, callback, **kwargs):
        def tagged(path):
            callback(path)
            delivered.set()
        return original_load(resource, callback=tagged, **kwargs)

    monkeypatch.setattr(b["loader"]._epub_physical, "_restore", gated)
    monkeypatch.setattr(b["loader"], "load_exact_epub", witness)
    result = []
    b["service"].get_cover(b["scope"], "detail", result.append)
    assert started.wait(10)
    _register(b, revision=8, embedded=False, candidates=(b["origin"],))
    try:
        release.set()
        assert delivered.wait(10)
        assert result == []  # Old scope+revision does not survive N2 invalidation.
        assert b["decisions"].peek(b["scope"], revision=7) is None
    finally:
        release.set()


def test_physical_failure_does_not_invent_documentary_missing(prepared):
    b = prepared
    b["physical"].unlink()
    b["book"].write_bytes(b["book"].read_bytes() + b"changed-source")
    _register(b)
    finished, outputs = Event(), []
    b["service"].get_cover(
        b["scope"], "detail",
        lambda path: (outputs.append(path), finished.set()),
    )
    assert finished.wait(10)
    assert outputs == [None]
    # N3 failed to prove bytes. N2 still reflects the original attested
    # source; a bounded N1 requalification/fallback remains pending.
    assert b["decisions"].peek(b["scope"], revision=7).tier == "embedded"
    assert b["loader"].stats()["download_ok"] == 0


def test_incomplete_snapshot_never_downloads_or_selects_catalogue(prepared):
    b = prepared
    _register(b, complete=False)
    result = []
    b["service"].get_cover(b["scope"], "detail", result.append)
    assert result == [None]
    assert b["decisions"].peek(b["scope"], revision=7).status == "pending"
    assert b["loader"].stats()["download_ok"] == 0


def test_duplicate_exact_registration_is_idempotent_and_changed_same_rev_refused(prepared):
    b = prepared
    first = _register(b)
    assert b["service"].register(first) is None
    with pytest.raises(ValueError, match="revision"):
        b["service"].register(
            replace(first, candidates=(b["catalog"],)),
        )
    with pytest.raises(ValueError, match="stale"):
        b["service"].register(replace(first, revision=6))


def test_cross_scope_embedded_resource_cannot_be_registered(prepared):
    b = prepared
    original = _register(b)
    with pytest.raises(ValueError):
        CoverSourceSnapshot(
            scope=replace(b["scope"], identity_id="bob"),
            revision=7, embedded=b["embedded"], verified_epub_path=b["book"],
        )
    with pytest.raises(ValueError):
        CoverSourceSnapshot(
            scope=b["scope"], revision=7,
            embedded=b["embedded"], verified_epub_path=Path("book.epub"),
        )
    assert original.embedded.resource.scope == b["scope"]


def test_cancelled_document_request_cannot_paint(prepared, monkeypatch):
    b = prepared
    b["physical"].unlink()
    _register(b)
    started, release = Event(), Event()
    original = b["loader"]._epub_physical._restore

    def gated(source, path):
        started.set()
        assert release.wait(10)
        return original(source, path)

    monkeypatch.setattr(b["loader"]._epub_physical, "_restore", gated)
    output = []
    handle = b["service"].get_cover(b["scope"], "detail", output.append)
    assert started.wait(10)
    handle.cancel()
    release.set()
    # Wait for N3 to finish; the cancelled outer handle must never paint.
    finished = b["loader"]._epub_physical.request(
        b["embedded"].resource, source_path=b["book"],
    )
    assert finished.result(timeout=10) == b["physical"].as_uri()
    assert output == []
