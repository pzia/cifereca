"""Bounded, offline parsing of Numilog's attested public search HTML (#1886).

No HTTP, cookies, account lookup, acquisition or implicit ownership. The
provider's private search is not switched to this parser by this increment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from cifereca.plugin_api import SearchResult
from cifereca_numilog_html import Parser, ParseError, classes, public_book_url

SEARCH_PATH = '/Pages/Recherche/ResultatRecherche.aspx'
GRID_ID = 'ctl02_CentrePage_ListeLivres1_GridView1'
MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_CARDS = 100
_FILTERS = frozenset({'fltrTheme', 'fltrPrice', 'fltrLang', 'fltrPub'})
_PARAMS = _FILTERS | {'mode', 'query', 'GRID_PAGE'}
_BOOK_PATH = r'/[0-9]{1,12}/[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.ebook'


class PublicSearchParseError(ParseError):
    """Fixed public code only; never reflect an upstream URL or body."""

    def __init__(self, code):
        self.code = code
        super().__init__('Numilog public search: ' + code)


def _fail(code):
    raise PublicSearchParseError(code)


def _safe_text(value, maximum):
    return (type(value) is str and len(value) <= maximum
            and not any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} for c in value))


def _nodes(node, name):
    return [n for n in node.walk() if name in classes(n) and n.tag not in {'script', 'style'}]


def _optional(node, name, maximum=2000):
    matches = _nodes(node, name)
    if len(matches) > 1:
        _fail('ambiguous_field')
    if not matches:
        return None
    value = matches[0].text()
    if not _safe_text(value, maximum):
        _fail('invalid_field')
    return value or None


def _query(url):
    if not _safe_text(url, 4096) or any(c.isspace() for c in url) or '\\' in url:
        _fail('invalid_search_url')
    try:
        p = urlsplit(url)
        if (p.scheme != 'https' or p.netloc != 'www.numilog.com'
                or p.path != SEARCH_PATH or p.fragment):
            _fail('invalid_search_url')
        q = parse_qs(p.query, keep_blank_values=True, max_num_fields=10)
        if (set(q) - _PARAMS or any(len(v) != 1 for v in q.values())
                or q.get('mode') != ['brSimple'] or 'query' not in q
                or not _safe_text(q['query'][0], 512)):
            _fail('invalid_search_url')
        if 'GRID_PAGE' in q and not re.fullmatch(r'0|[1-9][0-9]{0,3}', q['GRID_PAGE'][0]):
            _fail('invalid_search_url')
        if any(not _safe_text(q[k][0], 128) or not q[k][0] for k in q.keys() & _FILTERS):
            _fail('invalid_search_url')
        return {k: v[0] for k, v in q.items()}
    except ValueError:
        _fail('invalid_search_url')


def _search_link(href, source_url):
    if not _safe_text(href, 4096) or '\\' in href or any(c.isspace() for c in href):
        _fail('invalid_search_url')
    # Relative links are accepted only in the form observed in the fixture.
    if not (href.startswith('https://www.numilog.com' + SEARCH_PATH + '?')
            or href.startswith(SEARCH_PATH + '?')):
        _fail('invalid_search_url')
    value = urljoin(source_url, href)
    return value, _query(value)


def _product_link(href, source_url):
    if not _safe_text(href, 512) or '\\' in href or any(c.isspace() for c in href):
        _fail('invalid_product_url')
    # Validate the RAW relative shape before urljoin removes dot segments.
    # Only ../../ID/slug.ebook is attested; arbitrary traversal is rejected.
    if not re.fullmatch(r'(?:\.\./\.\./|/|https://www\.numilog\.com/)' + _BOOK_PATH[1:], href):
        _fail('invalid_product_url')
    value = public_book_url(urljoin(source_url, href))
    if value is None:
        _fail('invalid_product_url')
    return value


def _price(card):
    columns = _nodes(card, 'ColPrice')
    if not columns:
        return None
    if len(columns) != 1:
        _fail('ambiguous_price')
    major, fraction, symbol = (_optional(columns[0], name, 16) for name in
                               ('LblPrice', 'LblDecimal', 'LblSeparatorPrice'))
    if major is None and fraction is None and symbol is None:
        return None
    if (not re.fullmatch(r'[0-9]{1,8}', major or '')
            or not re.fullmatch(r'[0-9]{2}', fraction or '') or symbol != '€'):
        _fail('unqualified_price')
    return {
        'amount': f'{int(major)}.{fraction}', 'minor_units': int(major) * 100 + int(fraction),
        'currency': 'EUR', 'display_reconstructed': f'{major},{fraction} {symbol}',
        'raw_parts': [major, fraction, symbol], 'applies_to': 'displayed_card_offer',
        'tax_included': None,
        'source_selectors': {'major': '.ColPrice .LblPrice', 'fraction': '.ColPrice .LblDecimal',
                             'currency': '.ColPrice .LblSeparatorPrice'},
    }


def _card(card, source_url, rank, page_index):
    links = _nodes(card, 'TitleBook')
    if len(links) != 1 or links[0].tag != 'a':
        _fail('ambiguous_product')
    title = _optional(card, 'TitleBook')
    if not title:
        _fail('missing_title')
    url = _product_link(links[0].attrs.get('href'), source_url)
    product_id = urlsplit(url).path.split('/')[1]
    info = {}
    metas = _nodes(card, 'product_meta')
    if len(metas) > 1:
        _fail('ambiguous_metadata')
    if metas:
        for node in _nodes(metas[0], 'LabelInfos'):
            text = node.text()
            for prefix, key in (('Parution :', 'published_at'), ('Format(s) :', 'formats'),
                                ('Éditeur :', 'publisher'), ('Collection :', 'collection')):
                if text.startswith(prefix):
                    if key in info:
                        _fail('ambiguous_metadata')
                    value = text[len(prefix):].strip()
                    if not _safe_text(value, 2000):
                        _fail('invalid_field')
                    info[key] = value or None
    authors = [n.text() for n in _nodes(card, 'Author')]
    if len(authors) > 32 or any(not a or not _safe_text(a, 1000) for a in authors):
        _fail('invalid_authors')
    summary = _optional(card, 'list-group-item-text', 16000)
    cover = None
    images = [n for n in card.walk() if n.tag == 'img' and 'lazy-image' in classes(n)]
    if len(images) > 1:
        _fail('ambiguous_cover')
    warnings = []
    if images:
        raw_cover = images[0].attrs.get('data-src') or images[0].attrs.get('src')
        if type(raw_cover) is str and re.fullmatch(r'https://couverture\.numilog\.com/[0-9]{1,32}_GRANDE\.jpg', raw_cover):
            cover = raw_cover
        elif raw_cover:
            warnings.append('unqualified_cover')
    published = info.get('published_at')
    if published:
        try:
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', published):
                raise ValueError
            date.fromisoformat(published)
        except ValueError:
            published = None
            warnings.append('unqualified_publication_date')
    format_labels = (info.get('formats') or '').split(', ') if info.get('formats') else []
    if len(format_labels) > 10 or any(len(f) > 80 for f in format_labels):
        _fail('invalid_formats')
    selected = _optional(card, 'FormatLivre', 80)
    if selected == 'Choix format':
        selected = None
    options = []
    for option in _nodes(card, 'AvailableFormat'):
        label = option.text()
        if len(options) >= 10 or not _safe_text(label, 80):
            _fail('invalid_formats')
        # Only qualified attribute values are copied; never an arbitrary
        # JavaScript/event attribute or per-format price/DRM inference.
        raw_id = option.attrs.get('formatid')
        raw_lcp = option.attrs.get('availablelcp')
        options.append({'label': label,
                        'formatid_raw': raw_id if raw_id in {'3', '14'} else None,
                        'availablelcp_raw': raw_lcp if raw_lcp in {'0', '1'} else None})
    price = _price(card)
    if price is None:
        warnings.append('price_not_published')
    metadata = {
        'source_url': source_url, 'source_item_id': product_id, 'rank_on_page': rank,
        'page_index': page_index, 'authors': authors, 'collection': info.get('collection'),
        'summary_kind': 'listing_excerpt', 'price': price,
        'price_observation_kind': 'public_search_html', 'price_capture_timestamp': None,
        'format_labels': format_labels, 'selected_format_label': selected,
        'format_options': options,
        'drm_label': 'sans DRM' if selected and re.search(r'\bsans DRM\b', selected, re.I) else None,
        'isbn_digital': None, 'isbn_print': None, 'language': None,
        'author_roles': None, 'age_range': None,
        'warnings': warnings,
    }
    selectors = {
        'title': 'a.TitleBook', 'authors': 'a.Author', 'publisher': '.product_meta .LabelInfos[Éditeur]',
        'collection': '.product_meta .LabelInfos[Collection]', 'published_at': '.product_meta .LabelInfos[Parution]',
        'summary': 'p.list-group-item-text', 'cover_url': 'img.lazy-image[data-src]',
        'format_labels': '.product_meta .LabelInfos[Format(s)]',
    }
    values = {'title': title, 'authors': authors, 'publisher': info.get('publisher'),
              'collection': info.get('collection'), 'published_at': published,
              'summary': summary, 'cover_url': cover, 'format_labels': format_labels}
    # label-qualified source descriptions, not executable CSS for the labels.
    metadata['field_evidence'] = {
        key: {'source_url': source_url, 'source': selectors[key], 'kind': 'observed_public_html'}
        for key, value in values.items() if value
    }
    if price:
        metadata['field_evidence']['price'] = {
            'source_url': source_url, 'source': '.ColPrice', 'kind': 'observed_public_html',
        }
    result_id = 'numilog:public:' + product_id
    only_format = format_labels[0].casefold() if len(format_labels) == 1 else ''
    format_name = 'EPUB' if only_format in {'epub', 'epub sans drm'} else 'PDF' if only_format == 'pdf' else None
    return SearchResult(
        id=result_id, record_id=result_id, docbase='NUMILOG', medium='digital', title=title,
        author=' ; '.join(authors) or None, type='Livre numérique', format=format_name,
        publisher=info.get('publisher'), published_at=published, description=summary,
        thumbnail_url=cover, friendly_url=url, scenario='NUMILOG_PUBLIC',
        source_provider='numilog', source_credential='__public__', source_collection='public_catalog',
        owned=False, downloadable=False, download_url=None,
        price=price['amount'] if price else None, currency=price['currency'] if price else None,
        metadata=metadata,
    )


@dataclass(frozen=True)
class PublicSearchPage:
    items: tuple[SearchResult, ...]
    total_results: int
    page_index: int
    next_url: str | None
    pagination_links: tuple[dict, ...]
    facets: tuple[dict, ...]
    sort_options: tuple[dict, ...]
    complete: bool


def parse_public_search(text, source_url):
    """Parse one page, never fetch another; invalid layouts cannot mean zero."""
    query = _query(source_url)
    page_index = int(query.get('GRID_PAGE', '0'))
    if type(text) is not str or len(text) > MAX_HTML_BYTES or len(text.encode('utf-8', errors='replace')) > MAX_HTML_BYTES:
        _fail('html_size')
    try:
        root = Parser(text).root
    except ParseError:
        _fail('html_structure')
    grids = [n for n in root.walk() if n.tag == 'table' and n.attrs.get('id') == GRID_ID]
    if len(grids) != 1:
        _fail('unknown_search_layout')
    totals = [n.text() for n in _nodes(root, 'ResultatDetail') if re.fullmatch(r'[0-9]{1,9}', n.text())]
    if len(totals) != 1:
        _fail('unqualified_total')
    total = int(totals[0])
    cards = _nodes(grids[0], 'LivreDuThemeDetail')
    if len(cards) > MAX_CARDS or len(cards) > total or (total and not cards):
        _fail('inconsistent_results')
    items = tuple(_card(card, source_url, rank, page_index) for rank, card in enumerate(cards, 1))
    if len({item.record_id for item in items}) != len(items):
        _fail('duplicate_product')
    pagers = _nodes(root, 'pagination')
    if len(pagers) > 1:
        _fail('ambiguous_pagination')
    pages, next_urls = [], set()
    if pagers:
        current = _optional(pagers[0], 'PagerCurrentPage', 8)
        if current != str(page_index + 1):
            _fail('inconsistent_page')
        for link in (n for n in pagers[0].walk() if n.tag == 'a'):
            if len(pages) >= 100:
                _fail('excessive_pagination')
            url, target = _search_link(link.attrs.get('href'), source_url)
            if 'GRID_PAGE' not in target or {k: v for k, v in target.items() if k != 'GRID_PAGE'} != {k: v for k, v in query.items() if k != 'GRID_PAGE'}:
                _fail('pagination_query_changed')
            label = link.text()
            if not _safe_text(label, 32):
                _fail('invalid_pagination')
            pages.append({'label': label, 'url': url, 'GRID_PAGE': target['GRID_PAGE']})
            if int(target['GRID_PAGE']) == page_index + 1:
                next_urls.add(url)
    # Equivalent textual query ordering must not create multiple next links.
    next_queries = {urlencode(sorted(_query(u).items())) for u in next_urls}
    if len(next_queries) > 1:
        _fail('ambiguous_pagination')
    facets = []
    for link in _nodes(root, 'panel-sous-filtre'):
        if len(facets) >= 200 or link.tag != 'a':
            _fail('invalid_facets')
        url, target = _search_link(link.attrs.get('href'), source_url)
        changed = [key for key in target if key in _FILTERS and target.get(key) != query.get(key)]
        if len(changed) != 1 or target.get('mode') != query['mode'] or target.get('query') != query['query']:
            _fail('invalid_facets')
        key = changed[0]
        label = link.text()
        if not _safe_text(label, 2000):
            _fail('invalid_facets')
        facets.append({'label': label, 'parameter': key, 'value': target[key], 'url': url})
    sorts = [n for n in root.walk() if n.tag == 'select' and n.attrs.get('id') == 'ListeTrier']
    if len(sorts) > 1:
        _fail('ambiguous_sort')
    sort_options = []
    if sorts:
        for option in (n for n in sorts[0].walk() if n.tag == 'option'):
            value, label = option.attrs.get('value'), option.text()
            if len(sort_options) >= 20 or not _safe_text(value, 80) or not _safe_text(label, 200):
                _fail('invalid_sort')
            sort_options.append({'value': value, 'label': label})
    return PublicSearchPage(items, total, page_index, sorted(next_urls)[0] if next_urls else None,
                            tuple(pages), tuple(facets), tuple(sort_options),
                            page_index == 0 and total == len(items) and not next_urls)
