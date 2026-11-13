"""#5869: public catalogue attestations cannot originate in MesLivres."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.plugin_api import SearchResult, ProviderCapabilityError
from cifereca.core.public_search_projection import project_public_search_result
from cifereca_numilog import NumilogSession


def _session():
    return NumilogSession(
        credential_id="private-owner", label="Numilog",
        credentials={}, options={}, config={}, diagnostics=None,
    )


def _public_row():
    return SearchResult(
        id="numilog:public:123", record_id="numilog:public:123",
        docbase="NUMILOG", medium="digital", title="Public book",
        author="Author", type="Livre numérique", format="EPUB",
        publisher=None, published_at=None, description="Public description",
        thumbnail_url="https://www.numilog.com/cover.jpg",
        friendly_url="https://www.numilog.com/Book/123",
        scenario="NUMILOG_PUBLIC", source_provider="numilog",
        source_credential="__public__", source_collection="public_catalog",
        owned=False, downloadable=False, metadata={"public_enrichment": {"token": "private"}},
    )


def test_catalog_scope_attests_exact_anonymous_rows_without_private_access(monkeypatch):
    session = _session()
    row = _public_row()
    seen = []
    def anonymous(query, *, page=0, field="all"):
        seen.append((query, page, field))
        return [row]
    monkeypatch.setattr(session, "search_public", anonymous)
    monkeypatch.setattr(session, "library", lambda: pytest.fail("private library accessed"))
    try:
        response = session.search_with_response_facets("Public book", "catalog")
        assert seen == [("Public book", 0, "all")]
        assert response.results == [row]
        assert len(response.public_notices) == 1
        notice = response.public_notices[0].result
        assert notice is not row
        assert notice.source_credential == "__public__"
        assert notice.id == row.id
        assert notice.download_url is notice.source_account_id is None
        assert notice.friendly_url is notice.thumbnail_url is None
        assert notice.metadata == {}
        assert notice.description is None
        assert notice.owned is notice.downloadable is False
        projected = project_public_search_result(
            response, row, provider_id="numilog", credential_id="__public__",
        )
        assert projected is not None
        assert projected.title == "Public book"
        assert projected.friendly_url is projected.download_url is None
        assert projected.metadata == {"catalogue_access": "shared_read_only"}
    finally:
        session.close()


def test_private_scope_never_emits_public_notices(monkeypatch):
    session = _session()
    private = replace(
        _public_row(), id="owned:123", record_id="123",
        source_credential="private-owner", owned=True, downloadable=True,
        download_url="https://private.invalid/token",
    )
    monkeypatch.setattr(session, "search", lambda *args, **kwargs: [private])
    try:
        response = session.search_with_response_facets("Private", "digital")
        assert response.results == [private]
        assert response.public_notices == ()
        assert project_public_search_result(
            response, private, provider_id="numilog",
            credential_id="private-owner",
        ) is None
    finally:
        session.close()


def test_failed_public_search_cannot_reuse_previous_attestation(monkeypatch):
    session = _session()
    calls = [0]
    def anonymous(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 2:
            raise ProviderCapabilityError("Numilog : public_catalog_forbidden")
        return [_public_row()]
    monkeypatch.setattr(session, "search_public", anonymous)
    try:
        response = session.search_with_response_facets("first", "catalog")
        assert len(response.public_notices) == 1
        with pytest.raises(ProviderCapabilityError):
            session.search_with_response_facets("second", "catalog")
    finally:
        session.close()
