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
    from cifereca.plugin_api import PublicSearchNotice
    original_response = session.search_with_response_facets
    public_cover = "https://cdn.example.org/covers/edition.jpg"
    bibliography = {
        "language": "fr", "languages": ["fr", "en"],
        "subjects": ["Histoire", "Roman"], "series": "Cycle A",
        "isbn": "9780306406157", "publisher": "Éditeur",
        "contributors": ["Auteur Exemple"],
        "private": "SECRET", "account_id": "BOB",
        "authorization": "Bearer SECRET", "rights": {"token": "SECRET"},
        "extra_url": "https://private.invalid/?token=SECRET",
    }

    def observed_response(*args, **kwargs):
        response = original_response(*args, **kwargs)
        # Public catalogue parser explicitly emitted these bibliographic
        # fields in this exact response; private extras are not promoted.
        return replace(response, public_notices=tuple(
            PublicSearchNotice(replace(
                notice.result, thumbnail_url=public_cover,
                metadata=bibliography,
            )) for notice in response.public_notices
        ))

    session.search_with_response_facets = observed_response
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
        assert shared.thumbnail_url is None  # ABI v1 never exports URLs
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
    from cifereca.core.public_search_projection import project_public_search_result
    from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
    from tests.test_core043_progress_cache_stats import _FakeSession

    base = _FakeSession(provider).search("test", "all")[0]
    private = replace(base, source_provider=provider)
    notice = replace(private, owned=False, downloadable=False,
                     download_url=None, metadata={
                         "language": "bad\\x00value",
                         "series": "Bearer SECRET",
                         "publisher": "reader@example.com",
                         "subjects": ["Fiction", "token=SECRET"],
                         "rights": "SECRET", "account": "BOB",
                     })
    assert project_public_search_result(
        ProviderSearchResponse(
            [private], public_notices=(PublicSearchNotice(notice),),
        ), private, provider_id=provider, credential_id=private.source_credential,
    ) is None
