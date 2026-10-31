"""Parseurs du HTML observé ; aucun JavaScript exécuté."""
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
import json
import ipaddress
import re
from unicodedata import category
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


def _raw_delivery_url_safe(value):
    # urllib.parse.urljoin/urlsplit silently remove CR/LF/TAB in Python.
    # Do not turn a malformed signed link into a different valid URL.
    return (
        type(value) is str and bool(value)
        and not any(
            char.isspace() or category(char) in {'Cc', 'Cf', 'Cs'}
            for char in value
        )
    )


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


def readzis_recommendation_url(value, lid, base_url=LIBRARY):
    """One proven owned-card recommendation, not a product or delivery URL.

    Only the observed SN=FB shape is accepted. Share visits can trigger
    commercial tracking and must never occur in inventory, search or sync.
    """
    if (type(value) is not str or type(lid) is not str
            or not re.fullmatch(r'[0-9]{1,12}', lid)
            or not 0 < len(value) <= 512
            or not _raw_delivery_url_safe(value) or chr(92) in value):
        return None
    try:
        raw = urlsplit(value)
        if (any(p in ('.', '..') for p in raw.path.split('/'))
                or '%' in raw.path or '%' in raw.query
                or raw.fragment or raw.username or raw.password):
            return None
        parsed = urlsplit(urljoin(base_url, value))
        if (parsed.scheme != 'https' or parsed.netloc != 'www.numilog.com'
                or parsed.path != '/Pages/Readzis/Recommand.ashx'
                or parsed.fragment or parsed.username or parsed.password
                or origin(base_url) != ('https', 'www.numilog.com', 443)):
            return None
        query = parse_qs(parsed.query, keep_blank_values=True,
                         strict_parsing=True, max_num_fields=2)
        if (set(query) != {'LivreId', 'SN'}
                or query['LivreId'] != [lid] or query['SN'] != ['FB']):
            return None
        return BASE + 'Pages/Readzis/Recommand.ashx?LivreId=' + lid + '&SN=FB'
    except (ValueError, TypeError, AttributeError):
        return None


def _scoped_library_cards(table):
    """Separate book cards through normal HTML table/tbody/tr wrappers.

    Do not combine the first title and cover of one owned book with the
    readBook link of another in a row containing multiple independent cards.
    Different formats under ONE title remain a single card as before.
    """
    def present(node):
        return any(n.tag == 'a' and 'LnkDownloadBook' in classes(n)
                   for n in node.walk())

    def title_count(node):
        return sum('pTitle' in classes(n) and bool(n.text()) for n in node.walk())

    def split(node):
        if node.attrs.get('id') == 'ModelItemMesLivres' or not present(node):
            return []
        if node.tag in {'table', 'thead', 'tbody', 'tfoot'}:
            return [card for child in node.children for card in split(child)]
        link_children = [child for child in node.children if present(child)]
        links = [n for n in node.walk()
                 if n.tag == 'a' and 'LnkDownloadBook' in classes(n)]
        titles = title_count(node)
        if len(links) > 1 and titles > 1:
            # Common wrapper (tr/td/div) may contain independently titled
            # book cards. Descend only when every resulting link is scoped
            # to its own title; otherwise reject the whole ambiguous page.
            if len(link_children) == 1:
                return split(link_children[0])
            if len(link_children) > 1 and all(title_count(c) == 1 for c in link_children):
                return [card for child in link_children for card in split(child)]
            raise ParseError('Fiches de livres ambiguës dans la bibliothèque')
        if len(links) == 1 and node.tag in {'tr', 'td'} and titles == 0 and len(link_children) == 1:
            return split(link_children[0])
        return [node]

    return split(table)


def _js_delivery_shape_count(script):
    """Count object *shape* without decoding a private identifier or link."""
    if script.tag != 'script':
        return 0
    raw = ''.join(script.pieces)
    if len(raw) > 64 * 1024:
        return 0
    return min(10000, sum(
        all(re.search(
            r"""(?<![A-Za-z0-9_$])(?:["']?)""" + re.escape(field)
            + r"""(?:["']?)\s*:""", block,
        ) for field in ('ProductId', 'IndexExemplaire', 'DrmTechnlology', 'link'))
        for block in (m.group(0) for m in _JS_OBJECT.finditer(raw))
    ))


