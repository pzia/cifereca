"""#1886: an attested public search row must retain its exact safe product URL."""
from dataclasses import replace
from pathlib import Path
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_local_filter import refine_loaded_public_page

HTML = (Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html").read_text()
URL = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson"


def page():
    p = parse_public_search(HTML, URL)
    return replace(p, items=tuple(replace(row, source_provider="numilog-catalog") for row in p.items))


@pytest.mark.parametrize("mutation", [
    lambda url: url.replace("www.numilog.com", "evil.invalid"),
    lambda url: url.replace("https://", "http://"),
    lambda url: url + "?token=PRIVATE",
    lambda url: url + "#private",
    lambda url: url.replace(".ebook", ".acsm"),
    lambda url: url.replace("/1958476/", "/1958476/../1958476/"),
    lambda url: url.replace("www.numilog.com", "www.numilog.com@evil.invalid"),
    lambda url: url.replace("www.numilog.com", "www.numilog.com:444"),
])
def test_foreign_or_signed_public_product_url_is_not_validated_by_path_alone(mutation):
    p = page()
    first = p.items[0]
    forged = replace(p, items=(replace(first, friendly_url=mutation(first.friendly_url)), *p.items[1:]))
    with pytest.raises(ProviderCapabilityError, match="unverified_local_page") as exc:
        refine_loaded_public_page(forged, title="Jefferson")
    assert "PRIVATE" not in str(exc.value)


def test_unmodified_public_product_urls_keep_valid_local_refinement():
    p = page()
    r = refine_loaded_public_page(p, title="Jefferson")
    assert r.matching_count > 0
    assert r.public_unfiltered_total == 242
    assert all(row.owned is False and row.download_url is None for row in r.items)
