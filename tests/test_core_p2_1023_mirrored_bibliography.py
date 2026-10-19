"""#1023: Kobo's already-attested awards survive the identity-scoped Core mirror."""
from __future__ import annotations

from dataclasses import replace

import pytest

from tests.test_core043_progress_cache_stats import _service, _FakeSession
from cifereca.core.search_orchestration import _safe_public_search_text


SECRET = "https://private.invalid/?token=DO_NOT_STORE"


@pytest.mark.parametrize("value", [
    SECRET, "Bearer SECRET", "Token=SECRET", "user@example.invalid",
    ["Prix du Quai", SECRET], {"title": "Prix du Quai"},
    ["Prix du Quai"] * 33, "", ["prix\x01"],
])
def test_untrusted_nested_private_or_unbounded_values_never_reach_public_mirror(value):
    assert _safe_public_search_text("awards", value) is None


def test_safe_text_normalizes_only_text_and_bounds_descriptions():
    assert _safe_public_search_text("awards", ["Prix du Quai"]) == ["Prix du Quai"]
    assert _safe_public_search_text("themes", "  Enquêtes   françaises ") == "Enquêtes françaises"
    assert _safe_public_search_text("synopsis", " Résumé\npublic ") == "Résumé public"
    assert _safe_public_search_text("synopsis", "A" * 4001) is None
    assert _safe_public_search_text("awards", "A" * 241) is None


def test_core_mirror_keeps_awards_keywords_and_synopsis_for_the_same_edition(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        sample = _FakeSession("kobo").search("sample", "all")[0]
        row = replace(
            sample,
            source_provider="kobo", record_id="edition-kobo-001",
            title="Une histoire quelconque", author="Auteur fictif",
            source_collection="catalog",
            metadata={
                "awards": ["Prix du Quai des Orfèvres"],
                "prizes": ["Prix littéraire attesté"],
                "keywords": ["Enquête urbaine"],
                "themes": ["Justice"],
                "tags": ["Mystère"],
                "genres": ["Policier"],
                "categories": ["Littérature"],
                "synopsis": "Un roman mettant en scène le quai.",
                "summary": "Récit de la récompense littéraire.",
                "account": "PRIVATE_AUTH",
                "secret": SECRET,
            },
        )
        assert service._mirror_remote_results([row], identity_id="alice") == 1
        stored = service.storage.catalog_entries(identity_id="alice", provider_id="kobo")
        assert len(stored) == 1
        assert stored[0]["item_id"] == "edition-kobo-001"
        meta = stored[0]["metadata"]
        for key in ("awards", "prizes", "keywords", "themes", "tags", "genres",
                    "categories", "synopsis", "summary"):
            assert key in meta
        assert SECRET not in repr(meta)
        assert "PRIVATE_AUTH" not in repr(meta)
        matched = service.search_known_catalog("prix du quai", identity_id="alice")
        assert [r["item_id"] for r in matched] == ["edition-kobo-001"]
        assert service.search_known_catalog("enquête", identity_id="alice")[0]["item_id"] == "edition-kobo-001"
        assert service.search_known_catalog("mystère", identity_id="alice")[0]["item_id"] == "edition-kobo-001"
        assert service.search_known_catalog("littéraire", identity_id="alice")[0]["item_id"] == "edition-kobo-001"
        assert service.search_known_catalog("prix du quai", identity_id="bob") == []
        assert service.search_known_catalog("PRIVATE_AUTH", identity_id="alice") == []
    finally:
        service.close()


def test_private_or_conflicted_award_does_not_pollute_known_catalogue(tmp_path):
    service = _service(tmp_path)
    try:
        template = _FakeSession("kobo").search("sample", "all")[0]
        original = {
            "awards": ["Prix du Quai", SECRET],
            "keywords": {"license": "Prix du Quai"},
            "summary": f"private credential: {SECRET}",
            "genres": ["Policier"],
        }
        row = replace(
            template, source_provider="kobo", record_id="edition-2",
            title="Titre sans prix", source_collection="catalog", metadata=original,
        )
        assert service._mirror_remote_results([row], identity_id="alice") == 1
        stored = service.storage.catalog_entries(identity_id="alice", provider_id="kobo")
        meta = stored[0]["metadata"]
        assert all(key not in meta for key in ("awards", "keywords", "summary"))
        assert meta["genres"] == ["Policier"]
        assert SECRET not in repr(meta)
        assert not service.search_known_catalog("prix du quai", identity_id="alice")
        assert original["awards"] == ["Prix du Quai", SECRET]
    finally:
        service.close()
