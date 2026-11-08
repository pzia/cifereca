"""P1 #3104: every Core profile can see catalogue cover URLs from other profiles.

Only synthetic public catalogue rows, no HTTP, credentials, or remote mutation.
"""
from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from tests.test_core_p1_4909_kobo_shared_public import (
    setup as setup_kobo, run as run_kobo,
)
from tests.test_core_p1_4909_numilog_shared_public import (
    setup as setup_numilog, run as run_numilog,
)


def _fixture(tmp_path, monkeypatch, provider, cover):
    if provider == "kobo":
        service, session, _ = setup_kobo(tmp_path, monkeypatch)
        run = run_kobo
        collection = "catalog"
    else:
        service, session, _ = setup_numilog(tmp_path, monkeypatch)
        run = run_numilog
        collection = "public_catalog"
    original = session.search

    def search(*args, **kwargs):
        return [
            replace(row, thumbnail_url=cover)
            if row.source_collection == collection else row
            for row in original(*args, **kwargs)
        ]

    session.search = search
    return service, session, run


@pytest.mark.parametrize("provider", ["kobo", "numilog"])
@pytest.mark.parametrize("progressive", [False, True])
def test_cross_profile_public_cover_survives_search_cache_and_identity_roundtrip(
    tmp_path, monkeypatch, provider, progressive,
):
    cover = "https://cdn.example.com/catalogue/books/cover.jpg"
    service, session, run = _fixture(tmp_path, monkeypatch, provider, cover)
    try:
        shared = run(service, progressive, force=True)
        assert len(shared) == 1
        row = shared[0]
        assert row.thumbnail_url == cover
        assert row.source_provider == provider
        assert row.owned is False and row.downloadable is False
        assert row.download_url is None and row.friendly_url is None
        assert row.metadata == {"catalogue_access": "shared_read_only"}
        assert "SECRET" not in repr(asdict(row))
        assert service.can_direct_download(row) is False

        assert run(service, progressive, force=False) == shared
        service.select_identity("bob")
        own = run(service, progressive, force=False)
        assert len(own) == 1 and own[0].owned is True
        service.select_identity("alice")
        assert run(service, progressive, force=False)[0].thumbnail_url == cover
        assert session.private_calls >= 1
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["kobo", "numilog"])
@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("unsafe", [
    "https://cdn.example.com/catalogue/cover.jpg?token=SECRET",
    "https://cdn.example.com/account/B/cover.jpg",
    "https://user:SECRET@cdn.example.com/covers/a.jpg",
    "https://cdn.example.com/download/secret.jpg",
    "http://127.0.0.1/covers/secret.jpg",
    "https://cdn.example.com/covers/secret.jpg#SESSION",
])
def test_shared_catalogue_cover_never_exports_credentials_or_delivery_urls(
    tmp_path, monkeypatch, provider, progressive, unsafe,
):
    service, session, run = _fixture(tmp_path, monkeypatch, provider, unsafe)
    try:
        shared = run(service, progressive, force=True)
        assert len(shared) == 1
        row = shared[0]
        assert row.thumbnail_url is None
        assert row.owned is False and row.downloadable is False
        assert row.download_url is None and row.friendly_url is None
        assert row.metadata == {"catalogue_access": "shared_read_only"}
        assert "SECRET" not in repr(asdict(row))
        assert service.can_direct_download(row) is False
    finally:
        service.close()
