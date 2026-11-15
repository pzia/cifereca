"""#6168 Recent detail is the same owner-attested N2 ref as its list."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import candidate
from cifereca.core.local_cover_evidence import VerifiedLocalCoverEvidence
from cifereca.gui.recent_detail_controller import recent_selected


URI = "https://public.example.org/book.png"


def _evidence(*, owner="alice", artifact=None, placement="placement:a"):
    sha = "a" * 64
    scope = CoverDocumentScope(
        kind="placement", identity_id=owner,
        source_key=placement, artifact_id=artifact or "sha256:" + sha,
        placement_id=placement,
    )
    facts = VerifiedLocalCoverEvidence(
        scope=scope, artifact_sha256=sha,
        verified_path=Path("/tmp/verified.epub"),
        candidates=(candidate(
            URI, tier="catalog", source="known-catalogue",
            evidence_id="notice:a",
        ),),
        embedded=None,
    )
    return facts


def _case(item, *, identity="alice"):
    calls = []
    detail = NS(
        show_summary=lambda **kw: calls.append(("summary", kw)),
        show_local_presentation=lambda projection, **kw: calls.append(
            ("exact", projection, kw)
        ),
        empty=lambda title, body: calls.append(("empty", title, body)),
    )
    window = NS(
        _closed=False,
        _recent_objects={item.get("placement_id") or item["key"]: item},
        recent_page=NS(detail=detail),
        service=NS(
            current_identity=NS(identity_id=identity),
            provider_display_name=lambda provider: "Origine " + provider,
        ),
        _add_book_notice=lambda *_: calls.append(("notice",)),
        _add_local_book_actions=lambda *_: calls.append(("actions",)),
        _add_facets=lambda *_: calls.append(("facets",)),
        search_controls=object(),
    )
    key = item.get("placement_id") or item["key"]
    recent_selected(
        window, NS(key=key), Path=Path,
        format_recent_timestamp=lambda *_args, **_kw: "Hier · 12:34",
    )
    return calls


def test_exact_owner_recent_detail_uses_same_documentary_source_as_list():
    facts = _evidence()
    item = {
        "key": facts.scope.source_key,
        "placement_id": facts.scope.placement_id,
        "artifact_id": facts.scope.artifact_id,
        "identity_id": "alice",
        "title": "Un livre", "author": "Autrice",
        "provider_id": "local", "path": "/books/A/book.epub",
        "thumbnail_url": URI,
        "_exact_local_cover_evidence": facts,
    }
    calls = _case(item)
    assert calls[0][0] == "exact"
    presentation, kw = calls[0][1:]
    assert presentation.title == "Un livre"
    assert presentation.thumbnail_url == URI
    assert kw["cover_evidence"] is facts
    assert [x[0] for x in calls[1:]] == ["notice", "actions", "facets"]
    # No supplier I/O or inferred media relation: DetailPanel will call the
    # very same LocalCoverEvidenceAdapter used by the already-bound list.


def test_unattested_recent_rows_remain_legacy_summary_compatible():
    item = {
        "key": "legacy", "placement_id": "legacy",
        "title": "Sans preuve", "provider_id": "local",
        "thumbnail_url": URI,
    }
    calls = _case(item)
    assert calls[0][0] == "summary"
    assert calls[0][1]["thumbnail"] == URI
    assert calls[0][1]["badges"] == ["Origine local", "Local"]


def test_mismatched_proof_never_downgrades_to_cached_public_url():
    facts = _evidence()
    wrong = replace(
        facts, scope=replace(facts.scope, artifact_id="sha256:" + "b" * 64),
    )
    item = {
        "key": facts.scope.source_key, "placement_id": facts.scope.placement_id,
        "artifact_id": facts.scope.artifact_id, "identity_id": "alice",
        "thumbnail_url": URI,
        "_exact_local_cover_evidence": wrong,
    }
    calls = _case(item)
    assert calls[0][0] == "summary"
    assert calls[0][1]["thumbnail"] is None


def test_foreign_profile_selection_drops_stale_recent_detail_not_just_image():
    facts = _evidence(owner="alice")
    item = {
        "key": facts.scope.source_key, "placement_id": facts.scope.placement_id,
        "artifact_id": facts.scope.artifact_id, "identity_id": "alice",
        "title": "Private alice book", "thumbnail_url": URI,
        "_exact_local_cover_evidence": facts,
    }
    calls = _case(item, identity="bob")
    assert [c[0] for c in calls] == ["empty"]
    assert "Private alice book" not in repr(calls)
    assert URI not in repr(calls)


def test_missing_active_owner_does_not_assume_current_profile():
    facts = _evidence()
    item = {
        "key": facts.scope.source_key, "placement_id": facts.scope.placement_id,
        "artifact_id": facts.scope.artifact_id, "identity_id": "alice",
        "thumbnail_url": URI, "_exact_local_cover_evidence": facts,
    }
    calls = _case(item, identity=None)
    assert calls[0][0] == "summary"
    assert calls[0][1]["thumbnail"] is None