def _scoped_confirmation_href(card, links, base_url):
    """A link literally present in ONE fresh owned card, never a guessed order.

    A card with multiple readBook variants does not attest which edition
    a separate confirmation href applies to. Unknown/dynamic/malformed links
    are never followed. Opaque order refs remain private and transient.
    """
    if len(links) != 1 or sum(
        'pTitle' in classes(node) and bool(node.text())
        for node in card.walk()
    ) != 1:
        return None
    chosen = set()
    for node in card.walk():
        if node.tag != 'a':
            continue
        raw = node.attrs.get('href')
        if (
            not _raw_delivery_url_safe(raw) or chr(92) in raw
            or len(raw) > 4096
        ):
            # An opaque order reference is not a license to navigate a
            # multi-megabyte link from a page. The signed ACSM parser already
            # applies the same small, fixed URL budget.
            continue
        try:
            if any(segment in ('.', '..') for segment in urlsplit(raw).path.split('/')):
                continue
            full = urljoin(base_url, raw)
            if len(full) > 4096:
                continue
            parsed = urlsplit(full)
            if (origin(full) != ('https', 'www.numilog.com', 443)
                    or origin(full) != origin(base_url)
                    or parsed.username or parsed.password or parsed.fragment
                    or parsed.path.casefold() != '/pages/commande/confirmationpaiement.aspx'):
                continue
            query = parse_qs(
                parsed.query, keep_blank_values=True,
                strict_parsing=True, max_num_fields=32,
            )
            if (
                set(query) != {'ref', 'disp'}
                or len(query['ref']) != 1 or not query['ref'][0].strip()
                or query['disp'] != ['simple']
            ):
                continue
            chosen.add(full)
        except (TypeError, ValueError, AttributeError):
            continue
    if len(chosen) > 1:
        raise ParseError('Liens de confirmation concurrents : téléchargement arrêté')
    return next(iter(chosen)) if chosen else None


def parse_library_page(text, base_url=LIBRARY, *, cover_origins=None, delivery_evidence=False, delivery_diagnostics=None, delivery_selector=None, strict_public_enrichment_selector=None):
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
    cards = _scoped_library_cards(tables[0])
    if delivery_evidence and delivery_diagnostics is not None:
        scoped_scripts = {
            id(n) for card in cards for n in card.walk() if n.tag == 'script'
        }
        all_scripts = [n for n in root.walk() if n.tag == 'script']
        inside = sum(_js_delivery_shape_count(n) for n in all_scripts
                     if id(n) in scoped_scripts)
        outside = sum(_js_delivery_shape_count(n) for n in all_scripts
                      if id(n) not in scoped_scripts)
        # Numeric/categorical structure only, bounded: no page, title,
        # ProductId, lid, signed URL or provider error string is exposed.
        delivery_diagnostics.update({
            'cards': min(len(cards), 10000),
            'js_shapes_in_cards': min(inside, 10000),
            'js_shapes_outside_cards': min(outside, 10000),
            'bound_links': 0,
        })
    for card in cards:
        nodes = list(card.walk())
        links = [n for n in nodes if n.tag == 'a' and 'LnkDownloadBook' in classes(n)]
        if not links:
            continue
        title_nodes = [n for n in nodes if 'pTitle' in classes(n) and n.text()]
        if delivery_evidence and len(title_nodes) > 1:
            # A single readBook link under multiple title nodes cannot
            # establish which product the delivery card is advertising.
            raise ParseError('Titres ambigus dans la fiche de livraison')
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
        # Delay parsing JS URLs until AFTER the validated source row is
        # known. The caller can select one exact scoped record ID, so signed
        # links for unrelated purchases never enter memory on a user click.
        for link in links:
            raw_link = link.attrs.get('href') or ''
            if not _raw_delivery_url_safe(raw_link):
                raise ParseError('Lien de bibliothèque inattendu')
            href = urljoin(base_url, raw_link)
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
                # Multiple distinct cards with the same download ID can
                # present competing ProductId/copy/order data. Inventory may
                # deduplicate legacy pages, but an EXPLICIT acquisition must
                # refuse ambiguity instead of trusting the first link.
                if delivery_evidence and (
                    delivery_selector is None
                    or delivery_selector({'lid':lid[0],'variant':repr(key[1])})
                ):
                    raise ParseError('Fiches de livraison dupliquées : téléchargement arrêté')
                if (strict_public_enrichment_selector is not None
                        and strict_public_enrichment_selector({
                            'lid': lid[0], 'variant': repr(key[1]),
                        })):
                    # No signed delivery evidence is read. An owned edition
                    # with two competing public links is ambiguous even if
                    # the ordinary library list deduplicates such rows.
                    raise ParseError('Fiches publiques dupliquées : enrichissement arrêté')
                continue
            keys.add(key)
            cover = urljoin(site_root,images[0]) if images and images[0] else None
            if cover:
                parsed_cover=urlsplit(cover)
                if origin(cover) not in allowed_covers or parsed_cover.username or parsed_cover.password or parsed_cover.fragment or parsed_cover.query:
                    cover = None
            row = {'lid':lid[0],'variant':repr(key[1]),'title':titles[0] if titles else 'Livre Numilog '+lid[0], 'cover':cover, 'href':href, 'public_url':public_url}
            if (public_url is None and strict_public_enrichment_selector is not None
                    and strict_public_enrichment_selector(row)):
                # Scoped only to a fresh selected purchase; absent from normal
                # library, search and synchronization projections.
                if len(links) == 1 and len(title_nodes) == 1:
                    recommendations = set()
                    for anchor in (n for n in nodes if n.tag == 'a'):
                        candidate = anchor.attrs.get('href')
                        if type(candidate) is not str:
                            continue
                        try:
                            is_readzis = (urlsplit(candidate).path.casefold()
                                == '/pages/readzis/recommand.ashx')
                        except ValueError:
                            is_readzis = False
                        if not is_readzis:
                            continue
                        checked = readzis_recommendation_url(candidate, lid[0], base_url)
                        if checked is None:
                            raise ParseError('Lien Readzis non qualifié : enrichissement arrêté')
                        recommendations.add(checked)
                    if len(recommendations) > 1:
                        raise ParseError('Liens Readzis divergents : enrichissement arrêté')
                    if recommendations:
                        row['readzis_url'] = next(iter(recommendations))
            if delivery_evidence and (delivery_selector is None or delivery_selector(row)):
                # Never retain order refs in search or background sync.
                # The only eligible direct URL is an actual HTML href under
                # this one freshly selected and uniquely identified card.
                confirmation_href = _scoped_confirmation_href(card, links, base_url)
                if confirmation_href is not None:
                    row['confirmation'] = confirmation_href  # transient only
                # An ambiguous card (multiple links, products, or revisions)
                # never yields an attested ACSM. Never equate ProductId to lid.
                delivery = _card_delivery(
                    card, links, {
                        ('https', 'entrepot2r.numilog.com', 443),
                        ('https', 'services.numilog.com', 443),
                    },
                )
                if delivery is not None:
                    row['delivery'] = delivery  # transient, target-specific
                    if delivery_diagnostics is not None:
                        delivery_diagnostics['bound_links'] = min(
                            10000, delivery_diagnostics.get('bound_links', 0) + 1,
                        )
                elif (confirmation_href is None
                      and len(links) == 1 and len(title_nodes) == 1
                      and not any(
                          (n.tag == 'script' and _js_delivery_shape_count(n))
                          or ('ModalDownload' in str(n.attrs.get('onclick') or ''))
                          for n in nodes
                      )):
                    # 31/10 #2797: real purchased Numilog cards can expose
                    # readBook?lid=N&m=dwld with no inline signed JS object.
                    # Record ONLY an exact observed, scoped and uniquely
                    # owned download action; this is not a signed ACSM URL.
                    # Never infer a selectable offer from m=stream, the
                    # historical m=download, an orphan link or mixed JS.
                    observed = _readbook_coordinates(row['href'], base_url)
                    if (observed is not None
                            and observed[0] == row['lid']
                            and dict(observed[1]).get('m') == ('dwld',)):
                        row['readbook_dwld_offer'] = True  # selected-only
                        if delivery_diagnostics is not None:
                            delivery_diagnostics['readbook_dwld_offers'] = min(
                                10000, delivery_diagnostics.get(
                                    'readbook_dwld_offers', 0,
                                ) + 1,
                            )
            items.append(row)

    next_links=[]
    for node in root.walk():
        if node.tag != 'a' or 'next' not in (node.attrs.get('rel') or '').split():
            continue
        href=str(node.attrs.get('href') or '').strip()
        if not href:
            continue
        if not _raw_delivery_url_safe(href):
            raise ParseError('Lien de pagination inattendu')
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
@dataclass(frozen=True, repr=False)
class OwnedDelivery:
    # Identifiers and signed URL are private transient acquisition evidence.
    # Default dataclass repr would otherwise expose them via debug exceptions.
    product_id: str = field(repr=False)
    copy_index: str = field(repr=False)
    url: str = field(repr=False)
    delivery_kind: str = field(default='acsm', repr=False)


