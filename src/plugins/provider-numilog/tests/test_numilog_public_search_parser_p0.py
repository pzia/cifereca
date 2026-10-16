"""#1886: production parser vs the real sanitized capture and hostile derivatives."""
import json
from pathlib import Path
import re

import pytest

from cifereca.plugin_api import ProviderError

from cifereca_numilog_public import parse_public_search, PublicSearchParseError, MAX_HTML_BYTES, GRID_ID
from cifereca_numilog import NumilogSession

FIXTURES = Path(__file__).with_name('fixtures')
HTML = (FIXTURES / 'public_search_jefferson_20261016.html').read_text()
GOLDEN = json.loads((FIXTURES / 'public_search_jefferson_20261016.expected.json').read_text())
URL = GOLDEN['source_url']
FIRST = HTML[HTML.index('<tr class="LivreDuThemeDetail">'):HTML.index('<tr class="LivreDuThemeDetail">', HTML.index('<tr class="LivreDuThemeDetail">') + 1)]


def page_of(cards=FIRST, total=1, pager=''):
    return f'<span class="ResultatDetail">{total}</span><table id="{GRID_ID}"><tbody>{cards}</tbody></table>{pager}'


def test_real_capture_produces_twenty_public_search_results_and_exact_price_metadata():
    page = parse_public_search(HTML, URL)
    assert page.total_results == 242 and page.page_index == 0 and page.complete is False
    assert len(page.items) == 20
    assert list(page.pagination_links) == GOLDEN['pagination_links']
    assert list(page.facets) == GOLDEN['facets']
    assert list(page.sort_options) == GOLDEN['sort_options']
    assert page.next_url == GOLDEN['pagination_links'][0]['url']
    for item, expected in zip(page.items, GOLDEN['items']):
        assert item.title == expected['title']
        assert item.metadata['source_item_id'] == expected['source_item_id']
        assert item.metadata['authors'] == expected['authors']
        assert item.publisher == expected['publisher']
        assert item.published_at == expected['published_at']
        assert item.description == expected['summary']
        assert item.thumbnail_url == expected['cover_url']
        assert item.friendly_url == expected['public_url']
        assert item.metadata['collection'] == expected['collection']
        assert item.metadata['price'] == expected['metadata']['price']
        assert item.price == expected['metadata']['price']['amount'] and item.currency == 'EUR'
        for field in ('format_labels', 'selected_format_label', 'format_options', 'drm_label',
                      'isbn_digital', 'isbn_print', 'language', 'author_roles', 'age_range'):
            assert item.metadata[field] == expected['metadata'][field]
        assert item.id == 'numilog:public:' + expected['source_item_id'] == item.record_id
        assert item.source_provider == 'numilog' and item.source_credential == '__public__'
        assert item.source_collection == 'public_catalog'
        assert item.owned is False and item.downloadable is False and item.download_url is None
        assert item.metadata['price_capture_timestamp'] is None
        for evidence in item.metadata['field_evidence'].values():
            assert evidence['source_url'] == URL and evidence['kind'] == 'observed_public_html'
        assert not item.metadata['warnings']


def test_pdf_and_epub_same_title_remain_distinct_and_public_ids_cannot_be_private_acquisitions():
    page = parse_public_search(HTML, URL)
    pdf, epub = page.items[1:3]
    assert pdf.title == epub.title and pdf.record_id != epub.record_id
    assert pdf.format == 'PDF' and epub.format == 'EPUB'
    session = NumilogSession(credential_id='fictional', label='', credentials={}, options={}, config={}, diagnostics=None)
    try:
        with pytest.raises(ProviderError, match='autre connexion'):
            session._canonical_item(pdf)
    finally:
        session.close()


def test_zero_and_one_structurally_recognized_results_have_no_invented_next_page():
    # Derived structural cases, not claimed captures of the actual zero layout.
    zero = parse_public_search(page_of('', 0), URL)
    one = parse_public_search(page_of(), URL)
    assert zero.items == () and zero.total_results == 0 and zero.complete is True
    assert len(one.items) == 1 and one.total_results == 1 and one.complete is True
    assert zero.next_url is None and one.next_url is None


