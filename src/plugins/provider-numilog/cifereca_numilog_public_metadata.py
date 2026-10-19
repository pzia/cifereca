"""Visible declarations from the observed public product WebForms layout.

These labels describe the commercial product, never a purchased variant.
Missing layouts/labels supply no evidence; no URL, script or form is followed.
"""
from datetime import date
import re
import unicodedata
from urllib.parse import urlsplit

from cifereca_numilog_html import Parser, ParseError, classes, public_book_url

_PREFIX = 'ctl02_CentrePage_FicheDetaille1_'
_CONTAINER = 'ctl02_CentrePage_dvFicheDetail'
_FORMAT = 'ctl02_CentrePage_MiseEnAvantLivre_BuyWithFormatSelect_BuySelectedFormat'
_SELECTORS = {
    'brand': _PREFIX + 'rptEditeurs_ctl00_HLEditeurCol',
    'collection': _PREFIX + 'lblCollectionLivre',
    'publication_date': _PREFIX + 'lblDateParution',
    'pagination_label': _PREFIX + 'lblNbrePage',
    'language_label': _PREFIX + 'lblLanguage',
    'selected_format_label': _FORMAT,
}
_IGNORED = {'script', 'style', 'template', 'noscript', 'iframe', 'nav',
            'footer', 'header', 'aside', 'dialog', 'svg'}


class PublicMetadataError(ValueError):
    def __init__(self):
        super().__init__('Numilog public metadata: evidence_unverified')


class _Reader(Parser):
    def handle_starttag(self, tag, attrs):
        # Parser's dict conversion must not silently choose one duplicate ID
        # or canonical form action supplied by malformed markup.
        if len(dict(attrs)) != len(attrs):
            ids = {v for k, v in attrs if k == 'id'}
            if (tag in {'form', 'h1'} or ids & (set(_SELECTORS.values()) | {_CONTAINER})):
                raise PublicMetadataError()
            # Irrelevant malformed markup (observed duplicate style in a
            # navigation link) supplies no labels or descendant evidence.
            attrs = list(dict(attrs).items()) + [('hidden', '')]
        super().handle_starttag(tag, attrs)


def _hidden(node):
    # HTML attributes are case-insensitive in their relevant token values,
    # while CSS can separate property/value with tabs or newlines.
    # A hidden product label or parent cannot attest commercial metadata.
    aria = node.attrs.get('aria-hidden')
    style = node.attrs.get('style')
    compact = ''.join(style.casefold().split()) if type(style) is str else ''
    return ('hidden' in node.attrs
            or (type(aria) is str and aria.strip().casefold() == 'true')
            or 'display:none' in compact
            or 'visibility:hidden' in compact)


def _visible(node, *, wrapper=False):
    if (node.tag in _IGNORED or (node.tag == 'form' and not wrapper)
            or _hidden(node)):
        return
    yield node
    for child in node.children:
        yield from _visible(child)


def _value(node):
    # Excluded descendants must never bleed into an otherwise qualified label.
    if any(n.tag in _IGNORED or n.tag == 'form' or _hidden(n)
           or n.attrs.get('style')
           for n in node.walk() if n is not node):
        raise PublicMetadataError()
    text = node.text()
    if not text or len(text) > 256 or any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} for c in text):
        raise PublicMetadataError()
    return text