# Strictly bounded, non-evaluating extraction of simple JS object literals.
# Nested/dynamic JS is deliberately unsupported; no ProductId=lid assumption.
_JS_OBJECT = re.compile(r'\{[^{}]{1,4096}\}', re.S)
_JS_VALUE = r'''(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[0-9]{1,16})'''


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
            ('\\/', '/'), ('\\u0026', '&'), ("\\'", "'"), ('\\\\', '\\'),
        ):
            decoded = decoded.replace(escaped, plain)
        if '\\' in decoded:
            return None
    if not isinstance(decoded, str) or not 0 < len(decoded) <= 4096:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        return None
    return decoded


def _field(block, name):
    pattern = re.compile(
        r'''(?<![\w$])(?:["']?)''' + re.escape(name)
        + r'''(?:["']?)\s*:\s*''' + _JS_VALUE + r'''\s*(?=,|}|$)''', re.S,
    )
    values = [_js_literal(m.group('value')) for m in pattern.finditer(block)]
    return values[0] if len(values) == 1 else None


def _attested_public_https(value):
    """Network-independent policy for a URL in the confirmed owned Links array.

    No download origin/endpoint whitelist. Reject local/private network
    identifiers and malformed URLs before making the actual network request.
    The provider checks DNS resolution separately immediately before GET.
    """
    if type(value) is not str or len(value) > 4096 or not _raw_delivery_url_safe(value):
        return False
    try:
        p = urlsplit(value)
        hostname = (p.hostname or '').casefold()
        if (p.scheme != 'https' or p.username or p.password or p.fragment
                or p.port not in (None, 443) or not p.path.startswith('/')
                or not p.netloc or chr(92) in value):
            return False
        # No single-label, IPv4/IPv6 literal, numeric-looking or local DNS
        # names; accepting *any* domain does not authorize internal services.
        if (hostname.endswith('.') or len(hostname) > 253
                or hostname.endswith(('.localhost', '.local', '.internal',
                                       '.home.arpa', '.lan', '.onion',
                                       '.invalid', '.test', '.example'))
                or hostname in {'localhost', 'metadata.google.internal'}):
            return False
        try:
            ipaddress.ip_address(hostname)
            return False
        except ValueError:
            pass
        labels = hostname.split('.')
        return (len(labels) >= 2 and all(
            re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
            for label in labels
        ) and not all(part.isdecimal() for part in labels)
            and not any(pth in {'.', '..'} for pth in p.path.split('/')))
    except (ValueError, TypeError, AttributeError):
        return False


