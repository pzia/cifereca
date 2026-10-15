"""Public row projection and presentation adapter over the engine from PR #738."""
from dataclasses import dataclass, asdict, is_dataclass, replace
from collections.abc import Mapping
from ipaddress import ip_address
import re
from unicodedata import category
from urllib.parse import unquote, urlsplit
from cifereca.plugin_api import BookRef, book_ref_from_row
from constellations import ConstellationGraph, normalize_book

ACCESS = {'owned':'possédé', 'borrowable':'empruntable', 'free':'gratuit', 'external':'externe'}
REASONS = {'author':'Auteur', 'series':'Série', 'subject':'Sujet', 'period':'Période',
    'language':'Langue', 'length':'Longueur', 'format':'Format', 'mood':'Humeur',
    'publisher':'Éditeur', 'project':'Projet', 'progression':'Progression', 'mood-target':'Humeur recherchée'}


def reason_text(reason):
    kind, _, value = reason.partition(':')
    return REASONS.get(kind, kind) + ' : ' + value


def _hidden_url_controls(text: str, *, decoded_path: bool = False) -> bool:
    """Reject invisible Unicode controls in the raw and decoded URL layers.

    Percent-encoded *ordinary spaces* are a valid bibliographic path
    spelling (%20) and were already supported. Reject raw spaces, all
    control/format/surrogate codepoints and other Unicode separators.
    """
    return any(
        category(char) in {"Cc", "Cf", "Cs"}
        or (char.isspace() and (not decoded_path or char != " "))
        for char in text
    )


def public_url(value):
    """Only non-sensitive, externally reachable bibliographic notice URLs.

    A catalogue recommendation is not proof of download rights. Never offer
    a credentialed, signed, local or otherwise private URL as a document link.
    """
    if type(value) is not str or not value or len(value) > 2048:
        return ''
    if _hidden_url_controls(value):
        return ''
    if any(char in value for char in '<>\\'):
        return ''
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return ''
    if (
        parsed.scheme not in {'http', 'https'} or not host
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or '%' in host
    ):
        # A query can contain a signed license, token, or private search
        # context; only plain public notice links cross this UI boundary.
        return ''
    if port not in (None, 443 if parsed.scheme == 'https' else 80):
        return ''
    hostname = host.rstrip('.').casefold()
    if (
        '.' not in hostname
        or hostname in {'localhost', 'local', 'internal'}
        or hostname.endswith(('.localhost', '.local', '.internal', '.lan', '.home.arpa'))
        or not hostname.isascii() or len(hostname) > 253
        or not all(
            re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
            for label in hostname.split('.')
        )
    ):
        return ''
    try:
        address = ip_address(hostname)
    except ValueError:
        # Numeric-shorthand loopback addresses such as 127.1, 0177.0.0.1
        # and single-integer IPv4 can resolve privately in HTTP clients.
        if hostname.rsplit('.', 1)[-1].isdigit():
            return ''
    else:
        if not address.is_global:
            return ''

    # Never mistake a delivery or DRM resource for a public book notice.
    # Decode only to reject nested disguises; the URL is never rewritten.
    forbidden_segments = {
        'download', 'downloads', 'acquire', 'acquisition',
        'license', 'licence', 'licenses', 'licences', 'fulfillment',
        'assaredirect', 'loan', 'drm', 'token', 'tokens', 'oauth',
    }
    path = parsed.path
    for _ in range(4):
        if _hidden_url_controls(path, decoded_path=True):
            return ''
        segments = (part.casefold() for part in path.split('/') if part)
        if any(
            segment in forbidden_segments
            or segment.endswith(('.acsm', '.lcpl', '.epub', '.kepub', '.pdf'))
            for segment in segments
        ):
            return ''
        decoded = unquote(path)
        if decoded == path:
            return value
        path = decoded
    return ''


@dataclass
class Card:
    key: str
    book: BookRef
    raw: Mapping
    sources: tuple
    availability: tuple
    url: str
    graph: object = None
    cards: object = None


