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
        _register(b, revision=6)


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


def test_loan_trace_uses_one_live_receipt_per_render_variant(prepared):
    from PIL import Image

    from cifereca.gui.cover_preview import preview_path
    from tests.test_core_p1_6168_gtk_texture_applied_hook import env

    b = prepared
    service, loader = b["service"], b["loader"]
    document = CoverDocumentScope(
        kind="loan", identity_id="alice", source_key="loan:exact-edition",
        provider_id="library", credential_id="reader-card", account_id="account",
        loan_id="loan-1",
    )
    snapshot = CoverSourceSnapshot(document, revision=1, candidates=(b["catalog"],))
    service.register(snapshot)
    source = loader._path(CATALOG)
    image = Image.new("RGB", (32, 48), "red")
    image.save(source, format="PNG")
    image.save(preview_path(source, loader.preview_root, 120, 180), format="PNG")

    listed, detailed = [], []
    preview_ready = Event()
    listing = service.get_cover(
        document, "list", lambda path: (listed.append(path), preview_ready.set()),
        width=120, height=180,
    )
    assert preview_ready.wait(10)
    assert listed[0].parent == loader.preview_root
    detail = service.get_cover(document, "detail", detailed.append)
    state = service.cover_trace_snapshot(document)
    assert state.status == "selected" and state.media_cached and state.preview_cached
    assert not state.list_painted and not state.detail_painted
    assert not listing.note_texture_applied(detailed[0])

    host, gtk_loader, picture, _calls, apply = env()
    host._request = listing
    host._document_check = lambda: listing.still_current
    apply(host, 4, gtk_loader, listed[0])
    assert picture.paintable is not None
    state = service.cover_trace_snapshot(document)
    assert state.list_painted and not state.detail_painted
    assert not listing.note_texture_applied(listed[0])
    assert detail.note_texture_applied(detailed[0])
    state = service.cover_trace_snapshot(document)
    assert state.list_painted and state.detail_painted and state.preview_cached


def test_exact_epub_paint_receipt_is_revoked_even_if_image_path_is_reused(prepared):
    b = prepared
    service = b["service"]
    _register(b)
    received = []
    first = service.get_cover(b["scope"], "detail", received.append)
    assert received == [b["physical"]]
    assert service.cover_trace_snapshot(b["scope"]).media_cached

    _register(b, revision=8)
    stale = service.cover_trace_snapshot(b["scope"])
    assert stale.invalidated
    assert not first.note_texture_applied(received[0])

    second = service.get_cover(b["scope"], "detail", received.append)
    assert received == [b["physical"], b["physical"]]
    second.cancel()
    assert not second.note_texture_applied(received[1])
    assert not service.cover_trace_snapshot(b["scope"]).detail_painted


def test_trace_subscriptions_follow_snapshot_eviction_and_close(prepared):
    b = prepared
    loader = b["loader"]
    service = DocumentCoverService(loader, max_documents=1)
    scope = replace(b["scope"], identity_id="bob")
    try:
        first = CoverSourceSnapshot(b["scope"], revision=1)
        second = CoverSourceSnapshot(scope, revision=1)
        service.register(first)
        service.get_cover(first.scope, "list", lambda path: None)
        assert service.cover_trace_snapshot(first.scope).status == "missing"
        service.register(second)
        assert service.cover_trace_snapshot(first.scope) is None
        service.get_cover(second.scope, "detail", lambda path: None)
        assert service.decisions.stats()["subscribers"] == 1
    finally:
        service.close()
    assert service.decisions.stats()["subscribers"] == 0


def test_audit_current_n2_and_n3_are_independent(prepared, monkeypatch):
    b = prepared
    service, loader, decisions = b["service"], b["loader"], b["decisions"]
    snapshot = _register(b)
    observe = service.observe_cover_for_audit
    assert observe(b["scope"]).n2_status == "unknown"
    loader._path(ORIGIN).write_bytes(PNG)
    token = decisions.begin(b["scope"], revision=7)
    assert decisions.resolve_and_publish(
        token, snapshot.candidates + (snapshot.embedded.candidate,),
        snapshot_complete=True,
    )
    def forbidden(*args, **kwargs):
        raise AssertionError("audit must never resolve or populate covers")
    monkeypatch.setattr(loader, "load", forbidden)
    monkeypatch.setattr(loader, "load_exact_epub", forbidden)
    monkeypatch.setattr(decisions, "resolve_and_publish", forbidden)
    before = service.stats()
    assert observe(b["scope"], expected_revision=8).n2_status == "unknown"
    valid = observe(b["scope"], expected_revision=7)
    assert (valid.n2_status, valid.n2_tier, valid.n3_status) == (
        "selected", "embedded", "digest_valid",
    )
    b["physical"].write_bytes(b["physical"].read_bytes() + b"wrong digest")
    assert observe(b["scope"]).n3_status == "not_validated"
    b["physical"].unlink()
    absent = observe(b["scope"])
    assert (absent.n2_status, absent.n2_tier, absent.n3_status) == (
        "selected", "embedded", "not_validated",
    )
    assert observe(replace(b["scope"], identity_id="bob")).n2_status == "unknown"
    assert not b["physical"].exists()
    # Neither a different hot URL nor old N0 observation can stand in for N3.
    assert decisions.observe(b["scope"], revision=7)[0] == token
    assert service.stats() == before