def _attested_query_matches_product(url, product, drm):
    """Optional recognized query IDs cannot contradict the selected JS row.

    No expected vendor hostname or endpoint is hard-coded here; unfamiliar
    opaque signature parameters remain the vendor's responsibility.
    """
    try:
        fields = parse_qs(
            urlsplit(url).query, keep_blank_values=True, max_num_fields=64,
        )
        for key in ('pid', 'productid'):
            if key in fields and fields[key] != [product]:
                return False
        if drm in {'ADOBE', 'NOT_SPECIFIED'} and 'DRM' in fields:
            # Explicit LCP markers contradict a non-LCP Links entry.
            if len(fields['DRM']) != 1 or fields['DRM'][0].upper() == 'LCP':
                return False
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _is_direct_epub_download(value, allowed_origins, *, expected_product=None):
    """Only the observed signed Numilog handler, never another service URL.

    Never persist or publish the signed query. ProductId comes from Links,
    NOT from the MesLivres lid (they are distinct identifier namespaces).
    """
    if not _raw_delivery_url_safe(value) or len(value) > 4096:
        return False
    try:
        parsed = urlsplit(value)
        if (origin(value) != ('https', 'services.numilog.com', 443)
                or origin(value) not in allowed_origins
                or parsed.username or parsed.password or parsed.fragment
                or parsed.path != '/v4.0/HandlerDownloadLinks/HandlerDilicomDownloadLink.ashx'
                or not parsed.query or chr(92) in value):
            return False
        fields = parse_qs(
            parsed.query, keep_blank_values=True,
            strict_parsing=True, max_num_fields=8,
        )
        if set(fields) != {'productid', 'idcmd', 'date', 'hash'}:
            return False
        product, order = fields['productid'], fields['idcmd']
        date, signature = fields['date'], fields['hash']
        return (
            len(product) == len(order) == len(date) == len(signature) == 1
            and bool(re.fullmatch(r'[0-9]{1,16}', product[0]))
            and bool(re.fullmatch(r'[0-9]{1,20}', order[0]))
            and (expected_product is None or product[0] == expected_product)
            and 0 < len(date[0]) <= 128
            and 0 < len(signature[0]) <= 256
        )
    except (ValueError, TypeError, AttributeError):
        return False



def _eden_v2_fields(value, allowed_origins, *, drm=None, expected_product=None):
    """Only the observed signed Eden V2 handler; never expose URL or tokens."""
    if not _raw_delivery_url_safe(value) or len(value) > 4096:
        return None
    try:
        parsed = urlsplit(value)
        if (origin(value) != ('https', 'services.numilog.com', 443)
                or origin(value) not in allowed_origins
                or parsed.username or parsed.password or parsed.fragment
                or parsed.path != '/v4.0/HandlerDownloadLinks/HandlerEdenDownloadLinkV2.ashx'
                or not parsed.query or chr(92) in value):
            return None
        fields = parse_qs(parsed.query, keep_blank_values=True,
                          strict_parsing=True, max_num_fields=4)
        keys = {'pid', 'uid', 'tid'}
        if set(fields) != (keys | ({'DRM'} if drm == 'LCP' else set())):
            return None
        if any(len(v) != 1 for v in fields.values()):
            return None
        if (not re.fullmatch(r'[0-9]{1,16}', fields['pid'][0])
                or (expected_product is not None
                    and fields['pid'][0] != expected_product)):
            return None
        if not all(re.fullmatch(r'[0-9a-fA-F]{16,64}', fields[key][0])
                   for key in ('uid', 'tid')):
            return None
        if drm == 'LCP' and fields['DRM'] != ['LCP']:
            return None
        return fields
    except (ValueError, TypeError, AttributeError):
        return None


