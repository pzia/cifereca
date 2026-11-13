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
    # The public notice v1 explicitly excludes even safe cover URLs;
    # changing a private search row must not create a public image.
    return service, session, run


@pytest.mark.parametrize("provider", ["kobo", "numilog"])
@pytest.mark.parametrize("progressive", [False, True])
def test_cross_profile_public_cover_is_not_exported_across_cache_or_identity_roundtrip(
    tmp_path, monkeypatch, provider, progressive,
):
    cover = "https://cdn.example.com/catalogue/books/cover.jpg"
    service, session, run = _fixture(tmp_path, monkeypatch, provider, cover)
    try:
        shared = run(service, progressive, force=True)
        assert len(shared) == 1
        row = shared[0]
        assert row.thumbnail_url is None  # v1 exports no URLs
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
        assert run(service, progressive, force=False)[0].thumbnail_url is None
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


@pytest.mark.parametrize("provider", ["numilog", "paris", "kobo", "synthetic-alpha", "unknown"])
@pytest.mark.parametrize("cover,expected", [
    ("https://cdn.example.org/covers/title.jpg", "https://cdn.example.org/covers/title.jpg"),
    ("https://cdn.example.org/account/reader/cover.jpg", None),
    ("https://cdn.example.org/covers/title.jpg?token=secret", None),
    ("https://user:secret@cdn.example.org/covers/title.jpg", None),
])
def test_mirrored_cover_uses_exact_neutral_public_url_guard(
    tmp_path, provider, cover, expected,
):
    from tests.test_catalog_083 import _service
    from tests.test_core_p0_1354_mirrored_source_scope import notice

    service = _service(str(tmp_path))
    try:
        row = replace(
            notice(), source_provider=provider, thumbnail_url=cover,
        )
        assert service._mirror_remote_results([row], identity_id="default") == 1
        saved, = service.storage.catalog_entries(identity_id="default")
        assert saved["metadata"]["cover_url"] == expected
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
    finally:
        service.close()