@pytest.mark.parametrize('mutation', ['missing_grid', 'two_grids', 'no_total', 'positive_but_empty', 'more_cards_than_total', 'duplicate_product', 'wrong_current_page'])
def test_unknown_or_inconsistent_layout_is_never_silently_zero_or_deduplicated(mutation):
    bad = {
        'missing_grid': '<input type="password">',
        'two_grids': page_of() + f'<table id="{GRID_ID}"></table>',
        'no_total': page_of().replace('class="ResultatDetail"', 'class="other"'),
        'positive_but_empty': page_of('', 1),
        'more_cards_than_total': page_of(FIRST, 0),
        'duplicate_product': page_of(FIRST + FIRST, 2),
        'wrong_current_page': HTML.replace('class="PagerCurrentPage">1', 'class="PagerCurrentPage">2'),
    }[mutation]
    with pytest.raises(PublicSearchParseError):
        parse_public_search(bad, URL)


@pytest.mark.parametrize('mutation', ['missing_fraction', 'bad_fraction', 'currency', 'duplicate_price', 'arabic_digits'])
def test_partial_ambiguous_or_non_eur_prices_are_rejected_with_no_secret(mutation):
    bad = FIRST
    if mutation == 'missing_fraction':
        bad = bad.replace('class="LblDecimal"', 'class="other"', 1)
    elif mutation == 'bad_fraction':
        bad = re.sub(r'(<span class="LblDecimal">)[0-9]{2}', r'\g<1>PRIVATE_MARKER', bad, count=1)
    elif mutation == 'currency':
        bad = re.sub(r'(<span class="LblSeparatorPrice">)\s*€', r'\g<1> $', bad, count=1)
    elif mutation == 'arabic_digits':
        bad = re.sub(r'(<span class="LblPrice">)[0-9]+', r'\g<1>٩', bad, count=1)
    else:
        bad = bad.replace('<td>', '<td><div class="ColPrice"><span class="LblPrice">999</span></div>', 1)
    assert bad != FIRST
    with pytest.raises(PublicSearchParseError) as error:
        parse_public_search(page_of(bad), URL)
    assert 'PRIVATE_MARKER' not in str(error.value)


def test_missing_optional_metadata_is_unknown_not_inferred_from_title_cover_or_facets():
    bad = FIRST
    for name in ('product_meta', 'ColPrice', 'Author', 'FormatLivre', 'AvailableFormat', 'list-group-item-text', 'lazy-image'):
        # Remove selector membership while retaining real neighboring content.
        bad = re.sub(r'(?<=class=")' + name + r'(?=[ "\t])', 'unselected', bad)
        bad = bad.replace(' ' + name + ' ', ' unselected ')
    item = parse_public_search(page_of(bad), URL).items[0]
    assert item.author is None and item.publisher is None and item.published_at is None
    assert item.price is None and item.currency is None and item.metadata['price'] is None
    assert item.description is None and item.thumbnail_url is None
    assert item.metadata['format_labels'] == [] and item.metadata['isbn_print'] is None
    assert item.metadata['field_evidence'].keys() == {'title'}
    assert item.metadata['warnings'] == ['price_not_published']


@pytest.mark.parametrize('href', [
    'https://evil.example/1958476/book.ebook', '//evil.example/1958476/book.ebook',
    '../../../1958476/book.ebook', '../../Pages/Compte/MesLivres.aspx',
    '../../1958476/book.ebook?auth=PRIVATE_MARKER', '../../1958476/book.ebook#PRIVATE_MARKER',
    '../../1958476/book.ebook\n', '../../1958476/book.ebook\u202e',
    '../../1958476/%2e%2e.ebook', 'https://www.numilog.com:443/1958476/book.ebook',
])
def test_product_links_never_allow_private_queries_traversal_or_unexpected_origins(href):
    # All title/cover links on the card share the first product URL.
    bad = FIRST.replace('../../1958476/jefferson-prend-des-risques.ebook', href)
    with pytest.raises(PublicSearchParseError) as error:
        parse_public_search(page_of(bad), URL)
    assert 'PRIVATE_MARKER' not in str(error.value) and 'evil.example' not in str(error.value)