def _is_eden_adobe_download(value, allowed_origins, *, expected_product=None):
    """Accept ADOBE route without DRM flag; explicitly reject DRM=LCP."""
    return _eden_v2_fields(
        value, allowed_origins, expected_product=expected_product,
    ) is not None


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
            delivery_kind = (
                'acsm' if drm == 'ADOBE' else
                'epub_direct' if drm == 'NOT_SPECIFIED'
                and _field(block, 'format') == 'EPUB' else None
            )
            if (not product or not re.fullmatch(r'[0-9]{1,16}', product)
                    or not index or not re.fullmatch(r'[0-9]{1,16}', index)
                    or delivery_kind is None
                    or not isinstance(url, str) or len(url) > 4096):
                continue
            try:
                decoded_url = unescape(url)
                # Exact product/copy/technology is checked on the owning
                # card and again in the confirmation. The *merchant page*,
                # not this plugin's hard-coded endpoint table, authorizes
                # the selected delivery URL. The target must still be safe.
                if (not _attested_public_https(decoded_url)
                        or not _attested_query_matches_product(decoded_url, product, drm)):
                    continue
            except (ValueError, TypeError, AttributeError):
                continue
            deliveries.append(OwnedDelivery(product, index, decoded_url, delivery_kind))
    return deliveries


def _card_delivery(card, links, allowed_origins):
    """Allow only a single download in a card with one *descendant* JS object.

    ProductId/IndexExemplaire are checked against independent card attributes
    if available. They are NEVER compared to the readBook lid: Numilog has not
    attested that those identifiers share a namespace.
    """
    if len(links) != 1:
        return None
    # A scoped numeric lid establishes a technical download endpoint, not
    # an equivalence with the product ID of a JS object. Requiring a single
    # titled owner card avoids treating a bare/orphan link as store evidence.
    titles = [n for n in card.walk()
              if 'pTitle' in classes(n) and n.text()]
    if len(titles) != 1:
        return None
    items = _owned_delivery_candidates(card, allowed_origins=allowed_origins)
    if len(items) != 1:
        return None
    item = items[0]
    if item.delivery_kind == 'epub_direct':
        # A signed direct EPUB must have ONE exact action in this owned card.
        actions = [n.attrs.get('onclick', '') for n in card.walk()
                   if re.search(r'\bModalDownload\s*\(', n.attrs.get('onclick', ''))]
        if len(actions) != 1:
            return None
        selected = re.fullmatch(
            r'\s*ModalDownload\(\s*([0-9]{1,16})\s*,\s*([0-9]{1,16})\s*\)\s*;?\s*',
            actions[0],
        )
        if selected is None or selected.groups() != (item.product_id, item.copy_index):
            return None
    attributes = (
        ('data-product-id', item.product_id),
        ('data-index-exemplaire', item.copy_index),
    )
    for key, expected in attributes:
        # Product/copy identity markers may live on the nested book element
        # rather than on the enclosing <tr> in real HTML table layouts.
        for node in card.walk():
            supplied = node.attrs.get(key)
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


def _is_fulfillment_acsm(url, allowed_origins):
    """An exact HTTPS Numilog signed fulfillment URL, never a navigation link.

    Merely being hosted by Numilog/its partners is NOT evidence of ACSM.
    The origin, path and nonempty query must all match the attested contract.
    """
    try:
        parsed = urlsplit(url)
        return (
            origin(url) == ('https', 'entrepot2r.numilog.com', 443)
            and origin(url) in allowed_origins
            and parsed.path == '/fulfillment/URLLink.acsm'
            and bool(parsed.query)
            and not parsed.username and not parsed.password
            and not parsed.fragment and '\\' not in url
            and not any(c.isspace() or ord(c) < 32 for c in url)
        )
    except (ValueError, AttributeError, TypeError):
        return False


def _readbook_coordinates(value, base_url):
    try:
        if not _raw_delivery_url_safe(value) or len(value) > 4096:
            return None
        url = urljoin(base_url, value)
        if len(url) > 4096:
            return None
        parsed = urlsplit(url)
        if (origin(url) != origin(base_url) or parsed.username or parsed.password
                or parsed.fragment or parsed.path.lower() != '/pages/compte/readbook.ashx'):
            return None
        # Identity comparison must never erase a malformed query segment
        # (e.g. an extra '&edition' without '='), or accept unbounded
        # repeated params before deciding that a streaming card matches an
        # owned download. This parser is only used for readBook binding,
        # not for the ordinary public catalogue.
        query = parse_qs(
            parsed.query, keep_blank_values=True,
            strict_parsing=True, max_num_fields=32,
        )
        lid = query.get('lid', [])
        if len(lid) != 1 or not re.fullmatch(r'[0-9]{1,20}', lid[0]):
            return None
        # Do not silently discard an unknown upstream copy/edition parameter.
        # The card must repeat the whole fresh readBook query, order of keys
        # aside; values and repeated-value ordering remain exact.
        return (lid[0], tuple((key, tuple(query[key])) for key in sorted(query)))
    except (ValueError, TypeError, AttributeError):
        return None



