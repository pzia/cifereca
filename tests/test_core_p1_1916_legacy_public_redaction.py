"""#1916: no personal URLs, filesystem paths or credentials via old SQLite rows."""
from __future__ import annotations

import time

from cifereca.core.known_catalog_fallback import known_public_fallback
from tests.test_core043_progress_cache_stats import _service
from tests.test_core_p1_1916_stale_public_fallback import _known


def _fetch(service):
    return known_public_fallback(
        service.storage, provider_id="good", identity_id="alice",
        query="maritime", scope="digital", field="all", content_types=(),
    )


def _legacy(service, *, key, item_id=None, catalog_id=None, media_format="epub"):
    service.storage.catalog_entry_put({
        "catalog_entry_id": catalog_id or f"known-alice-{key}",
        "title": "Aventure maritime",
        "author": "Auteur",
        "identity_id": "alice", "provider_id": "good",
        "item_id": item_id or f"public-{key}",
        "local_state": "remote_catalog",
        "media_format": media_format,
        "metadata": {
            "source_collection": "catalog",
            "mirrored_at": int(time.time()), "medium": "digital",
        },
    })


def test_unsafe_title_is_never_published_from_legacy_catalog(tmp_path):
    service = _service(tmp_path)
    try:
        _known(service, key="one", title="Maritime https://private.invalid/?token=SECRET")
        _known(service, key="two", title="file:///home/private/maritime")
        _known(service, key="three", title=r"C:\Users\private\maritime")
        _known(service, key="four", title="/home/private/maritime")
        assert _fetch(service) == ()
    finally:
        service.close()


def test_poisoned_author_is_dropped_without_losing_safe_title(tmp_path):
    service = _service(tmp_path)
    try:
        _known(service, key="one", title="Aventure maritime",
               author="Auteur bearer PRIVATE_TOKEN")
        result, = _fetch(service)
        assert result.title == "Aventure maritime"
        assert result.author is None
        assert "PRIVATE_TOKEN" not in repr(result)
        assert result.owned is False and result.downloadable is False
    finally:
        service.close()


def test_opaque_signed_or_path_like_sqlite_ids_do_not_escape(tmp_path):
    service = _service(tmp_path)
    try:
        _legacy(service, key="signed", item_id="https://private.invalid/?token=SECRET")
        _legacy(service, key="path", catalog_id="/home/private/catalogue")
        _legacy(service, key="header", item_id="session=SECRET")
        _legacy(service, key="safe", item_id="urn:uuid:abc-123")
        rows = _fetch(service)
        assert len(rows) == 1
        assert rows[0].record_id == "urn:uuid:abc-123"
        assert "SECRET" not in repr(rows)
        assert "/home/private" not in repr(rows)
    finally:
        service.close()


def test_legacy_media_format_is_reduced_to_short_public_token(tmp_path):
    service = _service(tmp_path)
    try:
        _legacy(service, key="unsafe", media_format="https://private.invalid/?token=SECRET")
        _legacy(service, key="valid", media_format="application/epub+zip")
        rows = {r.record_id: r for r in _fetch(service)}
        assert rows["public-unsafe"].format is None
        assert rows["public-valid"].format == "application/epub+zip"
        assert "SECRET" not in repr(rows)
    finally:
        service.close()
