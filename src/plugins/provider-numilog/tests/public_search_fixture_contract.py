"""Offline contract reader for the documented #1886 fixture, not provider I/O.

It prepares/validates expected metadata. This is not a production public-search
implementation and deliberately provides no HTTP, credentials or buy action.
"""
import re
from urllib.parse import urljoin, urlsplit, parse_qs

from cifereca_numilog_html import Parser, classes

SOURCE_URL = 'https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson'
GRID_ID = 'ctl02_CentrePage_ListeLivres1_GridView1'


def _class(node, name):
    return [n for n in node.walk() if name in classes(n)]


def _one_text(node, name):
    matches = _class(node, name)
    if len(matches) != 1 or not matches[0].text():
        raise ValueError('fixture_field_cardinality')
    return matches[0].text()


def _book_url(href):
    url = urljoin(SOURCE_URL, href)
    p = urlsplit(url)
    if (p.scheme != 'https' or p.netloc != 'www.numilog.com' or p.query or p.fragment
            or not re.fullmatch(r'/[0-9]+/[A-Za-z0-9._-]+\.ebook', p.path)):
        raise ValueError('fixture_public_url')
    return url


def inspect_fixture(html):
    root = Parser(html).root
    grids = [n for n in root.walk() if n.attrs.get('id') == GRID_ID]
    if len(grids) != 1:
        raise ValueError('fixture_grid_cardinality')
    records = []
    for rank, card in enumerate(_class(grids[0], 'LivreDuThemeDetail'), 1):
        title = _one_text(card, 'TitleBook')
        link = _class(card, 'TitleBook')[0]
        url = _book_url(link.attrs['href'])
        product_id = urlsplit(url).path.split('/')[1]
        meta = _class(card, 'product_meta')
        if len(meta) != 1:
            raise ValueError('fixture_metadata_cardinality')
        info = {}
        for row in _class(meta[0], 'LabelInfos'):
            text = row.text()
            for prefix, key in (('Parution :', 'publication_date'), ('Format(s) :', 'formats'),
                                ('Éditeur :', 'publisher'), ('Collection :', 'collection')):
                if text.startswith(prefix):
                    info[key] = text[len(prefix):].strip() or None
        major, fraction, symbol = (_one_text(card, key) for key in
                                   ('LblPrice', 'LblDecimal', 'LblSeparatorPrice'))
        if not major.isdecimal() or not re.fullmatch(r'[0-9]{2}', fraction) or symbol != '€':
            raise ValueError('fixture_price_structure')
        price = {
            'amount': str(int(major)) + '.' + fraction,
            'minor_units': int(major) * 100 + int(fraction), 'currency': 'EUR',
            'display_reconstructed': f'{major},{fraction} {symbol}',
            'raw_parts': [major, fraction, symbol],
            'applies_to': 'displayed_card_offer', 'tax_included': None,
            'source_selectors': {'major': '.ColPrice .LblPrice', 'fraction': '.ColPrice .LblDecimal', 'currency': '.ColPrice .LblSeparatorPrice'},
        }
        format_label = _one_text(card, 'FormatLivre')
        selected_format = None if format_label == 'Choix format' else format_label
        format_options = [
            {'label': n.text(), 'formatid_raw': n.attrs.get('formatid'),
             'availablelcp_raw': n.attrs.get('availablelcp')}
            for n in _class(card, 'AvailableFormat')
        ]
        images = [n for n in card.walk() if n.tag == 'img' and 'lazy-image' in classes(n)]
        if len(images) != 1:
            raise ValueError('fixture_cover_cardinality')
        cover = images[0].attrs.get('data-src') or images[0].attrs.get('src') or None
        p = urlsplit(cover or '')
        if cover and (p.scheme != 'https' or p.netloc != 'couverture.numilog.com' or p.query or p.fragment):
            raise ValueError('fixture_cover_origin')
        summary = [n.text() for n in card.walk() if n.tag == 'p' and 'list-group-item-text' in classes(n)]
        records.append({
            'rank': rank, 'source_item_id': product_id, 'title': title,
            'authors': [n.text() for n in _class(card, 'Author')],
            'publisher': info.get('publisher'), 'collection': info.get('collection'),
            'published_at': info.get('publication_date'),
            'summary': summary[0] if len(summary) == 1 else None,
            'summary_kind': 'listing_excerpt', 'public_url': url, 'cover_url': cover,
            'source_collection': 'public_catalog', 'owned': False, 'downloadable': False,
            'download_url': None,
            'metadata': {
                'price': price, 'format_labels': (info.get('formats') or '').split(', '),
                'selected_format_label': selected_format, 'format_options': format_options,
                'drm_label': 'sans DRM' if 'sans drm' in format_label.casefold() else None,
                'title_attribute': link.attrs.get('title'),
                'isbn_digital': None, 'isbn_print': None, 'language': None,
                'author_roles': None, 'age_range': None,
                'source_url': SOURCE_URL,
                'price_observation_kind': 'user_supplied_snapshot',
                'price_capture_timestamp': None,
            },
        })
    facets = []
    for link in _class(root, 'panel-sous-filtre'):
        query = parse_qs(urlsplit(link.attrs['href']).query)
        name = next(key for key in query if key.startswith('fltr'))
        facets.append({'label': link.text(), 'parameter': name, 'value': query[name][0],
                       'url': urljoin(SOURCE_URL, link.attrs['href'])})
    pager = _class(root, 'pagination')[0]
    pages = [{'label': n.text(), 'url': urljoin(SOURCE_URL, n.attrs['href']),
              'GRID_PAGE': parse_qs(urlsplit(n.attrs['href']).query)['GRID_PAGE'][0]}
             for n in pager.walk() if n.tag == 'a']
    sort = next(n for n in root.walk() if n.attrs.get('id') == 'ListeTrier')
    return {
        'schema': 'numilog.public_search.fixture/1', 'source_url': SOURCE_URL,
        'qualified_on': '2026-10-16', 'capture_timestamp': None,
        'live_response_verified': False, 'anonymous_access_verified': False,
        'total_results_label': _class(root, 'ResultatDetail')[0].text(),
        'current_page_label': _one_text(root, 'PagerCurrentPage'),
        'pagination_links': pages, 'facets': facets,
        'sort_options': [{'value': n.attrs['value'], 'label': n.text()}
                         for n in sort.walk() if n.tag == 'option'],
        'items': records,
    }