def _confirmation_readbook_binding(observed, expected):
    """Prove one owned edition across download and streaming *link labels*.

    Numilog's purchased MesLivres card may advertise
    readBook?lid=N&m=dwld, while the SAME confirmation card exposes only
    'Lire en ligne (Streaming)' as readBook?lid=N&m=stream. The streaming
    href is evidence of the card's lid, NEVER a fulfillment URL.

    Preserve the previous exact comparison first. The only relaxation is
    a directional dwld -> stream mode switch on the same readBook origin,
    path, lid and *all* other query keys/values/multiplicities. Any other
    mode or copy/edition variance fails closed.
    """
    if observed is None or expected is None:
        return False
    if observed == expected:
        return True
    observed_lid, observed_query = observed
    expected_lid, expected_query = expected
    if observed_lid != expected_lid:
        return False
    observed_fields = dict(observed_query)
    expected_fields = dict(expected_query)
    if (expected_fields.get('m') != ('dwld',)
            or observed_fields.get('m') != ('stream',)):
        return False
    # Keys include lid; repeated non-mode values remain ordered tuples.
    # Drop ONLY the attested single 'm' mode, never a copy/edition field.
    observed_fields.pop('m')
    expected_fields.pop('m')
    return observed_fields == expected_fields


def _confirmation_delivery(root, base_url, expected_readbook, allowed, *,
                           allow_missing_readbook=False,
                           allow_owned_confirmation_without_readbook=False,
                           selection=None):
    """Bind the observed global Links array to ONE explicitly selected card.

    A single signed URL is not sufficient when a confirmation names another
    product/copy. No JS evaluation, ProductId=lid inference or endpoint guess.
    Unknown/dynamic versions of this known layout fail closed.
    """
    declaration = re.compile(r'\b(?:const|let|var)\s+Links\s*=')
    scripts = [n for n in root.walk() if n.tag == 'script'
               and declaration.search(''.join(n.pieces))]
    if not scripts:
        return None  # historical intermediary layouts retain their contract
    def require(condition):
        if not condition:
            raise DeliverySelectionError('ambiguous')
    require(len(scripts) == 1)
    raw = ''.join(scripts[0].pieces)
    require(len(raw) <= 64 * 1024 and len(declaration.findall(raw)) == 1)
    literal = re.search(
        r'\b(?:const|let|var)\s+Links\s*=\s*'
        r'(\[(?:\s|,|\{[^{}]{1,4096}\})*\])\s*;', raw,
    )
    require(literal is not None)
    candidates = _owned_delivery_candidates(
        Node('script', pieces=[literal.group(1)]), allowed_origins=allowed,
    )
    require(len(candidates) == 1)
    candidate = candidates[0]
    # The vendor may publish both Adobe and LCP for *one* purchased copy.
    # Neither is a second book: choose the one ADOBE option only after the
    # selected card proves that every Links entry is for the same edition.
    # A new vendor hostname/path need not trigger a plugin update.
    blocks = [m.group(0) for m in _JS_OBJECT.finditer(literal.group(1))]
    require(1 <= len(blocks) <= 16 and all(
        _field(block, 'ProductId') == candidate.product_id
        and _field(block, 'IndexExemplaire') == candidate.copy_index
        for block in blocks
    ))
    if candidate.delivery_kind == 'acsm':
        require(sum(_field(block, 'DrmTechnlology') == 'ADOBE'
                    for block in blocks) == 1)
        require(sum(_field(block, 'DrmTechnlology') == 'LCP'
                    for block in blocks) <= 1)
        require(all(_field(block, 'DrmTechnlology') in {'ADOBE', 'LCP'}
                    for block in blocks))
    if candidate.delivery_kind == 'epub_direct':
        # A second edition or direct alternative is never silently ignored.
        blocks = [m.group(0) for m in _JS_OBJECT.finditer(literal.group(1))]
        require(1 <= len(blocks) <= 16 and all(
            _field(block, 'ProductId') == candidate.product_id
            and _field(block, 'IndexExemplaire') == candidate.copy_index
            for block in blocks
        ))
        require(sum(
            _field(block, 'DrmTechnlology') == 'NOT_SPECIFIED'
            and _field(block, 'format') == 'EPUB' for block in blocks
        ) == 1)
    expected = _readbook_coordinates(expected_readbook, base_url)
    require(expected is not None)
    # Never extend the streaming equivalence to arbitrary HTML landings:
    # the extra identity proof exists ONLY on the observed canonical
    # Numilog confirmation page, after the HTTPS/origin check.
    page = urlsplit(base_url)
    allow_stream_identity = (
        origin(base_url) == ('https', 'www.numilog.com', 443)
        and page.path.casefold() == '/pages/commande/confirmationpaiement.aspx'
        and page.username is None and page.password is None
        and not page.fragment
    )
    parents = {id(child): node for node in root.walk() for child in node.children}
    bindings = []
    for action in root.walk():
        handler = action.attrs.get('onclick', '')
        if not re.search(r'\bModalDownload\s*\(', handler):
            continue
        card = action
        while card is not None and 'card' not in classes(card):
            card = parents.get(id(card))
        require(card is not None)
        # A confirmation form enclosing the selected card may itself
        # attest an opaque order ref. If it does, its reference must agree
        # with the actual GET/302 confirmation URL. Never log or return it,
        # and never assume a ProductId/readBook lid equals an order ref.
        page_parts = urlsplit(base_url)
        ancestor = parents.get(id(card))
        while ancestor is not None:
            if ancestor.tag == 'form' and ancestor.attrs.get('action'):
                raw_action = ancestor.attrs['action']
                require(_raw_delivery_url_safe(raw_action))
                try:
                    form_url = urljoin(base_url, raw_action)
                    form_parts = urlsplit(form_url)
                    if (origin(form_url) == origin(base_url)
                            and form_parts.path.casefold()
                            == '/pages/commande/confirmationpaiement.aspx'
                            and page_parts.path.casefold()
                            == '/pages/commande/confirmationpaiement.aspx'):
                        # A different HTML intermediary may include a form
                        # *linking* to a confirmation. It has no GET order
                        # ref of its own to compare, so it cannot attest
                        # either a match or a contradiction. Preserve the
                        # legacy intermediary selection contract.
                        page_ref = parse_qs(
                            page_parts.query, keep_blank_values=True,
                            strict_parsing=True, max_num_fields=32,
                        ).get('ref', [])
                        form_refs = parse_qs(
                            form_parts.query, keep_blank_values=True,
                            strict_parsing=True, max_num_fields=32,
                        ).get('ref', [])
                        if form_refs:
                            require(
                                len(form_refs) == len(page_ref) == 1
                                and bool(form_refs[0].strip())
                                and form_refs == page_ref
                            )
                except (ValueError, AttributeError, TypeError):
                    require(False)
            ancestor = parents.get(id(ancestor))
        readbooks = [n for n in card.walk() if n.tag == 'a'
                     and _readbook_coordinates(n.attrs.get('href', ''), base_url) is not None]
        # A confirmation's readBook can be a streaming-only *identity
        # link*. Numilog's actual Adobe download remains the unique signed
        # Links/ModalDownload candidate; never navigate readBook m=stream.
        # The comparison changes ONLY here, inside the already verified
        # exact confirmation layout and owning card.
        matched_readbook = any(
            (
                _confirmation_readbook_binding(
                    _readbook_coordinates(n.attrs['href'], base_url), expected,
                ) if allow_stream_identity
                else _readbook_coordinates(n.attrs['href'], base_url) == expected
            ) for n in readbooks
        )
        if matched_readbook:
            require(len(readbooks) == 1)
        elif (not readbooks and allow_stream_identity
              and (allow_missing_readbook or allow_owned_confirmation_without_readbook)):
            mode = dict(expected[1]).get('m')
            require(
                (allow_missing_readbook and (mode == ('dwld',)
                    or (mode == ('download',) and candidate.delivery_kind == 'acsm'
                        and not _is_fulfillment_acsm(candidate.url, allowed))))
                or (allow_owned_confirmation_without_readbook
                    and candidate.delivery_kind == 'epub_direct'
                    and mode in {('dwld',), ('download',)})
            )
            # Some purchases have NO streaming capability and therefore no
            # readBook link on the confirmation card. The caller may assert
            # this fallback ONLY after a direct, checked HTTP 302 from this
            # freshly selected owned readBook?m=dwld to this exact URL.
            # Never let a standalone confirmation href use this relaxation.
            try:
                fields = parse_qs(
                    page.query, keep_blank_values=True,
                    strict_parsing=True, max_num_fields=32,
                )
            except (ValueError, AttributeError, TypeError):
                require(False)
            require(
                set(fields) == {'ref', 'disp'}
                and len(fields['ref']) == 1 and bool(fields['ref'][0].strip())
                and fields['disp'] == ['simple']
            )
            # Reject multi-book orders or unrelated cards, even if there is
            # only one ADOBE URL. No inferred ProductId == readBook lid.
            carts = [n for n in root.walk() if 'shopping-cart' in classes(n)]
            require(len(carts) == 1)
            card_nodes = [n for n in carts[0].walk() if 'card' in classes(n)]
            require(len(card_nodes) == 1 and card_nodes[0] is card)
            require(sum(bool(re.search(r'\bModalDownload\s*\(',
                n.attrs.get('onclick', ''))) for n in root.walk()) == 1)
            # A second book's readBook anywhere on this page makes an
            # otherwise anonymous single-card match unsafe.
            require(not any(
                n.tag == 'a' and (
                    '/pages/compte/readbook.ashx' in str(
                        n.attrs.get('href', ''),
                    ).casefold()
                ) for n in root.walk()
            ))
            # All declared LCP/ADOBE alternatives must attest precisely ONE
            # product and copy; reject hidden or malformed competing entries.
            blocks = [m.group(0) for m in _JS_OBJECT.finditer(literal.group(1))]
            require(1 <= len(blocks) <= 16 and all(
                _field(block, 'ProductId') == candidate.product_id
                and _field(block, 'IndexExemplaire') == candidate.copy_index
                for block in blocks
            ))
        else:
            continue
        require(sum(bool(re.search(r'\bModalDownload\s*\(', n.attrs.get('onclick', '')))
                    for n in card.walk()) == 1)
        call = re.fullmatch(
            r'\s*ModalDownload\(\s*([0-9]{1,16})\s*,\s*([0-9]{1,16})\s*\)\s*;?\s*', handler,
        )
        require(call is not None)
        bindings.append(call.groups())
    require(bindings == [(candidate.product_id, candidate.copy_index)])
    if selection is not None:
        selection['kind'] = candidate.delivery_kind
    return candidate.url


