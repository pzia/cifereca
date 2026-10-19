"""#1886: anonymous Numilog details never adopt a foreign result scenario.

No HTTP, account, credentials, ACSM, DRM or personal inventory.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_detail import validate_public_detail_item
import cifereca_numilog_public_provider as provider

URL = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=jefferson"
)
HTML = (
    Path(__file__).with_name("fixtures")
    / "public_search_jefferson_20261016.html"
).read_text()
PAGE = parse_public_search(HTML, URL)
PUBLIC = replace(PAGE.items[0], source_provider="numilog-catalog")


@pytest.mark.parametrize(("attribute", "value"), [
    ("scenario", "NUMILOG_OWNED"),
    ("scenario", None),
    ("scenario", "NUMILOG_PUBLIC\nTOKEN=PRIVATE"),
    ("docbase", "KOBOV"),
    ("docbase", ""),
    ("docbase", None),
])
def test_private_or_foreign_scenario_refused_before_public_detail(attribute, value):
    forged = replace(PUBLIC, **{attribute: value})
    with pytest.raises(ProviderCapabilityError, match="detail_evidence_conflict"):
        validate_public_detail_item(forged)


def test_real_public_fixture_still_qualifies_detail_url():
    assert validate_public_detail_item(PUBLIC) == PUBLIC.friendly_url


def test_explicit_enrichment_rejects_foreign_scenario_before_http(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "cifereca_numilog_public_client.fetch_public_detail",
        lambda url: calls.append(url),
    )
    session = provider.PLUGIN.create_session(
        credential_id="__public__", label="Public", credentials={},
        options={}, config={}, storage=None, diagnostics=None,
    )
    session.connect()
    try:
        with pytest.raises(ProviderCapabilityError, match="detail_evidence_conflict"):
            session.enrich_public_detail(replace(PUBLIC, scenario="NUMILOG_OWNED"))
        assert calls == []
    finally:
        session.close()
