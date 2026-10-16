"""#1886: fixture qualification only; no provider/network search is claimed."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest

from cifereca_numilog_html import Parser
from tests.public_search_fixture_contract import inspect_fixture, SOURCE_URL

BASE = Path(__file__).with_name('fixtures') / 'public_search_jefferson_20261016'
HTML = BASE.with_suffix('.html').read_text()
EXPECTED = json.loads(BASE.with_suffix('.expected.json').read_text())


def test_twenty_real_public_cards_match_documented_metadata_snapshot():
    assert inspect_fixture(HTML) == EXPECTED
    assert len(EXPECTED['items']) == 20
    assert EXPECTED['total_results_label'] == '242'
    assert EXPECTED['live_response_verified'] is False
    assert EXPECTED['anonymous_access_verified'] is False
    assert EXPECTED['capture_timestamp'] is None
    assert sum(len(row['authors']) for row in EXPECTED['items']) == 28
    assert all(row['publisher'] and row['published_at'] and row['summary'] and row['cover_url']
               for row in EXPECTED['items'])


def test_all_twenty_prices_are_exact_eur_decimal_metadata_not_floats():
    prices = [row['metadata']['price'] for row in EXPECTED['items']]
    assert [p['amount'] for p in prices] == [
        '9.99', '13.99', '13.99', '23.99', '6.49', '16.99', '7.49', '13.99',
        '6.99', '15.99', '40.41', '17.27', '30.78', '14.99', '14.99', '3.99',
        '12.33', '17.27', '9.99', '7.49',
    ]
    assert prices[0]['minor_units'] == 999 and prices[10]['minor_units'] == 4041
    for row, price in zip(EXPECTED['items'], prices):
        assert type(price['minor_units']) is int and price['currency'] == 'EUR'
        assert price['applies_to'] == 'displayed_card_offer'
        assert price['tax_included'] is None
        assert len(price['source_selectors']) == 3
        assert row['metadata']['price_capture_timestamp'] is None
        assert row['metadata']['source_url'] == SOURCE_URL


def test_homonymous_pdf_and_epub_are_separate_products_not_cover_isbn():
    first, second = EXPECTED['items'][1:3]
    assert first['title'] == second['title']
    assert first['source_item_id'] == '1890322'
    assert second['source_item_id'] == '1864213'
    assert first['metadata']['format_labels'] == ['PDF']
    assert second['metadata']['format_labels'] == ['ePub']
    assert len({row['source_item_id'] for row in EXPECTED['items']}) == 20
    for row in EXPECTED['items']:
        assert row['metadata']['isbn_digital'] is None
        assert row['metadata']['isbn_print'] is None
        assert row['metadata']['language'] is None
        assert row['metadata']['author_roles'] is None
        assert row['metadata']['age_range'] is None
        assert row['owned'] is False and row['downloadable'] is False
        assert row['download_url'] is None and row['source_collection'] == 'public_catalog'


def test_multiple_formats_do_not_invent_individual_prices_or_drm():
    rows = EXPECTED['items']
    multiple = [r for r in rows if len(r['metadata']['format_labels']) > 1]
    assert len(multiple) == 9
    assert all(r['metadata']['selected_format_label'] is None for r in multiple)
    assert all(len(r['metadata']['format_options']) == 2 for r in multiple)
    assert sum(r['metadata']['drm_label'] == 'sans DRM' for r in rows) == 4
    for row in rows:
        for option in row['metadata']['format_options']:
            assert option['availablelcp_raw'] == '1'
            assert option['formatid_raw'] in {'3', '14'}
        assert 'format_prices' not in row['metadata']


def test_get_pagination_and_facets_preserve_query_without_claiming_page_two():
    assert EXPECTED['current_page_label'] == '1'
    assert [(r['label'], r['GRID_PAGE']) for r in EXPECTED['pagination_links']] == [
        ('2', '1'), ('3', '2'), ('>', '1'),
    ]
    for link in EXPECTED['pagination_links']:
        q = parse_qs(urlsplit(link['url']).query)
        assert q['mode'] == ['brSimple'] and q['query'] == ['jefferson']
    facets = EXPECTED['facets']
    assert {key: sum(f['parameter'] == key for f in facets)
            for key in ('fltrTheme', 'fltrPrice', 'fltrLang', 'fltrPub')} == {
        'fltrTheme': 20, 'fltrPrice': 9, 'fltrLang': 3, 'fltrPub': 10,
    }
    assert [x['value'] for x in EXPECTED['sort_options']] == [
        'RANK', 'Date Parution desc', 'Price asc', 'TITRE asc', 'AUTEUR asc',
    ]


def test_fixture_has_no_private_shell_state_scripts_or_purchase_endpoints():
    root = Parser(HTML).root
    assert not any(n.tag in {'script', 'input', 'form', 'iframe', 'noscript'} for n in root.walk())
    for node in root.walk():
        assert not any(key.lower().startswith('on') for key in node.attrs)
        assert not any(key.startswith('data-') and key != 'data-src' for key in node.attrs)
        if 'href' in node.attrs:
            path = urlsplit(node.attrs['href']).path.lower()
            assert not any(part in path for part in ('compte', 'commande', 'panier', 'fulfillment', 'reader'))
    for marker in ('__VIEWSTATE', '__EVENTVALIDATION', 'orderid=', 'auth=', 'ModalDownload', 'URLLink.acsm'):
        assert marker not in HTML


@pytest.mark.parametrize('mutation', ['missing_price', 'bad_currency'])
def test_fixture_contract_refuses_missing_or_unqualified_currency_price(mutation):
    if mutation == 'missing_price':
        broken = HTML.replace('class="LblPrice"', 'class="absent"', 1)
    else:
        broken = re.sub(r'(<span class="LblSeparatorPrice">)\s*€', r'\1 $', HTML, count=1)
    assert broken != HTML
    with pytest.raises(ValueError):
        inspect_fixture(broken)


def test_product_observation_keeps_edition_eans_and_format_price_scope():
    product = json.loads((BASE.parent / 'public_product_1958476_20261016.observed.json').read_text())
    assert product['source_url'] == EXPECTED['items'][0]['public_url']
    assert product['raw_dom_available'] is False
    assert product['anonymous_http_client_verified'] is False
    assert product['download_url'] is None and product['owned'] is False
    eans = [product['isbn_print']] + [f['isbn_digital'] for f in product['formats']]
    assert len(set(eans)) == 3
    for ean in eans:
        assert len(ean) == 13 and ean.isdecimal()
        assert sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(ean)) % 10 == 0
    for edition in product['formats']:
        assert edition['price']['scope'] == 'format_offer'
        assert edition['price']['amount'] == EXPECTED['items'][0]['metadata']['price']['amount']
        assert edition['price']['minor_units'] == 999
        assert edition['file_size']['unit_raw'] == 'Ko'
        assert 'drm' not in edition