def delivery_url(text, base_url, *, allowed_origins=None, evidence=None,
                 expected_readbook=None, require_bound_confirmation=False,
                 allow_missing_readbook=False,
                 allow_owned_confirmation_without_readbook=False, selection=None):
    """Locate a single allowlisted delivery; optionally require an owned binding.

    When an HTML page also has a login widget, callers may only follow a
    signed ACSM that is structurally bound to the fresh readBook card inside
    an observed Numilog confirmation layout. Never accept an unrelated
    single HTML link as an alternative in that mode.
    Evidence contains bounded counters only, never a URL or HTML body.
    """
    root = Parser(text).root
    allowed=set(allowed_origins or {origin('https://services.numilog.com/')})
    candidates = set()
    signed_acsm = set()
    signed_epub = set()
    signed_eden = set()
    signed_shapes = html_links = html_scripts = 0
    for node in root.walk():
        values = [('href', node.attrs.get('href') or '')]
        if node.tag == 'script':
            if evidence is not None:
                signed_shapes = min(10000, signed_shapes + _js_delivery_shape_count(node))
            for match in QUOTED.finditer(''.join(node.pieces)):
                raw = match.group(0)
                try:
                    value = json.loads(raw) if raw[0] == '"' else raw[1:-1].replace('\\/','/').replace('\\u0026','&').replace("\\'", "'")
                except ValueError:
                    continue
                values.append(('script', value))
        for source, value in values:
            if (
                not _raw_delivery_url_safe(value)
                or not value.startswith(('https://','http://'))
            ):
                continue
            if evidence is not None:
                if source == 'href':
                    html_links = min(10000, html_links + 1)
                else:
                    html_scripts = min(10000, html_scripts + 1)
            try:
                decoded_url = unescape(value)
                # A JS literal can carry numeric HTML character references;
                # reject controls introduced by unescape as well as raw ones.
                if not _raw_delivery_url_safe(decoded_url):
                    continue
                url = urljoin(base_url, decoded_url)
                parsed = urlsplit(url)
                # Every HTML-derived delivery URL obeys the same exact origin
                # and credential/fragment policy as the subsequent GET.
                if (
                    origin(url) in allowed and not parsed.username
                    and not parsed.password and not parsed.fragment
                    and parsed.port in {None,80,443} | {o[2] for o in allowed if o[2]}
                ):
                    candidates.add(url)
                    if _is_fulfillment_acsm(url, allowed):
                        signed_acsm.add(url)
                    if _is_eden_adobe_download(url, allowed):
                        signed_eden.add(url)
                    if _is_direct_epub_download(url, allowed):
                        signed_epub.add(url)
            except (ValueError, AttributeError, TypeError):
                continue
    if evidence is not None:
        evidence.update({
            'js_delivery_shapes': signed_shapes,
            'absolute_html_hrefs': html_links,
            'absolute_script_literals': html_scripts,
            'allowed_candidates': min(10000, len(candidates)),
            'attested_acsm_candidates': min(10000, len(signed_acsm)),
        })
    bound_confirmation = _confirmation_delivery(
        root, base_url, expected_readbook, allowed,
        allow_missing_readbook=allow_missing_readbook,
        allow_owned_confirmation_without_readbook=allow_owned_confirmation_without_readbook,
        selection=selection,
    )
    if require_bound_confirmation:
        # A shared login widget does not by itself prove authentication
        # failure, but it makes all unbound HTML links untrustworthy. Only
        # the exact product/copy + fresh owned readBook + ModalDownload
        # combination may authorize following the signed ACSM.
        if bound_confirmation is None:
            raise DeliverySelectionError('missing')
        if ((signed_acsm | signed_epub | signed_eden) - {bound_confirmation}
                or not _attested_public_https(bound_confirmation)):
            raise DeliverySelectionError('ambiguous')
        return bound_confirmation
    if bound_confirmation is not None:
        if ((signed_acsm | signed_epub | signed_eden) - {bound_confirmation}
                or not _attested_public_https(bound_confirmation)):
            raise DeliverySelectionError('ambiguous')
        return bound_confirmation
    # A signed direct EPUB must never be followed from an arbitrary script.
    if signed_epub or signed_eden:
        raise DeliverySelectionError('ambiguous')
    # Pages intermédiaires may contain harmless site navigation and a
    # SINGLE signed ACSM. Prefer that exact delivery over ordinary permitted
    # links; never pick arbitrarily between different ACSM signatures.
    if len(signed_acsm) == 1:
        return signed_acsm.pop()
    if len(signed_acsm) > 1:
        raise DeliverySelectionError('ambiguous')
    if len(candidates) != 1:
        raise DeliverySelectionError('missing' if not candidates else 'ambiguous')
    return candidates.pop()
