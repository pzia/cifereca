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
