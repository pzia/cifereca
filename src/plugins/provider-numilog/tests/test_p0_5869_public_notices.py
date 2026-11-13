"""P0 #5869: real Numilog session publishes only parsed anonymous cards.

Offline HTML fixture. No live HTTP, authenticated library, ADEPT or download.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.core.public_search_projection import project_public_search_result
from cifereca.plugin_api import ProviderSearchResponse
from cifereca_numilog import NumilogSession
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_client import public_search_url


FIX = Path(__file__).parent / "fixtures"
PUBLIC_PAGE = parse_public_search(
    (FIX / "public_search_jefferson_20261016.html").read_text(),
    public_search_url("jefferson", 0),
)


def _session():
    return NumilogSession(
        credential_id="private-credential", label="Offline",
        credentials={}, options={}, config={}, diagnostics=None,
    )


def test_anonymous_catalogue_attests_only_exact_public_parser_cards(monkeypatch):
    session = _session()
    calls = []
    def public(query, *, page, field):
        calls.append((query, page, field))
        return list(PUBLIC_PAGE.items)
    monkeypatch.setattr(session, "search_public", public)
    monkeypatch.setattr(
        session, "library",
        lambda: pytest.fail("public search must never read private purchases"),
    )
    try:
        response = session.search_with_response_facets("jefferson", "catalog")
        assert type(response) is ProviderSearchResponse
        assert len(response.results) == len(response.public_notices) == len(PUBLIC_PAGE.items)
        assert calls == [("jefferson", 0, "all")]
        for row, declaration in zip(response.results, response.public_notices):
            assert declaration.result is not row
            assert declaration.result.id == row.id
            assert declaration.result.source_provider == "numilog"
            assert declaration.result.source_credential == "__public__"
            assert declaration.result.source_collection == "public_catalog"
            assert declaration.result.metadata == {}
            assert declaration.result.owned is declaration.result.downloadable is False
            assert declaration.result.source_account_id is None
            assert declaration.result.download_url is declaration.result.friendly_url is None
            assert declaration.result.thumbnail_url is declaration.result.description is None
            projected = project_public_search_result(
                response, row, provider_id="numilog", credential_id="__public__",
            )
            if projected is None:
                # Public HTML is not automatically safe to republish verbatim.
                # The Core must reject even a genuinely public catalogue card
                # if one of its descriptive text fields includes an URL,
                # authentication marker or account/email string.
                import re
                sensitive = re.compile(
                    r"(?i)(?:https?://|(?<!\w)(?:bearer|basic)\s+\S+|"
                    r"(?<!\w)(?:token|auth|cookie|signature|access_token|refresh_token)\s*=|"
                    r"[^\s@]+@[^\s@]+\.[^\s@]+)"
                )
                assert any(
                    type(value) is str and sensitive.search(value)
                    for value in (
                        declaration.result.title, declaration.result.author,
                        declaration.result.type, declaration.result.format,
                        declaration.result.publisher, declaration.result.published_at,
                        declaration.result.description, declaration.result.content_type,
                    )
                ), "unexpected rejection of an inert Numilog public card"
                continue
            assert projected.metadata == {"catalogue_access": "shared_read_only"}
            assert projected.owned is projected.downloadable is False
            assert projected.download_url is projected.friendly_url is None
        assert response.facets == () and response.partial is False
    finally:
        session.close()


@pytest.mark.parametrize("scope", ["all", "digital"])
def test_private_numilog_session_does_not_attest_owned_inventory(scope, monkeypatch):
    session = _session()
    owned = replace(
        PUBLIC_PAGE.items[0], source_credential="private-credential",
        source_collection="library", owned=True, downloadable=True,
        download_url="https://private.invalid/owned.acsm",
    )
    monkeypatch.setattr(session, "search", lambda *_a, **_k: [owned])
    monkeypatch.setattr(
        session, "search_public",
        lambda *_a, **_k: pytest.fail("private search cannot trigger public GET"),
    )
    try:
        response = session.search_with_response_facets("jefferson", scope)
        assert response.results == [owned]
        assert response.public_notices == ()
    finally:
        session.close()


def test_untrusted_or_mixed_row_cannot_become_public_declaration(monkeypatch):
    session = _session()
    good = PUBLIC_PAGE.items[0]
    private = replace(
        good, id="numilog:private:42",
        source_credential="private-credential", source_collection="library",
        owned=False,
    )
    wrong = replace(
        good, id="numilog:foreign:43", source_provider="another-plugin",
    )
    monkeypatch.setattr(session, "search_public", lambda *_a, **_k: [good, private, wrong])
    try:
        response = session.search_with_response_facets("jefferson", "catalog")
        assert response.results == [good, private, wrong]
        assert len(response.public_notices) == 1
        assert response.public_notices[0].result.id == good.id
        assert project_public_search_result(
            response, private,
            provider_id="numilog", credential_id="private-credential",
        ) is None
        assert project_public_search_result(
            response, wrong,
            provider_id="another-plugin", credential_id="__public__",
        ) is None
    finally:
        session.close()


def test_previous_public_response_not_reused_after_failed_query(monkeypatch):
    session = _session()
    state = {"fail": False}
    def public(*_a, **_k):
        if state["fail"]:
            raise RuntimeError("synthetic upstream forbidden")
        return list(PUBLIC_PAGE.items[:1])
    monkeypatch.setattr(session, "search_public", public)
    try:
        first = session.search_with_response_facets("first", "catalog")
        assert len(first.public_notices) == 1
        state["fail"] = True
        with pytest.raises(RuntimeError, match="synthetic upstream forbidden"):
            session.search_with_response_facets("second", "catalog")
        assert len(first.public_notices) == 1
        assert first.public_notices[0].result is not first.results[0]
    finally:
        session.close()