@pytest.mark.parametrize('url', [
    URL + '&auth=PRIVATE_MARKER', URL + '&mode=brSimple', URL + '&GRID_PAGE=-1',
    URL + '&GRID_PAGE=10000', URL.replace('https:', 'http:'),
    URL.replace('www.numilog.com', 'www.numilog.com:443'),
    URL.replace('query=jefferson', 'query=%0aPRIVATE_MARKER'),
])
def test_source_url_cannot_persist_a_signed_or_ambiguous_query(url):
    with pytest.raises(PublicSearchParseError) as error:
        parse_public_search(HTML, url)
    assert 'PRIVATE_MARKER' not in str(error.value)


@pytest.mark.parametrize('mutation', ['query', 'private_parameter', 'external', 'source_page'])
def test_pagination_cannot_change_search_scope_or_fake_which_page_was_read(mutation):
    bad, source = HTML, URL
    if mutation == 'source_page':
        source += '&GRID_PAGE=1'
    elif mutation == 'query':
        bad = bad.replace('jefferson&amp;GRID_PAGE=1', 'other&amp;GRID_PAGE=1')
    elif mutation == 'private_parameter':
        bad = bad.replace('&amp;GRID_PAGE=1', '&amp;GRID_PAGE=1&amp;auth=PRIVATE_MARKER')
    else:
        bad = bad.replace('/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&amp;query=jefferson&amp;GRID_PAGE=1', 'https://evil.example/private')
    with pytest.raises(PublicSearchParseError):
        parse_public_search(bad, source)


def test_later_page_uses_observed_index_without_claiming_a_real_second_capture():
    pager = '<tr class="pagination"><span class="PagerCurrentPage">2</span><a href="/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&amp;query=jefferson&amp;GRID_PAGE=0">1</a><a href="/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&amp;query=jefferson&amp;GRID_PAGE=2">3</a></tr>'
    page = parse_public_search(page_of(pager=pager), URL + '&GRID_PAGE=1')
    assert page.page_index == 1 and page.complete is False
    assert 'GRID_PAGE=2' in page.next_url
    assert page.items[0].metadata['rank_on_page'] == 1


def test_untrusted_cover_date_and_non_bibliographic_shell_never_add_secret_metadata():
    body = FIRST.replace('2026-10-08', '2026-99-99').replace(
        'https://couverture.numilog.com/9782075241939_GRANDE.jpg', 'https://evil.example/PRIVATE_MARKER?auth=PRIVATE_MARKER')
    shell = '<input type="password" value="PRIVATE_MARKER"><script>var x="PRIVATE_MARKER"</script><span class="Author">PRIVATE_MARKER</span>'
    item = parse_public_search(shell + page_of(body), URL).items[0]
    assert item.published_at is None and item.thumbnail_url is None
    assert set(item.metadata['warnings']) == {'unqualified_publication_date', 'unqualified_cover'}
    assert 'PRIVATE_MARKER' not in repr(item.metadata) and item.author == 'Jean-Claude Mourlevat'


def test_size_structure_and_card_count_are_bounded():
    with pytest.raises(PublicSearchParseError, match='html_size'):
        parse_public_search('é' * (MAX_HTML_BYTES // 2 + 1), URL)
    with pytest.raises(PublicSearchParseError, match='html_structure'):
        parse_public_search('<div>' * 102, URL)
    with pytest.raises(PublicSearchParseError):
        parse_public_search(page_of(FIRST * 101, 101), URL)