def node_from_row(item, *, origin='catalogue'):
    if isinstance(item, Mapping):
        row = dict(item)
    elif is_dataclass(item):
        row = asdict(item)
    else:
        raise TypeError('Notice attendue')
    if isinstance(item, BookRef):
        row['provider_id'] = item.identifiers.get('provider', '')
        row['item_id'] = item.identifiers.get('source_item_id', '')
    metadata = dict(row.get('metadata') or {})
    if origin == 'bibliothèque':
        # Only a row fetched through the identity-scoped local metadata broker
        # may establish that a concrete local file belongs to this profile.
        if row.get('local_path'):
            metadata['owned'] = True
    else:
        # A remote catalogue hit or a contextual selected BookRef is only
        # bibliographic evidence. Flags and paths in those untrusted rows
        # cannot assert possession by the current account.
        ownership_keys = ('owned', 'possessed', 'local_path')
        for container in (row, metadata):
            for key in ownership_keys:
                container.pop(key, None)
            for key in ('availability', 'availability_state', 'access'):
                if str(container.get(key) or '').strip().casefold() in {
                    'owned', 'possessed', 'local',
                }:
                    container.pop(key, None)
        raw_search = metadata.get('search_result')
        if isinstance(raw_search, Mapping):
            search = dict(raw_search)
            for key in ownership_keys:
                search.pop(key, None)
            for key in ('availability', 'availability_state', 'access'):
                if str(search.get(key) or '').strip().casefold() in {
                    'owned', 'possessed', 'local',
                }:
                    search.pop(key, None)
            metadata['search_result'] = search
    row['metadata'] = metadata
    ref = book_ref_from_row(row)
    if isinstance(item, BookRef):
        ref = replace(ref, book_id=item.book_id)
    # Supply the engine with the existing public validated bibliographic projection.
    row['identifiers'] = dict(ref.identifiers)
    if ref.book_id:
        row['book_id'] = ref.book_id
    if not row.get('book_id') and not ref.identifiers.get('source_item_id') and not ref.identifiers.get('isbn'):
        row['book_id'] = origin + ':row:' + str(id(item))
    model = normalize_book(row)
    search_result = metadata.get('search_result')
    search_meta = search_result if isinstance(search_result, Mapping) else {}
    # An unsafe first candidate must not mask an independently safe public
    # notice supplied by another source. No download/acquisition links here.
    url = next((
        safe for candidate in (
            row.get('friendly_url'),
            metadata.get('friendly_url'),
            search_meta.get('friendly_url'),
        )
        if (safe := public_url(candidate))
    ), '')
    return Card(model.key, ref, row, model.sources or (origin,), (ACCESS[model.availability],), url)


def merge_nodes(nodes):
    graph = ConstellationGraph(n.raw for n in nodes)
    originals = {}
    for node in nodes:
        originals.setdefault(node.key, node)
    cards = {}
    for key, book in graph.books.items():
        card = replace(originals[key], sources=book.sources or originals[key].sources,
                       availability=(ACCESS[book.availability],), graph=graph)
        cards[key] = card
    for card in cards.values():
        card.cards = cards
    return tuple(cards.values())


@dataclass(frozen=True)
class Edge:
    target: Card
    strength: float
    reasons: tuple


def neighbours(nodes, center, *, mode='Explorateur', surprise=.25, theme='', mood='', profile=(), limit=12):
    modes = {'Explorateur':'explorer','Confort':'comfort','Projet':'project','Humeur':'mood'}
    ranked = center.graph.rank(center.key, mode=modes[mode], exploration=int(surprise*100), mood=mood)
    if mode == 'Projet' and theme.strip():
        ranked = tuple(r for r in ranked if theme.strip().casefold() in r.book.subjects)
    return tuple(Edge(center.cards[r.book.key],r.score,tuple(reason_text(x) for x in r.reasons) +
                 tuple('Inféré : '+reason_text(x) for x in r.inferred)) for r in ranked[:limit])


def relation(a,b):
    edge = a.graph.edge(a.key,b.key)
    return Edge(b,edge.strength,tuple(REASONS.get(r.kind,r.kind)+' : '+r.value for r in edge.reasons)) if edge else Edge(b,0,())


def promenade(nodes,start,end,*,variant='équilibré',control=None):
    walk = start.graph.promenade(start.key,end.key,variant={'court':'short','équilibré':'balanced','surprenant':'surprising'}[variant], control=control)
    return tuple(start.cards[step.book.key] for step in walk.steps) if walk else ()


def replace_step(path,index,candidates):
    from constellations import Walk, WalkStep
    if not 0 < index < len(path)-1:
        raise ValueError('Étape intermédiaire requise')
    graph = path[0].graph
    walk = Walk('balanced',tuple(WalkStep(graph.books[n.key]) for n in path),0)
    for candidate in candidates:
        try:
            replaced = graph.replace_step(walk,index,candidate.key)
        except ValueError:
            continue
        return tuple(path[0].cards[step.book.key] for step in replaced.steps)
    return path


class Navigation:
    def __init__(self):
        self.history, self.position = [], -1

    def visit(self,key):
        if self.position >= 0 and self.history[self.position] == key:
            return
        self.history[self.position+1:] = [key]; self.position += 1

    def move(self,offset):
        if 0 <= self.position+offset < len(self.history):
            self.position += offset
        return self.history[self.position] if self.position >= 0 else None
