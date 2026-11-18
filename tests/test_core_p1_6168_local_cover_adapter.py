"""#6168: lazy Core-attested local cover facts -> versioned N2, with shared N3."""
from __future__ import annotations

from dataclasses import replace
import hashlib
from threading import Event

import pytest

from cifereca import epub_metadata
from cifereca.core.covers import candidate
from cifereca.core.local_cover_evidence import project_verified_local_cover_evidence
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.local_cover_adapter import LocalCoverEvidenceAdapter
from tests.test_epub_cover_contract import _write_epub


ORIGIN = "https://public.example.org/a.png"
SECOND = "https://public.example.org/b.png"


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "real.epub"
    binary = _write_epub(book, epub3=True)
    sha = hashlib.sha256(binary).hexdigest()
    artifact = epub_metadata.inspect_epub(book)
    input = dict(
        identity_id="alice", book_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="placement:alice",
        artifact_sha256=sha, artifact_metadata=artifact,
        verified_path=book,
        candidates=(candidate(ORIGIN, tier="origin", source="publisher",
                              evidence_id="edition-a"),),
    )
    facts = project_verified_local_cover_evidence(**input)
    assert facts is not None and facts.embedded is not None
    image = epub_metadata.cover_cache_dir() / facts.embedded.resource.cache_name
    loader = CoverLoader(max_workers=2)
    service = DocumentCoverService(loader)
    adapter = LocalCoverEvidenceAdapter()
    try:
        yield input, facts, image, service, adapter, loader
    finally:
        adapter.close()
        service.close()
        loader.close()


def test_same_core_evidence_reuses_single_n2_revision(prepared):
    _input, facts, _image, service, adapter, loader = prepared
    doc = adapter.register(service, facts)
    assert adapter.register(service, facts) == doc
    assert adapter.stats()["sources"] == 1
    events = []
    service.decisions.subscribe(doc, events.append)
    path = []
    service.get_cover(doc, "detail", path.append)
    service.get_cover(doc, "detail", path.append)
    assert len(path) == 2 and path[0] == path[1]
    assert [x.kind for x in events] == ["refresh", "resolved"]
    assert service.decisions.peek(doc, revision=1).tier == "embedded"
    assert loader.stats()["download_ok"] == 0


def test_changing_attested_source_revokes_old_n2_token_and_aba(prepared):
    _input, facts, _image, service, adapter, _loader = prepared
    doc = adapter.register(service, facts)
    selected = []
    service.get_cover(doc, "detail", selected.append)
    old = service.decisions.selected_token(
        doc, revision=1, uri=facts.embedded.candidate.url,
    )
    assert old is not None
    alt = replace(facts, candidates=(
        candidate(SECOND, tier="origin", source="publisher",
                  evidence_id="edition-b"),
    ))
    adapter.register(service, alt)
    assert service.decisions.selected_token(
        doc, revision=1, uri=facts.embedded.candidate.url,
    ) is None
    next_view = []
    service.get_cover(doc, "detail", next_view.append)
    token2 = service.decisions.selected_token(
        doc, revision=2, uri=facts.embedded.candidate.url,
    )
    assert token2 is not None and token2.generation > old.generation
    adapter.register(service, facts)
    assert service.decisions.peek(doc, revision=2) is None
    final = []
    service.get_cover(doc, "detail", final.append)
    token3 = service.decisions.selected_token(
        doc, revision=3, uri=facts.embedded.candidate.url,
    )
    assert token3.generation > token2.generation


def test_same_n3_file_different_owner_never_aliases_n2(prepared):
    input, a, image, service, adapter, _loader = prepared
    b = project_verified_local_cover_evidence(
        **{**input, "identity_id": "bob", "placement_id": "placement:bob"},
    )
    assert b is not None and b.scope != a.scope
    doc_a = adapter.register(service, a)
    doc_b = adapter.register(service, b)
    assert doc_a != doc_b
    assert adapter.stats()["sources"] == 2
    first = []
    second = []
    service.get_cover(doc_a, "detail", first.append)
    service.get_cover(doc_b, "detail", second.append)
    assert first == second == [image]
    assert service.decisions.peek(doc_a, revision=1).status == "selected"
    assert service.decisions.peek(doc_b, revision=2).status == "selected"
    assert image.is_file()


def test_physical_cache_miss_keeps_embedded_n2_and_restores_once(prepared):
    _input, facts, image, service, adapter, loader = prepared
    image.unlink()
    doc = adapter.register(service, facts)
    done = Event()
    result = []
    service.get_cover(doc, "detail",
        lambda value: (result.append(value), done.set()))
    assert done.wait(10)
    assert result == [image]
    assert service.decisions.peek(doc, revision=1).tier == "embedded"
    assert loader.stats()["download_ok"] == 0


def test_evidence_registry_eviction_never_resurrects_prior_revision(prepared):
    input, a, _image, service, _adapter, _loader = prepared
    adapter = LocalCoverEvidenceAdapter(max_sources=1)
    try:
        doc_a = adapter.register(service, a)
        b = project_verified_local_cover_evidence(
            **{**input, "identity_id": "bob", "placement_id": "p:b"},
        )
        adapter.register(service, b)
        assert adapter.stats()["sources"] == 1
        adapter.register(service, a)
        service.get_cover(doc_a, "detail", lambda path: None)
        assert service.decisions.peek(doc_a, revision=3).uri == (
            a.embedded.candidate.url
        )
        assert service.decisions.peek(doc_a, revision=3).tier == "embedded"
        assert service.decisions.peek(doc_a, revision=1) is None
    finally:
        adapter.close()


def test_invalid_facts_and_closed_service_cannot_publish(prepared):
    _input, facts, _image, service, adapter, _loader = prepared
    with pytest.raises(TypeError):
        adapter.register(service, object())
    with pytest.raises(TypeError):
        adapter.register(object(), facts)
    service.close()
    with pytest.raises(RuntimeError):
        adapter.register(service, facts)
    assert adapter.stats()["sources"] == 0


@pytest.mark.parametrize("limit", [0, -1, True, 20001])
def test_bad_adapter_capacity_rejected(limit):
    with pytest.raises(ValueError):
        LocalCoverEvidenceAdapter(max_sources=limit)


def test_audit_port_checks_exact_registered_evidence_and_revision(prepared, monkeypatch):
    from types import SimpleNamespace
    _input, facts, _image, service, adapter, _loader = prepared
    calls = []
    def probe(document, *, expected_revision):
        calls.append((document, expected_revision))
        return SimpleNamespace(n2_status="selected", n2_tier="embedded",
                               n3_status="digest_valid")
    monkeypatch.setattr(service, "observe_cover_for_audit", probe, raising=False)
    assert adapter.observe_audit_packet(service, facts) is None
    doc = adapter.register(service, facts)
    assert adapter.observe_audit_packet(service, facts) == (
        "selected", "embedded", "digest_valid",
    )
    assert calls == [(doc, 1)]
    changed = replace(facts, candidates=())
    adapter.register(service, changed)
    assert adapter.observe_audit_packet(service, facts) is None
    assert calls == [(doc, 1)]
    adapter.close()
    assert adapter.observe_audit_packet(service, changed) is None
