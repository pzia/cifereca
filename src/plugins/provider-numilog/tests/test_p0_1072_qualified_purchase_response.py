"""Real anonymous parser, source ABI and cache observation time, fake HTTP."""
from types import SimpleNamespace

import pytest

from cifereca.core.notice_c_purchase_publication import prepare_purchase_public_observation
from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from tests.test_p0_1072_public_detail_freshness import session, count
from tests.test_public_detail_transport_p0 import URL


def control():
    return SimpleNamespace(check_cancelled=lambda: None)


def test_qualified_product_response_is_accepted_without_relabeling_private_title(monkeypatch):
    s, mono, wall = session(monkeypatch)
    candidate = SimpleNamespace(public_locator=URL, title="  fixture  ")
    try:
        response, at, hold = s._purchase_enrichment_response(
            URL, candidate.title, check_authority=lambda: True, control=control(),
        )
        row, = response.results
        assert row.title == "Fixture" and row.title != candidate.title
        assert row.source_provider == "numilog-catalog" and row.source_credential == "__public__"
        with hold():
            pass
        obs = prepare_purchase_public_observation(response, s, candidate, observed_at=at)
        assert obs.key.visibility == "public" and obs.observed_at == 1000
        assert obs.fields_copy()["search_result"]["public_bibliography"]["title"] == "Fixture"
        mono[0] += 20
        wall[0] += 20
        _, again, _ = s._purchase_enrichment_response(
            URL, "Fixture", check_authority=lambda: True, control=control(),
        )
        assert again == at and count() == 1
        s.connect()
        with pytest.raises(ProviderError, match="purchase_publication_revoked"):
            with hold():
                pytest.fail("stale producer")
    finally:
        s.close()


def test_wrong_title_or_cancelled_control_cannot_produce_response(monkeypatch):
    s, _, _ = session(monkeypatch)
    try:
        with pytest.raises(ProviderCapabilityError, match="title_conflict"):
            s._purchase_enrichment_response(
                URL, "Autre titre", check_authority=lambda: True, control=control(),
            )
        assert count() == 1
        def cancelled():
            raise RuntimeError("synthetic cancellation")
        with pytest.raises(RuntimeError, match="synthetic cancellation"):
            s._purchase_enrichment_response(
                URL, "Fixture", check_authority=lambda: True,
                control=SimpleNamespace(check_cancelled=cancelled),
            )
        assert count() == 1
    finally:
        s.close()


@pytest.mark.parametrize("language,expected", (("fra", "fr"), ("Français", None)))
def test_public_product_metadata_is_projected_without_acquired_edition_claim(monkeypatch, language, expected):
    import json
    from tests import test_public_detail_transport_p0 as transport

    node = {"@type": "Book", "url": URL, "name": "Fixture",
            "inLanguage": language, "genre": ["Policier"], "keywords": "enquête, mer",
            "typicalAgeRange": "12+", "isPartOf": {"@type": "BookSeries", "name": "Série non qualifiée"}}
    monkeypatch.setattr(transport, "BODY", '<script type="application/ld+json">'
                        + json.dumps(node) + '</script>')
    s, _, _ = session(monkeypatch)
    try:
        candidate = SimpleNamespace(public_locator=URL, title="Fixture")
        response, at, hold = s._purchase_enrichment_response(
            URL, candidate.title, check_authority=lambda: True, control=control(),
        )
        with hold():
            obs = prepare_purchase_public_observation(response, s, candidate, observed_at=at)
        bio = obs.fields_copy()["search_result"]["public_bibliography"]
        assert bio["classification"] == {"genres": ["Policier"], "keywords": ["enquête, mer"]}
        if expected is None:
            assert "language_observation" not in bio
        else:
            assert bio["language_observation"]["raw_tags"] == [language]
            assert bio["language_observation"]["languages"] == [expected]
            assert bio["language_observation"]["source_field"] == "inLanguage"
        row, = response.results
        assert not row.owned and not row.downloadable and row.format is None
        assert "series_relation" not in bio and "public_age_range_label" not in bio
    finally:
        s.close()
