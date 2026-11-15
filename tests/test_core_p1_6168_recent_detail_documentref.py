"""P1 #6168: Recent detail uses the same exact Core N2 EPUB proof as Library."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca import epub_metadata
from cifereca.core.local_cover_evidence import project_verified_local_cover_evidence
from cifereca.gui.local_detail_cover import exact_recent_detail_cover
from cifereca.gui.recent_detail_controller import recent_selected
from tests.test_epub_cover_contract import _write_epub


@pytest.fixture
def exact(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "source.epub"
    data = _write_epub(book, epub3=True)
    digest = hashlib.sha256(data).hexdigest()
    proof = project_verified_local_cover_evidence(
        identity_id="alice", book_key="artifact:sha256:" + digest,
        artifact_id="sha256:" + digest, placement_id="placement:alice",
        artifact_sha256=digest, verified_path=book,
        artifact_metadata=epub_metadata.inspect_epub(book),
    )
    assert proof is not None and proof.embedded is not None
    row = {
        "title": "Exact", "author": "Autrice",
        "provider_id": "local", "identity_id": "alice",
        "artifact_id": proof.scope.artifact_id,
        "placement_id": proof.scope.placement_id,
        "path": str(book), "thumbnail_url": "https://public.example.org/hot.jpg",
        "_exact_local_cover_evidence": proof,
    }
    return row, proof


def host(row, *, identity="alice", capable=True):
    events = []
    detail = NS(
        show_summary=lambda **k: events.append(("url", k)),
        empty=lambda title, description: events.append(("empty", title, description)),
    )
    if capable:
        detail.show_local_presentation = lambda presentation, *, cover_evidence: (
            events.append(("n2", presentation, cover_evidence))
        )
    window = NS(
        _closed=False, _recent_objects={"placement:alice": row},
        recent_page=NS(detail=detail),
        service=NS(current_identity=NS(identity_id=identity),
                   provider_display_name=lambda _s: "Local"),
        search_controls=object(),
        _add_book_notice=lambda *_: events.append(("notice",)),
        _add_local_book_actions=lambda *_: events.append(("actions",)),
        _add_facets=lambda *_: events.append(("facets",)),
    )
    return window, events


def test_recent_detail_does_not_use_hot_url_when_exact_epub_is_attested(exact):
    row, proof = exact
    image = epub_metadata.cover_cache_dir() / proof.embedded.resource.cache_name
    image.unlink()  # N3 miss is not documentary N1 failure.
    window, events = host(row)
    recent_selected(window, NS(key="placement:alice"), Path=Path,
                    format_recent_timestamp=lambda *_a, **_kw: "Aujourd'hui")
    assert events[0][0] == "n2"
    presentation, received = events[0][1:]
    assert received is proof
    assert presentation.thumbnail_url == row["thumbnail_url"]
    assert presentation.title == "Exact"
    assert [v[0] for v in events[1:]] == ["notice", "actions", "facets"]
    assert not image.exists()


def test_read_only_foreign_profile_never_uses_exact_local_packet(exact):
    row, _proof = exact
    window, events = host(row, identity="bob")
    recent_selected(window, NS(key="placement:alice"), Path=Path,
                    format_recent_timestamp=lambda *_a, **_kw: "Date")
    assert events[0][0] == "empty"  # Stale profile cannot reveal title/path.
    assert "Exact" not in repr(events)
    assert row["thumbnail_url"] not in repr(events)


@pytest.mark.parametrize("mutation", [
    {"identity_id": "bob"},
    {"artifact_id": "sha256:" + "b" * 64},
    {"placement_id": "other-placement"},
    {"_exact_local_cover_evidence": object()},
    {"_library_view_owner": "bob"},
])
def test_recent_detail_never_adopts_mismatched_packet(exact, mutation):
    row, proof = exact
    assert exact_recent_detail_cover(
        {**row, **mutation}, active_identity_id="alice",
    ) is None
    assert exact_recent_detail_cover(
        row, active_identity_id="alice",
    ) is proof


def test_recent_detail_fails_closed_without_document_aware_panel(exact):
    row, _proof = exact
    window, events = host(row, capable=False)
    recent_selected(window, NS(key="placement:alice"), Path=Path,
                    format_recent_timestamp=lambda *_a, **_kw: "Date")
    assert events[0] == ("url", {
        "title": "Exact", "subtitle": "Autrice", "thumbnail": None,
        "badges": ["Local", "Local"], "group_title": "Récupération",
        "rows": [
            ("Fichier", "source.epub"), ("Récupéré", "Date"),
            ("Provenance", "Local"), ("Chemin", row["path"]),
        ],
    })


def test_recent_legacy_rows_keep_their_original_summary_abi():
    item = {"title": "Old book", "placement_id": "local:old",
            "thumbnail_url": "https://public.example.org/legacy.jpg"}
    window, events = host(item)
    window._recent_objects = {"local:old": item}
    recent_selected(window, NS(key="local:old"), Path=Path,
                    format_recent_timestamp=lambda *_a, **_kw: "Date")
    assert events[0][0] == "url"
    assert events[0][1]["thumbnail"] == item["thumbnail_url"]
