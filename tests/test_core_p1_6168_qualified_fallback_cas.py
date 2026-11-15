"""#6168: EPUB > URL except a bounded, qualified exact-source N2 fallback."""
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
from cifereca.core.epub_cover_failure_qualification import (
    EpubCoverPhysicalAssessment, qualify_failed_epub_cover,
)
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import (
    CoverSourceSnapshot, DocumentCoverService,
)
from tests.test_epub_cover_contract import _write_epub


ORIGIN = "https://public.example.org/exact-edition.png"
CATALOG = "https://public.example.org/catalog.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "exact.epub"
    raw = _write_epub(book, epub3=True)
    sha = hashlib.sha256(raw).hexdigest()
    metadata = epub_metadata.inspect_epub(book)
    scope = CoverDocumentScope(
        kind="placement", identity_id="owner",
        source_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="p:exact",
    )
    embedded = project_exact_embedded_cover(
        scope, revision=7, artifact_sha256=sha, artifact_metadata=metadata,
    )
    assert embedded is not None
    image = epub_metadata.cover_cache_dir() / embedded.resource.cache_name
    origin = candidate(
        ORIGIN, tier="origin", source="source-exact", evidence_id="edition",
    )
    catalog = candidate(
        CATALOG, tier="catalog", source="catalog-exact", evidence_id="notice",
    )
    clock = [0.0]
    loader = CoverLoader(max_workers=2)
    decisions = CoverDecisionCache()
    svc = DocumentCoverService(
        loader, decisions=decisions, fallback_ttl=20,
        clock=lambda: clock[0],
    )
    # N3 already contains an admitted public image, but it must not
    # outrank the documented embedded source before qualification.
    loader._path(ORIGIN).write_bytes(PNG)
    loader._path(CATALOG).write_bytes(PNG)
    try:
        yield {
            "book": book, "scope": scope, "embedded": embedded,
            "image": image, "origin": origin, "catalog": catalog,
            "loader": loader, "decisions": decisions, "service": svc,
            "clock": clock,
        }
    finally:
        svc.close()
        loader.close()


def _register(item, *, broken=False, candidates=None, revision=7):
    embedded = item["embedded"]
    if broken:
        # Models an obsolete EPUB member record for the same, verified
        # immutable Artifact. No physical bytes exist in N3.
        embedded = replace(
            embedded, resource=replace(
                embedded.resource, member="OPS/cover-not-present.png",
            ),
        )
    if revision != 7:
        embedded = replace(
            embedded, resource=replace(
                embedded.resource, source_revision=revision,
            ),
        )
    item["service"].register(CoverSourceSnapshot(
        scope=item["scope"], revision=revision,
        embedded=embedded, verified_epub_path=item["book"],
        candidates=(
            (item["origin"], item["catalog"])
            if candidates is None else candidates
        ),
    ))
    return embedded


def _choose_epub(item):
    done = Event()
    values = []
    request = item["service"].get_cover(
        item["scope"], "detail",
        lambda value: (values.append(value), done.set()),
    )
    assert request.source_uri == item["embedded"].candidate.url
    assert done.wait(10)
    decision = item["decisions"].peek(item["scope"], revision=7)
    assert (decision.status, decision.tier) == ("selected", "embedded")
    return item["decisions"].selected_token(
        item["scope"], revision=7, uri=decision.uri,
    )


def test_definitive_immutable_epub_failure_allows_one_exact_n2_url_fallback(state):
    item = state
    item["image"].unlink()
    broken = _register(item, broken=True)
    old = _choose_epub(item)
    assert old is not None

    assessment = qualify_failed_epub_cover(
        broken.resource, source_path=item["book"],
    )
    assert assessment.permits_url_fallback
    fallback = item["service"].apply_qualified_epub_fallback(
        item["scope"], assessment, expected_token=old,
    )
    assert fallback is not None
    assert (fallback.status, fallback.tier, fallback.uri) == (
        "selected", "origin", ORIGIN,
    )
    latest = item["decisions"].selected_token(
        item["scope"], revision=7, uri=ORIGIN,
    )
    assert latest is not None and latest.generation > old.generation
    assert item["decisions"].selected_token(
        item["scope"], revision=7, uri=broken.candidate.url,
    ) is None

    painted = []
    request = item["service"].get_cover(
        item["scope"], "detail", painted.append,
    )
    assert request.source_uri == ORIGIN
    assert painted == [item["loader"]._path(ORIGIN)]
    assert item["loader"].stats()["download_ok"] == 0
    # Replayed/late original EPUB N2 token cannot publish a second fallback.
    assert item["service"].apply_qualified_epub_fallback(
        item["scope"], assessment, expected_token=old,
    ) is None


