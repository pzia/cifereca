"""P0 #3867: public catalogue URL qualification survives Core mirroring.

Real sanitized Numilog public search capture; no authenticated session, GET,
signed link, file acquisition, or purchase/permission inference.
"""
from pathlib import Path
from dataclasses import replace

from cifereca.core.search_orchestration import _numilog_public_catalog_url
from cifereca_numilog_public import parse_public_search


FIXTURES = Path(__file__).parent / "fixtures"
HTML = (FIXTURES / "public_search_jefferson_20261016.html").read_text()
URL = ("https://www.numilog.com/Pages/Recherche/"
       "ResultatRecherche.aspx?mode=brSimple&query=jefferson")


def test_public_search_verified_product_urls_are_not_lost_at_core_boundary():
    page = parse_public_search(HTML, URL)
    assert len(page.items) == 20
    for row in page.items:
        assert row.scenario == "NUMILOG_PUBLIC"
        assert row.source_collection == "public_catalog"
        assert row.owned is False and row.downloadable is False
        assert row.download_url is None
        assert row.metadata["public_url_evidence"] == "numilog.public_search_product_card"
        assert _numilog_public_catalog_url(
            row.friendly_url,
            evidence=row.metadata["public_url_evidence"],
        ) == row.friendly_url
        assert _numilog_public_catalog_url(row.friendly_url) is None


def test_public_search_proof_cannot_turn_signed_or_private_urls_into_notices():
    row = parse_public_search(HTML, URL).items[0]
    assert row.friendly_url
    for forged in (
        row.friendly_url + "?token=PRIVATE",
        row.friendly_url + "?ref=library",
        row.friendly_url + "#secret",
        row.friendly_url.replace("https://", "http://"),
        row.friendly_url.replace("www.numilog.com", "127.0.0.1"),
        row.friendly_url.replace(".ebook", ".acsm"),
    ):
        assert _numilog_public_catalog_url(
            forged, evidence=row.metadata["public_url_evidence"],
        ) is None


def test_legacy_private_owned_card_marker_retains_its_only_benign_query():
    safe = "https://www.numilog.com/livres/old-book?ref=library"
    assert _numilog_public_catalog_url(
        safe, evidence="numilog.library_title_link",
    ) == safe
    assert _numilog_public_catalog_url(
        safe, evidence="numilog.public_search_product_card",
    ) is None