def extract_public_metadata(html, product_url, expected_title):
    """Return only exact visible labels in the canonical product wrapper."""
    if (type(html) is not str or len(html.encode('utf-8', 'replace')) > 1024 * 1024
            or type(product_url) is not str
            or public_book_url(product_url) != product_url
            or urlsplit(product_url).netloc != 'www.numilog.com'):
        raise PublicMetadataError()
    empty = {'product_url': product_url, 'fields': {}, 'field_evidence': {}}
    try:
        root = _Reader(html).root
    except (ParseError, RecursionError):
        raise PublicMetadataError() from None
    def forms_in(node):
        # A canonical-looking wrapper in an ignored/foreign form is no
        # product context. Respect all ancestors, including the body.
        if (node.tag in _IGNORED or _hidden(node)
                or node.attrs.get('style')):
            return
        if node.tag == 'form':
            if node.attrs.get('id') == 'aspnetForm':
                yield node
            return
        for child in node.children:
            yield from forms_in(child)
    forms = list(forms_in(root))
    # Duplicate wrappers anywhere stay ambiguous even when nested.
    if sum(n.tag == 'form' and n.attrs.get('id') == 'aspnetForm' for n in root.walk()) > 1:
        raise PublicMetadataError()
    if not forms:
        return empty
    if len(forms) != 1:
        raise PublicMetadataError()
    form = forms[0]
    if (form.attrs.get('name') != 'aspnetForm'
            or form.attrs.get('method', '').casefold() != 'post'
            or form.attrs.get('action') != urlsplit(product_url).path):
        return empty
    nodes = list(_visible(form, wrapper=True))
    titles = [n for n in nodes if n.tag == 'h1']
    if (len(titles) != 1 or 'Titlebook' not in classes(titles[0])
            or titles[0].text() != expected_title):
        return empty
    details = [n for n in nodes if n.attrs.get('id') == _CONTAINER]
    if len(details) > 1:
        raise PublicMetadataError()
    detail_nodes = list(_visible(details[0])) if details else []
    if details and not any('titledetailDRM' in classes(n) and n.text() == 'Détails'
                           for n in detail_nodes):
        detail_nodes = []
    fields, evidence = {}, {}
    for field, selector in _SELECTORS.items():
        candidates = [n for n in (nodes if field == 'selected_format_label' else detail_nodes)
                      if n.attrs.get('id') == selector]
        if len(candidates) > 1:
            raise PublicMetadataError()
        if not candidates:
            continue
        node = candidates[0]
        if node.tag != ('a' if field == 'brand' else 'span'):
            raise PublicMetadataError()
        value = _value(node)
        if value.casefold() in {'n.c', 'n.c.', 'non communiqué', 'non communiquée'}:
            continue
        if field == 'brand':
            labels = [n for n in detail_nodes if n.attrs.get('id') == _PREFIX + 'LblPublisher']
            if len(labels) != 1 or labels[0].text() != 'Marque':
                continue
        if field == 'publication_date':
            try:
                if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
                    raise ValueError
                date.fromisoformat(value)
            except ValueError:
                raise PublicMetadataError() from None
        fields[field] = value
        evidence[field] = {'source': 'public_product_visible_label',
                           'source_url': product_url, 'selector_id': selector,
                           'kind': 'observed_public_html',
                           'edition_proven': False, 'rights_proven': False}
    return {'product_url': product_url, 'fields': fields, 'field_evidence': evidence}


def validated_public_metadata(detail, product_url):
    """Closed optional provenance section, also checked by owned/public merges."""
    data = detail.get('public_html_metadata')
    if data is None:
        return None
    if (type(data) is not dict or set(data) != {'product_url', 'fields', 'field_evidence'}
            or data['product_url'] != product_url or type(data['fields']) is not dict
            or type(data['field_evidence']) is not dict
            or not set(data['fields']).issubset(_SELECTORS)
            or set(data['fields']) != set(data['field_evidence'])):
        raise PublicMetadataError()
    for field, value in data['fields'].items():
        proof = data['field_evidence'][field]
        expected = {'source': 'public_product_visible_label', 'source_url': product_url,
                    'selector_id': _SELECTORS[field], 'kind': 'observed_public_html',
                    'edition_proven': False, 'rights_proven': False}
        if (type(value) is not str or not 0 < len(value) <= 256
                or any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} for c in value)
                or proof != expected or type(proof) is not dict
                or proof.get('edition_proven') is not False
                or proof.get('rights_proven') is not False):
            raise PublicMetadataError()
    return {'product_url': product_url, 'fields': dict(data['fields']),
            'field_evidence': {k: dict(v) for k, v in data['field_evidence'].items()}}