@pytest.mark.parametrize("complete,status,physical", [
    (True, "missing", "not_applicable"),
    (False, "pending", "unknown"),
])
def test_audit_no_selection_never_probes_media(
    prepared, monkeypatch, complete, status, physical,
):
    b = prepared
    _register(b, embedded=False, candidates=(), complete=complete)
    token = b["decisions"].begin(b["scope"], revision=7)
    assert b["decisions"].resolve_and_publish(token, (), snapshot_complete=complete)
    def forbidden(*args, **kwargs):
        raise AssertionError("no selected URI to probe")
    monkeypatch.setattr(b["loader"], "probe_media", forbidden)
    observed = b["service"].observe_cover_for_audit(b["scope"])
    assert (observed.n2_status, observed.n3_status) == (status, physical)


def test_audit_rejects_generation_change_during_physical_probe(prepared, monkeypatch):
    b = prepared
    _register(b, embedded=False, candidates=(b["catalog"],))
    b["loader"]._path(CATALOG).write_bytes(PNG)
    token = b["decisions"].begin(b["scope"], revision=7)
    assert b["decisions"].resolve_and_publish(
        token, (b["catalog"],), snapshot_complete=True,
    )
    assert b["service"].observe_cover_for_audit(b["scope"]).n3_status == "signature_valid"
    probe = b["loader"].probe_media
    def invalidated(uri):
        result = probe(uri)
        b["decisions"].invalidate(b["scope"], cause="evidence_changed")
        return result
    monkeypatch.setattr(b["loader"], "probe_media", invalidated)
    result = b["service"].observe_cover_for_audit(b["scope"])
    assert (result.n2_status, result.n3_status) == ("unknown", "unknown")


def test_audit_n2_observation_does_not_refresh_lru_or_mutate_expired_slot(prepared):
    b = prepared
    now = [0.0]
    cache = CoverDecisionCache(max_scopes=2, selected_ttl=1, clock=lambda: now[0])
    first = b["scope"]
    second = replace(first, placement_id="second")
    third = replace(first, placement_id="third")
    for scope in (first, second):
        token = cache.begin(scope, revision=7)
        assert cache.resolve_and_publish(token, (b["catalog"],), snapshot_complete=True)
    assert cache.observe(first, revision=7) is not None
    assert cache.observe(first, revision=8) is None
    cache.begin(third, revision=7)
    assert cache.observe(first, revision=7) is None
    before = cache._slots[second].decision
    now[0] = 2
    assert cache.observe(second, revision=7) is None
    assert cache._slots[second].decision is before


def test_n2_gateway_owns_registered_n1_proofs_after_producer_mutation(prepared):
    """Producer-owned frozen dataclasses cannot rekey C or change EPUB priority."""
    b = prepared
    original = replace(b["scope"])
    original_embedded_uri = b["embedded"].candidate.url
    original_origin_uri = b["origin"].url
    snapshot = _register(b)
    stable = b["service"]._snapshots[original]
    assert stable is not snapshot
    assert stable.scope is not b["scope"]
    assert stable.embedded is not b["embedded"]
    assert stable.embedded.resource is not b["embedded"].resource
    assert stable.embedded.resource.scope is not b["scope"]

    object.__setattr__(b["scope"], "placement_id", "placement:another-valid")
    object.__setattr__(b["embedded"].candidate, "url", "https://other.example.org/forged.jpg")
    object.__setattr__(b["origin"], "url", "https://other.example.org/changed.jpg")
    object.__setattr__(snapshot, "revision", 999)

    assert original in b["service"]._snapshots
    assert b["scope"] not in b["service"]._snapshots
    stored = b["service"]._snapshots[original]
    assert stored.revision == 7
    assert stored.embedded.resource.scope == original
    assert stored.embedded.candidate.url == original_embedded_uri
    assert stored.candidates[1].url == original_origin_uri
    assert stored.embedded.resource.scope.placement_id == original.placement_id
    assert len(b["service"]._snapshots) == 1


def test_n2_gateway_get_cover_captures_document_before_gtk_recycle(prepared):
    """A later caller mutation does not poison N2 source subscriptions."""
    b = prepared
    source = replace(b["scope"])
    original = replace(source)
    notifications = []
    handle = b["service"].get_cover(
        source, "list", lambda _path: None,
        on_source_changed=lambda: notifications.append(True),
    )
    try:
        assert original in b["service"]._source_subscriptions
        object.__setattr__(source, "placement_id", "placement:recycled")
        assert original in b["service"]._source_subscriptions
        assert source not in b["service"]._source_subscriptions
    finally:
        handle.cancel()
    assert b["service"]._source_subscriptions == {}
    assert notifications == []


def test_n2_gateway_revalidates_mutated_snapshot_before_cache_key(prepared):
    b = prepared
    snapshot = CoverSourceSnapshot(
        scope=b["scope"], revision=7, candidates=(b["origin"],),
    )
    object.__setattr__(b["scope"], "kind", "public_catalogue")
    with pytest.raises(ValueError):
        b["service"].register(snapshot)
    assert b["service"]._snapshots == {}
