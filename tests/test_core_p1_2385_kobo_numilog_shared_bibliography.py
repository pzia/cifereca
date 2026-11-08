"""P1 #2385: shared Kobo/Numilog catalogue metadata is readable across profiles.

Synthetic local catalogue sessions, no private-provider GET, credentials or DRM.
"""
from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from tests import test_core_p1_4909_kobo_shared_public as kobo
from tests import test_core_p1_4909_numilog_shared_public as numilog


@pytest.mark.parametrize("provider", ["kobo", "numilog"])
@pytest.mark.parametrize("progressive", [False, True])
def test_public_bibliography_survives_cross_profile_search_and_cache(
    tmp_path, monkeypatch, provider, progressive,
):
    module = kobo if provider == "kobo" else numilog
    service, session, _owners = module.setup(tmp_path, monkeypatch)
    old_search = session.search
    public_cover = "https://cdn.example.org/covers/edition.jpg"
    bibliography = {
        "language": "fr", "languages": ["fr", "en"],
        "subjects": ["Histoire", "Roman"], "series": "Cycle A",
        "isbn": "9780306406157", "publisher": "Éditeur",
        "contributors": ["Auteur Exemple"],
        "private": "SECRET", "account_id": "BOB",
        "authorization": "Bearer SECRET",
        "rights": {"token": "SECRET"},
        "extra_url": "https://private.invalid/?token=SECRET",
    }

    def observed_search(*args, **kwargs):
        rows = old_search(*args, **kwargs)
        return [
            replace(row, thumbnail_url=public_cover, metadata=bibliography)
            if (
                row.source_collection == ("catalog" if provider == "kobo" else "public_catalog")
                and row.scenario == ("KOBO" if provider == "kobo" else "NUMILOG_PUBLIC")
            ) else row
            for row in rows
        ]

    session.search = observed_search
    expected = {
        "catalogue_access": "shared_read_only",
        "language": "fr", "languages": ["fr", "en"],
        "subjects": ["Histoire", "Roman"], "series": "Cycle A",
        "isbn": "9780306406157", "publisher": "Éditeur",
        "contributors": ["Auteur Exemple"],
    }
    try:
        rows = module.run(service, progressive, force=True)
        assert len(rows) == 1
        shared = rows[0]
        assert shared.metadata == expected
        assert shared.thumbnail_url == public_cover
        assert shared.downloadable is False and shared.download_url is None
        assert shared.owned is False and shared.friendly_url is None
        assert "SECRET" not in repr(asdict(shared))
        before = session.public_calls
        assert module.run(service, progressive, force=False) == rows
        assert session.public_calls == before
        service.select_identity("bob")
        service.select_identity("alice")
        assert module.run(service, progressive, force=False) == rows
        assert session.private_calls == 0
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["kobo", "numilog"])
def test_shared_catalogue_rejects_unsafe_and_unsupported_metadata(provider):
    from cifereca.core.search_orchestration import _shared_catalogue_bibliography
    from tests.test_core043_progress_cache_stats import _FakeSession

    base = _FakeSession(provider).search("test", "all")[0]
    row = replace(
        base, source_provider=provider,
        scenario="KOBO" if provider == "kobo" else "NUMILOG_PUBLIC",
        metadata={
            "language": "bad\\x00value", "series": "Bearer SECRET",
            "publisher": "reader@example.com",
            "subjects": ["Fiction", "token=SECRET"],
            "rights": "SECRET", "account": "BOB",
            "thumbnail_url": "https://example.org/private",
        },
    )
    assert _shared_catalogue_bibliography(row) == {
        "catalogue_access": "shared_read_only"
    }
