"""Parseurs du HTML observé ; aucun JavaScript exécuté."""
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qs

BASE = 'https://www.numilog.com/'
LIBRARY = BASE + 'Pages/Compte/MesLivres.aspx'


class ParseError(ValueError):
    pass


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    pieces: list = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def text(self):
        return ' '.join(' '.join(self.pieces).split())


class Parser(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.count = 0
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 25000 or len(self.stack) > 100:
            raise ParseError('Structure HTML trop volumineuse')
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag,attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.stack[-1].tag in {'script','style'}:
            self.stack[-1].pieces.append(data)
        else:
            for node in self.stack:
                if node.tag not in {'script','style'}:
                    node.pieces.append(data)


def classes(node):
    return set((node.attrs.get('class') or '').split())


def origin(value):
    p=urlsplit(value)
    # An explicit :0 is not an omitted port: it must never inherit 443/80.
    port=p.port if p.port is not None else (443 if p.scheme=='https' else 80 if p.scheme=='http' else None)
    return (p.scheme.casefold(),(p.hostname or '').casefold(),port)



def public_book_url(value, base_url=LIBRARY):
    """Allow only an attested Numilog public book-detail URL.

    The 2026 public Numilog catalogue exposes /<id>/<slug>.ebook.
    Older local fixtures also attest /livres/<slug> and /ebooks/<slug>.
    This is not a login/library/download link and must never transport
    account sessions, signed query parameters, or fragments into Core.
    """
    if not isinstance(value,str) or not 0 < len(value) <= 512:
        return None
    if value != value.strip() or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    if '\\' in value:
        return None
    try:
        raw = urlsplit(value)
        # URL-joining removes dot segments, which must not let a disguised
        # account/private path become a public book page by normalization.
        if any(part in ('.','..') for part in raw.path.split('/')):
            return None
        parsed = urlsplit(urljoin(base_url,value))
        candidate = urlunsplit((parsed.scheme,parsed.netloc,parsed.path,"",""))
        if (origin(candidate) != origin(base_url)
                or parsed.username or parsed.password or parsed.fragment
                or parsed.scheme not in ('https','http')
                or not parsed.path.startswith('/')):
            return None
        path = parsed.path
        public = re.fullmatch(
            r'/[0-9]{1,12}/[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.ebook',
            path,flags=re.I,
        )
        legacy = re.fullmatch(
            r'/(?:livres|ebooks)/[A-Za-z0-9][A-Za-z0-9._-]{0,100}',
            path,flags=re.I,
        )
        if not (public or legacy):
            return None
        # Only a harmless, previously supported legacy link parameter is
        # preserved. Never echo tokens, signed URLs or tracking parameters.
        if parsed.query:
            if not legacy or parse_qs(parsed.query,keep_blank_values=True) != {'ref':['library']}:
                return None
        return urlunsplit((parsed.scheme,parsed.netloc,parsed.path,parsed.query,""))
    except (ValueError,AttributeError):
        return None


def parse_library_page(text, base_url=LIBRARY, *, cover_origins=None, delivery_evidence=False):
    root = Parser(text).root
    tables = [n for n in root.walk() if n.attrs.get('id') == 'myTable']
    if len(tables) != 1:
        raise ParseError('Bibliothèque absente : session expirée ou page modifiée')

    site_origin=origin(base_url)
    site=urlsplit(base_url)
    site_root=f'{site.scheme}://{site.netloc}/'
    allowed_covers={site_origin,origin('https://couverture.numilog.com/')}
    allowed_covers.update(cover_origins or ())

    items, keys = [], set()
    for card in tables[0].children:
        if card.attrs.get('id') == 'ModelItemMesLivres':
            continue
        nodes = list(card.walk())
        links = [n for n in nodes if n.tag == 'a' and 'LnkDownloadBook' in classes(n)]
        if not links:
            continue
        title_nodes = [n for n in nodes if 'pTitle' in classes(n) and n.text()]
        titles = [n.text() for n in title_nodes]
        images = [n.attrs.get('src') for n in nodes if n.tag == 'img' and 'imgCouv' in classes(n)]
        public_candidates=[]
        for anchor in (n for n in nodes if n.tag == 'a'):
            if not any('pTitle' in classes(nested) for nested in anchor.walk()):
                continue
            raw_href=str(anchor.attrs.get('href') or '').strip()
            if not raw_href:
                continue
            resolved=public_book_url(raw_href,base_url)
            if resolved:
                public_candidates.append(resolved)
        public_candidates=list(dict.fromkeys(public_candidates))
        public_url=public_candidates[0] if len(public_candidates)==1 else None
        # Never inspect signed JS deliveries during search or inventory sync.
        delivery = (_card_delivery(
            card, links,
            {('https', 'entrepot2r.numilog.com', 443)},
        ) if delivery_evidence else None)
        for link in links:
            href = urljoin(base_url, link.attrs.get('href') or '')
            url = urlsplit(href)
            if origin(href) != site_origin or url.username or url.password or url.fragment or url.path != '/Pages/Compte/readBook.ashx':
                raise ParseError('Lien de bibliothèque inattendu')
            params = parse_qs(url.query)
            lid = params.get('lid',[])
            if len(lid) != 1 or not re.fullmatch(r'\d{1,20}',lid[0]):
                raise ParseError('Identifiant du livre absent ou inattendu')
            variant = tuple((k,tuple(params[k])) for k in ('m','format','FormatId','FORMAT_ID') if k in params)
            key = (lid[0],variant)
            if key in keys:
                continue
            keys.add(key)
            cover = urljoin(site_root,images[0]) if images and images[0] else None
            if cover:
                parsed_cover=urlsplit(cover)
                if origin(cover) not in allowed_covers or parsed_cover.username or parsed_cover.password or parsed_cover.fragment or parsed_cover.query:
                    cover = None
            row = {'lid':lid[0],'variant':repr(key[1]),'title':titles[0] if titles else 'Livre Numilog '+lid[0], 'cover':cover, 'href':href, 'public_url':public_url}
            if delivery is not None:
                row['delivery'] = delivery  # ephemeral; never serialized
            items.append(row)

    next_links=[]
    for node in root.walk():
        if node.tag != 'a' or 'next' not in (node.attrs.get('rel') or '').split():
            continue
        href=str(node.attrs.get('href') or '').strip()
        if not href:
            continue
        resolved=urljoin(base_url,href)
        parsed=urlsplit(resolved)
        if origin(resolved)!=site_origin or parsed.username or parsed.password or parsed.fragment:
            raise ParseError('Lien de pagination inattendu')
        if parsed.path.lower()!='/pages/compte/meslivres.aspx':
            raise ParseError('Lien de pagination inattendu')
        next_links.append(resolved)
    next_links=list(dict.fromkeys(next_links))
    if len(next_links)>1:
        raise ParseError('Pagination ambiguë dans la bibliothèque')
    return items,(next_links[0] if next_links else None)


def parse_library(text, base_url=LIBRARY, *, cover_origins=None):
    items,_next=parse_library_page(text,base_url,cover_origins=cover_origins)
    return items



# Private signed links are read ONLY during an explicit download action.
# The object is not a SearchResult/public catalog field and must not be
# serialized or logged. The upstream misspelling is intentionally preserved.
@dataclass(frozen=True)
class OwnedDelivery:
    product_id: str
    copy_index: str
    url: str


# Strictly bounded, non-evaluating extraction of simple JS object literals.
# Nested/dynamic JS is deliberately unsupported; no ProductId=lid assumption.
_JS_OBJECT = re.compile(r'\\{[^{}]{1,4096}\\}', re.S)
_JS_VALUE = r'''(?P<value>"(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*'|[0-9]{1,16})'''


def _js_literal(value):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    if not value.startswith(('"', "'")):
        return value if value.isdecimal() and len(value) <= 16 else None
    if len(value) < 2 or value[-1] != value[0]:
        return None
    if value[0] == '"':
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return None
    else:
        decoded = value[1:-1]
        for escaped, plain in (
            ('\\\\/', '/'), ('\\\\u0026', '&'), ("\\\\'", "'"), ('\\\\\\\\', '\\\\'),
        ):
            decoded = decoded.replace(escaped, plain)
        if '\\\\' in decoded:
            return None
    if not isinstance(decoded, str) or not 0 < len(decoded) <= 4096:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        return None
    return decoded


def _field(block, name):
    pattern = re.compile(
        r'''(?<![\\w$])(?:["']?)''' + re.escape(name)
        + r'''(?:["']?)\\s*:\\s*''' + _JS_VALUE + r'''\\s*(?=,|}|$)''', re.S,
    )
    values = [_js_literal(m.group('value')) for m in pattern.finditer(block)]
    return values[0] if len(values) == 1 else None


def _owned_delivery_candidates(node, *, allowed_origins):
    """Inspect descendant scripts, never evaluate JS or fetch a signed URL."""
    deliveries = []
    for script in node.walk():
        if script.tag != 'script':
            continue
        raw = ''.join(script.pieces)
        if len(raw) > 64 * 1024:
            continue
        for match in _JS_OBJECT.finditer(raw):
            block = match.group(0)
            product = _field(block, 'ProductId')
            index = _field(block, 'IndexExemplaire')
            drm = _field(block, 'DrmTechnlology')
            url = _field(block, 'link')
            if (not product or not product.isdecimal() or not index
                    or not index.isdecimal() or str(drm).upper() != 'ADOBE'
                    or not isinstance(url, str) or len(url) > 4096):
                continue
            try:
                parsed = urlsplit(unescape(url))
                # Only the exact ACSM delivery origin/path, with no user-info,
                # fragment, whitespace, relative URL, or disguised port.
                if (
                    origin(url) not in allowed_origins
                    or origin(url) != ('https', 'entrepot2r.numilog.com', 443)
                    or parsed.username or parsed.password or parsed.fragment
                    or parsed.path != '/fulfillment/URLLink.acsm'
                    or not parsed.query or any(c.isspace() for c in url)
                    or '\\\\' in url or any(ord(c) < 32 for c in url)
                ):
                    continue
            except (TypeError, ValueError, AttributeError):
                continue
            deliveries.append(OwnedDelivery(product, index, unescape(url)))
    return deliveries


def _card_delivery(card, links, allowed_origins):
    """Allow only a single download in a card with one *descendant* JS object.

    ProductId/IndexExemplaire are checked against independent card attributes
    if available. They are NEVER compared to the readBook lid: Numilog has not
    attested that those identifiers share a namespace.
    """
    if len(links) != 1:
        return None
    items = _owned_delivery_candidates(card, allowed_origins=allowed_origins)
    if len(items) != 1:
        return None
    item = items[0]
    attributes = (
        ('data-product-id', item.product_id),
        ('data-index-exemplaire', item.copy_index),
    )
    for key, expected in attributes:
        supplied = card.attrs.get(key)
        if supplied is not None and str(supplied).strip() != expected:
            return None
    return item


def html_response_kind(text):
    """Return only sanitized structural evidence for a 200 HTML response."""
    root = Parser(text).root
    inputs = [node for node in root.walk() if node.tag == 'input']
    if any(str(node.attrs.get('type') or '').casefold() == 'password' for node in inputs):
        return 'login'
    headings = [node.text().casefold() for node in root.walk()
                if node.tag in {'title', 'h1', 'h2'}]
    if any(label in {'erreur', 'error', 'accès refusé', 'access denied'}
           for label in headings):
        return 'error'
    return 'intermediate'


class DeliverySelectionError(ParseError):
    def __init__(self, code):
        # Codes are fixed and public; never include a signed delivery URL.
        self.code = code
        super().__init__('Livraison Numilog ' + code)


QUOTED = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''',re.X)


def delivery_url(text, base_url, *, allowed_origins=None):
    root = Parser(text).root
    allowed=set(allowed_origins or {origin('https://services.numilog.com/')})
    candidates = set()
    for node in root.walk():
        values = [node.attrs.get('href') or '']
        if node.tag == 'script':
            for match in QUOTED.finditer(''.join(node.pieces)):
                raw = match.group(0)
                try:
                    value = json.loads(raw) if raw[0] == '"' else raw[1:-1].replace('\\/','/').replace('\\u0026','&').replace("\\'", "'")
                except ValueError:
                    continue
                values.append(value)
        for value in values:
            if not isinstance(value,str) or not value.startswith(('https://','http://')):
                continue
            url = urljoin(base_url,unescape(value))
            parsed = urlsplit(url)
            # Every HTML-derived delivery URL obeys the same exact origin
            # and credential/fragment policy as the subsequent GET.
            if (
                origin(url) in allowed and not parsed.username
                and not parsed.password and not parsed.fragment
                and parsed.port in {None,80,443} | {o[2] for o in allowed if o[2]}
            ):
                candidates.add(url)
    if len(candidates) != 1:
        raise DeliverySelectionError('missing' if not candidates else 'ambiguous')
    return candidates.pop()