def test_retryable_epub_n3_miss_never_promotes_a_hot_origin_url(state):
    item = state
    item["image"].unlink()
    real = _register(item)
    token = _choose_epub(item)
    assessment = qualify_failed_epub_cover(
        real.resource, source_path=item["book"],
    )
    assert not assessment.permits_url_fallback
    assert item["service"].apply_qualified_epub_fallback(
        item["scope"], assessment, expected_token=token,
    ) is None
    assert item["decisions"].peek(
        item["scope"], revision=7,
    ).tier == "embedded"


def test_fallback_lease_expires_and_restores_epub_priority(state):
    item = state
    original = item["image"].read_bytes()
    item["image"].unlink()
    broken = _register(item, broken=True)
    token = _choose_epub(item)
    proof = qualify_failed_epub_cover(
        broken.resource, source_path=item["book"],
    )
    assert item["service"].apply_qualified_epub_fallback(
        item["scope"], proof, expected_token=token,
    ).tier == "origin"
    stale = item["decisions"].selected_token(
        item["scope"], revision=7, uri=ORIGIN,
    )
    assert stale is not None
    # N3 is repaired independently: it never creates a document association.
    item["image"].write_bytes(original)
    item["clock"][0] = 20.5
    callback = []
    request = item["service"].get_cover(
        item["scope"], "detail", callback.append,
    )
    assert request.source_uri == broken.candidate.url
    assert callback == [item["image"]]
    selected = item["decisions"].peek(item["scope"], revision=7)
    assert selected.tier == "embedded"
    new = item["decisions"].selected_token(
        item["scope"], revision=7, uri=broken.candidate.url,
    )
    assert new is not None and new.generation > stale.generation
    assert item["loader"].stats()["download_ok"] == 0


def test_no_unique_remote_candidate_keeps_embedded_n2(state):
    item = state
    item["image"].unlink()
    second = candidate(
        "https://public.example.org/other-origin.png",
        tier="origin", source="second", evidence_id="other",
    )
    broken = _register(item, broken=True, candidates=(item["origin"], second))
    token = _choose_epub(item)
    proof = qualify_failed_epub_cover(
        broken.resource, source_path=item["book"],
    )
    assert proof.permits_url_fallback
    assert item["service"].apply_qualified_epub_fallback(
        item["scope"], proof, expected_token=token,
    ) is None
    assert item["decisions"].selected_token(
        item["scope"], revision=7, uri=broken.candidate.url,
    ) == token


def test_wrong_source_scope_revision_or_token_cannot_change_n2(state):
    item = state
    item["image"].unlink()
    broken = _register(item, broken=True)
    token = _choose_epub(item)
    proof = qualify_failed_epub_cover(
        broken.resource, source_path=item["book"],
    )
    foreign = replace(
        broken.resource,
        scope=replace(item["scope"], identity_id="foreign", placement_id="p:b"),
    )
    assert item["service"].apply_qualified_epub_fallback(
        item["scope"], replace(proof, resource=foreign),
        expected_token=token,
    ) is None
    assert item["service"].apply_qualified_epub_fallback(
        replace(item["scope"], identity_id="foreign", placement_id="p:b"),
        proof, expected_token=token,
    ) is None
    assert item["service"].apply_qualified_epub_fallback(
        item["scope"], proof, expected_token=replace(token, generation=0),
    ) is None
    assert item["decisions"].selected_token(
        item["scope"], revision=7, uri=broken.candidate.url,
    ) == token


def test_source_revision_change_revokes_existing_fallback_and_token(state):
    item = state
    item["image"].unlink()
    broken = _register(item, broken=True)
    token = _choose_epub(item)
    proof = qualify_failed_epub_cover(
        broken.resource, source_path=item["book"],
    )
    item["service"].apply_qualified_epub_fallback(
        item["scope"], proof, expected_token=token,
    )
    _register(item, revision=8)
    assert item["decisions"].peek(item["scope"], revision=7) is None
    assert item["service"].apply_qualified_epub_fallback(
        item["scope"], proof, expected_token=token,
    ) is None


@pytest.mark.parametrize("kwargs", [
    {"fallback_ttl": 0}, {"fallback_ttl": -1},
    {"fallback_ttl": 301}, {"fallback_ttl": True},
    {"clock": None},
])
def test_fallback_lifetime_configuration_is_bounded(state, kwargs):
    with pytest.raises(ValueError):
        DocumentCoverService(state["loader"], **kwargs)
