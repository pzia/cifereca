from __future__ import annotations
import hashlib, html, re, unicodedata, logging
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, quote_plus, quote, urlsplit, parse_qsl
import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT
import xml.etree.ElementTree as ET
from cifereca.plugin_api import Account, ItemDetail, SearchResult, ProviderCapabilities, ProviderSearchCapabilities, ProviderCapabilityError, ProviderDescriptor, ProviderError, ProviderPlugin, ProviderSession, TemporaryUnavailable, UnsupportedOperation, Conflict, Cancelled

_DOWNLOAD_TRACE = logging.getLogger('cifereca.plugins.ebooksgratuits')

BASE='https://www.ebooksgratuits.com/'
MAX_REQUESTED_PAGES = 101  # Pages 0..100, not an unbounded upstream scan.
_RELEASE_RE = re.compile(r'\bParution\s+le\s+([0-3][0-9]/[01][0-9]/[0-9]{4})\b', re.I)
_RESTRICTION_RE = re.compile(r'téléchargement\s+non\s+autorisé\s+dans\s+certains\s+pays', re.I)
_MAX_CARD_PLAINTEXT = 12_000


def _ebook_release_evidence(value):
    """Source-specific release date, not historical publication of the work."""
    match = _RELEASE_RE.search(value[:_MAX_CARD_PLAINTEXT])
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), '%d/%m/%Y').date().isoformat()
    except ValueError:
        return None


def _rights_warning_attested(value):
    return bool(_RESTRICTION_RE.search(value[:_MAX_CARD_PLAINTEXT]))


def _safe_catalog_link(base_url, raw_href, kind):
    """Only publish same-origin, structurally identified catalogue links.

    No redirects are followed here; an untrusted HTML card or persisted item
    must not choose an arbitrary transport destination or Referer.
    """
    if (type(raw_href) is not str or not raw_href or len(raw_href) > 4096
            or any(ch.isspace() or unicodedata.category(ch) in {'Cc', 'Cf', 'Cs'}
                   for ch in raw_href)):
        return None
    try:
        base = urlsplit(base_url)
        target = urlsplit(urljoin(base_url, raw_href))
        def origin(parts):
            return (parts.scheme.casefold(), (parts.hostname or '').casefold(),
                    parts.port if parts.port is not None
                    else 443 if parts.scheme == 'https' else 80 if parts.scheme == 'http' else None)
        if (origin(base) != origin(target) or target.scheme not in {'http', 'https'}
                or base.username or base.password or target.username or target.password
                or target.fragment):
            return None
        path = target.path.casefold()
        if kind == 'detail':
            if not path.endswith('/details.php'):
                return None
        elif kind == 'epub':
            if not path.endswith('.epub'):
                if not path.endswith('/newsendbook.php'):
                    return None
                pairs = parse_qsl(target.query, keep_blank_values=True,
                                  strict_parsing=True, max_num_fields=32)
                if sum(k.casefold() == 'format' and v.casefold() == 'epub'
                       for k, v in pairs) != 1 or sum(k.casefold() == 'format'
                                                       for k, _ in pairs) != 1:
                    return None
        else:
            return None
        return target.geturl()
    except ValueError:
        return None


DESCRIPTOR=ProviderDescriptor(
    id='ebooksgratuits', display_name='Ebooks libres et gratuits',
    description='Catalogue Calibre public via OPDS Atom (COPS) ; téléchargement EPUB.',
    state='ready',
    capabilities=ProviderCapabilities(search=True,holdings=True,direct_download=True),
    search_capabilities=ProviderSearchCapabilities(
        fields=('all','title','author','subject','category','genre'),
        scopes=('all','digital'), pagination='page', page_size=50,
        max_pages=MAX_REQUESTED_PAGES,
        notes=('Recherche amont OPDS/OpenSearch générale ; champs spécialisés filtrés localement sur la page chargée, couverture non exhaustive.',
               'Pages OPDS parcourues exclusivement via les liens rel=next du serveur ; pas de crawl implicite.',
               'Le genre Jeunesse ou une catégorie ne constituent pas une indication d’âge.')),
    # Human-readable publisher website. The COPS origin root is not an HTML
    # book page; OPDS retrieval remains on opds_feed_url (/index.php/feed).
    homepage='https://www.ebooksgratuits.com/',
    notes=('Source publique sans authentification ; OPDS Atom 1.x/COPS (pas OPDS2 JSON).',
           'Le catalogue Calibre inclut aussi des livres partenaires.'))
class Parser(HTMLParser):
    """Small structured parser for catalog <li> cards; no regex-based DOM slicing."""
    def __init__(self, base_url=BASE):
        super().__init__(convert_charrefs=True); self.base_url=str(base_url); self.cards=[]; self._depth=0; self._card=None; self._capture=None; self._ignored=0
    @staticmethod
    def _attrs(attrs): return {str(k).lower():str(v or '') for k,v in attrs}
    def handle_starttag(self,tag,attrs):
        tag=tag.lower(); a=self._attrs(attrs)
        if tag=='li':
            if self._depth==0: self._card={'title':[],'author':[],'description':[],'detail':'','epub':'','image':'','_plain':[],'_plain_size':0}
            self._depth+=1; return
        if not self._card: return
        # Script/style/template text is not visible catalogue evidence.
        if tag in {'script','style','template'}: self._ignored+=1
        classes={x.casefold() for x in a.get('class','').split()}
        if tag=='a':
            href=a.get('href','')
            low=href.casefold()
            if 'details.php' in low:
                verified=_safe_catalog_link(self.base_url,href,'detail')
                if verified: self._card['detail']=verified
            if 'newsendbook.php' in low or low.endswith('.epub'):
                verified=_safe_catalog_link(self.base_url,href,'epub')
                if verified: self._card['epub']=verified
        elif tag=='img' and not self._card['image']:
            src=a.get('src','').strip()
            if src: self._card['image']=urljoin(self.base_url,src)
        if tag in {'span','div','p'}:
            if 'listeb' in classes: self._capture='title'
            elif 'auteur' in classes: self._capture='author'
            elif classes.intersection({'resume','résumé','description','descriptif'}): self._capture='description'
    def handle_data(self,data):
        if not self._card or self._ignored: return
        if self._capture and data.strip(): self._card[self._capture].append(data.strip())
        # Only evidence already present on the returned catalog card. No
        # detail-page fetch or inference from the author's death date.
        if data.strip() and self._card['_plain_size'] < _MAX_CARD_PLAINTEXT:
            kept=data.strip()[:_MAX_CARD_PLAINTEXT-self._card['_plain_size']]
            self._card['_plain'].append(kept)
            self._card['_plain_size']+=len(kept)
    def handle_endtag(self,tag):
        tag=tag.lower()
        if tag in {'script','style','template'} and self._ignored: self._ignored-=1
        if tag in {'span','div','p'}: self._capture=None
        if tag=='li' and self._depth:
            self._depth-=1
            if self._depth==0 and self._card is not None:
                card=self._card; self._card=None
                # Do not publish untrusted raw card text or comments. Project
                # only two explicitly attested, bounded source-specific facts.
                plain=' '.join(card['_plain'])
                row={k:(' '.join(v).strip() if isinstance(v,list) else v)
                     for k,v in card.items() if not k.startswith('_')}
                row['ebook_release_date']=_ebook_release_evidence(plain)
                row['territorial_rights_warning']=_rights_warning_attested(plain)
                if row['detail'] and row['epub'] and row['title']: self.cards.append(row)
    def rows(self,text):
        self.cards=[]; self._depth=0; self._card=None; self._capture=None; self._ignored=0; self.feed(text); self.close(); return list(self.cards)
class FilterParser(HTMLParser):
    """Extract public search select options without executing page scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=True); self.options={}; self._select=None; self._value=None; self._pieces=[]
    def handle_starttag(self,tag,attrs):
        attrs={str(k).lower():str(v or '') for k,v in attrs}; tag=tag.lower()
        if tag=='select':
            name=attrs.get('name','').strip().lower()
            self._select=name if name in {'id_auteur','id_categorie','id_genre'} else None
        elif tag=='option' and self._select:
            self._value=attrs.get('value','').strip(); self._pieces=[]
    def handle_data(self,data):
        if self._value is not None and data.strip(): self._pieces.append(data.strip())
    def handle_endtag(self,tag):
        tag=tag.lower()
        if tag=='option' and self._select and self._value is not None:
            label=' '.join(self._pieces).strip()
            # These IDs are the site's already-used "all" defaults, not
            # selectable author/category/genre evidence. Matching their
            # labels (Tous/Toutes) would silently return an unfiltered page.
            default_value = {'id_auteur': '0', 'id_categorie': '1', 'id_genre': '1'}.get(self._select)
            if self._value and label and self._value != default_value:
                self.options.setdefault(self._select, []).append((self._value, label))
            self._value=None; self._pieces=[]
        elif tag=='select':
            self._select=None; self._value=None; self._pieces=[]

def _fold(value):
    return ''.join(ch for ch in unicodedata.normalize('NFKD',str(value or '').casefold()) if not unicodedata.combining(ch))

def _filter_choice(text,names,query,*,ambiguity=None):
    parser=FilterParser(); parser.feed(text); parser.close()
    needle=_fold(query).strip()
    if not needle: return None
    best=None
    # A partial author/category name can match multiple *equally ranked*
    # server choices. Picking one is not evidence of exhaustive coverage.
    equal_rank_counts={}
    for name in names:
        for value,label in parser.options.get(name,()):
            folded=_fold(label)
            if folded==needle: score=0
            elif folded.startswith(needle): score=1
            elif needle in folded: score=2
            else: continue
            candidate=(score,len(folded),name,value,label)
            if best is None or candidate<best: best=candidate
            equal_rank_counts[(name,score)]=equal_rank_counts.get((name,score),0)+1
    if ambiguity is not None:
        # Distinct subject selectors already have their own warning; only
        # count competing options inside the chosen server selector.
        ambiguity.append(best is not None and equal_rank_counts[(best[2],best[0])]>1)
    return None if best is None else best[2:]

def _attested_filter_metadata(selected,label):
    label=str(label or '').strip()
    if not label:return {}
    if selected=='id_genre':
        return {'genres':[label],'genre_evidence':'ebooksgratuits.search_filter'}
    if selected=='id_categorie':
        return {'categories':[label],'category_evidence':'ebooksgratuits.search_filter'}
    return {}

def _public_search_error(exc):
    """Fixed HTTP/transport status; never echo upstream URL, body or headers."""
    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    code = (
        'auth_required' if status == 401 else
        'access_denied' if status == 403 else
        'rate_limited' if status == 429 else
        'service_unavailable'
    )
    failure = ProviderError(f'Ebooks libres et gratuits : {code}')
    # #1917: protect the Core public-search fallback after wrapping httpx.
    # Semantic 4xx and unverified redirects are not technical outages.
    if status == 401:
        failure.code = 'search_authentication_required'
    elif status == 403:
        failure.code = 'search_access_denied'
    elif (status in {408, 429}
          or type(status) is int and 500 <= status <= 599
          or isinstance(exc, (httpx.TransportError, httpx.DecodingError))):
        failure.code = 'search_temporarily_unavailable'
        failure.retryable = False
    return failure


# A failed public download is different from a failed search. Convert ONLY
# typed broker errors into closed, actionable categories. Never forward a
# transport exception, response body, signed URL or dynamically supplied MIME.
_DOWNLOAD_HTTP_ERRORS = {
    'auth_required': 'download_auth_required',
    'access_denied': 'download_access_denied',
    'not_found': 'download_not_found',
    'rate_limited': 'download_rate_limited',
    'service_unavailable': 'download_service_unavailable',
}
_DOWNLOAD_REJECTION_ERRORS = {
    'transport.http: réponse HTML refusée': 'download_invalid_content',
    'transport.http: réponse vide': 'download_empty_response',
    'transport.http: artefact protégé refusé': 'download_protected',
    'transport.http: fichier trop volumineux': 'download_too_large',
    'transport.http: redirection hors origine refusée': 'download_redirect_denied',
    'transport.http: redirection HTTPS vers HTTP refusée': 'download_redirect_denied',
    'transport.http: destination de redirection invalide': 'download_redirect_denied',
    'transport.http: politique de redirection invalide': 'download_redirect_denied',
    'transport.http: redirection sans destination': 'download_redirect_limit',
    'transport.http: trop de redirections': 'download_redirect_limit',
    'transport.http: redirections non résolues': 'download_redirect_limit',
}


def _public_download_error(exc):
    """Return a source-scoped safe code, not provider text or a raw exception.

    This is a *classification of the failed request*, never proof that a
    catalogue entry no longer exists or that a repeat request is authorized.
    """
    if isinstance(exc, TemporaryUnavailable):
        raw = str(exc)
        prefix = 'transport.http: '
        code = raw[len(prefix):] if raw.startswith(prefix) else ''
        mapped = _DOWNLOAD_HTTP_ERRORS.get(code)
        if mapped is None and code in {
            'redirection sans destination', 'trop de redirections',
            'redirections non résolues',
        }:
            mapped = 'download_redirect_limit'
        return ProviderError('Ebooks libres et gratuits : ' +
                             (mapped or 'download_service_unavailable'))
    if isinstance(exc, UnsupportedOperation):
        raw = str(exc)
        mapped = _DOWNLOAD_REJECTION_ERRORS.get(raw)
        if mapped is None:
            if raw.startswith('transport.http: content-type refusé ('):
                mapped = 'download_invalid_content'
            elif raw.startswith('transport.http: artefact invalide ('):
                mapped = 'download_invalid_epub'
        return ProviderError('Ebooks libres et gratuits : ' +
                             (mapped or 'download_rejected'))
    if isinstance(exc, Conflict):
        return ProviderError('Ebooks libres et gratuits : download_destination_conflict')
    return ProviderError('Ebooks libres et gratuits : download_unavailable')



def _validated_public_query(query):
    """Accept public text, not arbitrary objects serialized into HTTP URLs."""
    if query is None:
        return ""  # Existing empty browse contract.
    if (type(query) is not str or len(query) > 512
            or any(unicodedata.category(ch) in {'Cc', 'Cf', 'Cs'}
                   for ch in query)):
        raise ProviderCapabilityError(
            'Ebooks libres et gratuits : invalid_query'
        )
    return query


def _public_epub_filename(title, acquisition_url):
    """Stable, collision-resistant basename for an attested public EPUB offer.

    Different editions may have the same title. A caller-controlled record_id
    must never become a path, and the acquisition URL (possibly with query
    parameters) must never appear in the basename or logs.
    """
    stem = re.sub(r'[^\w .-]', '_', title if type(title) is str else '')
    stem = stem.strip(' .')[:48].rstrip(' .')
    if not any(char.isalnum() for char in stem):
        stem = 'Livre'
    # Up to 48 Unicode code points cannot exceed the 255-byte component
    # limit once the fixed digest and suffix are added.
    digest = hashlib.sha256(acquisition_url.encode('utf-8')).hexdigest()[:16]
    return f'{stem}-{digest}.epub'


class EbooksSession(ProviderSession):
    def __init__(self,*,config,diagnostics,transport=None,**kw):
        super().__init__(descriptor=DESCRIPTOR,**kw)
        self.transport=transport
        self.base_url=str(self.options.get('base_url') or BASE).rstrip('/')+'/'
        self.maximum=int(self.options.get('max_download_bytes',100*1024*1024))
        self.diagnostics=diagnostics
        self.page_size=int(self.options.get('search_page_size',50))
        self.acquisition_backend=str(self.options.get('acquisition_backend') or 'opds').strip().lower()
        if self.acquisition_backend not in {'opds','html'}:
            raise ProviderCapabilityError('Ebooks libres et gratuits : unsupported_backend')
        self.opds_feed_url=str(self.options.get('opds_feed_url') or OPDS_FEED)
        self._opds_pages_limit=MAX_REQUESTED_PAGES
        self.client=self._new_client()

    def _new_client(self):
        # close() permanently closes an httpx.Client. Only a fresh
        # cookie-free transport can serve a subsequent explicit connect().
        # Both OPDS and legacy simulation share this lifecycle.
        return HTTPX_TRANSPORT.client(producer="provider.ebooksgratuits",
            timeout=float(self.options.get('timeout_seconds',30)),
            follow_redirects=True, trust_env=False,
            headers={
                'Accept-Language':'fr-FR,fr;q=0.9',
            },
        )

    def connect(self):
        # Lightweight test transports may expose get() but no HTTPX
        # is_closed property. Only a positively closed real client requires
        # replacement; do not hide invalid-query tests behind a new GET.
        if getattr(self.client, 'is_closed', False) is True:
            try:
                self.client=self._new_client()
            except (OSError, ValueError, TypeError, httpx.HTTPError):
                # A runtime/proxy error might include a path or credentials.
                # Do not turn a failed reopen into a connected public source
                # or show exception text in the Core/GTK status.
                raise ProviderError(
                    'Ebooks libres et gratuits : transport_unavailable'
                ) from None
        # This is a local session registration, NOT a successful OPDS probe.
        self.connected=True
        self._accounts=[Account(
            display_name='Catalogue public',unique_id='ebooksgratuits',
            provider_id='ebooksgratuits',credential_id=self.credential_id,
            provider_label=self.descriptor.display_name,is_primary=True,
        )]
        return self.accounts

    def close(self):
        self.client.close()
        self.connected=False
        self._accounts=[]
    def _fetch(self,url):
        try:r=self.client.get(url); r.raise_for_status(); return r
        except httpx.HTTPError as e: raise _public_search_error(e) from None
    @staticmethod
    def _search_payload(query,page,page_size=50):
        return {'titre':str(query or '').strip(),'offset':str(page*page_size),'Submit':'Rechercher'}
    def search(self,query,scope='all',page=0,field='all'):
        self.last_search_warnings = []
        query = _validated_public_query(query)
        if self.acquisition_backend == 'opds':
            return self._search_opds(query,scope=scope,page=page,field=field)
        # Unimplemented fields/scopes must never masquerade as zero matches.
        if type(field) is not str or field.strip().casefold() not in DESCRIPTOR.search_capabilities.fields:
            raise ProviderCapabilityError('Ebooks libres et gratuits : unsupported_field')
        if type(scope) is not str or scope.strip().casefold() not in DESCRIPTOR.search_capabilities.scopes:
            raise ProviderCapabilityError('Ebooks libres et gratuits : unsupported_scope')
        if not self.connected: raise ProviderError('Ebooks libres et gratuits : connecter le catalogue')
        if type(page) is not int or not 0 <= page < MAX_REQUESTED_PAGES:
            # Invalid pages must never mean a successful page-zero search,
            # nor send a malformed offset to the public server.
            raise ProviderCapabilityError('Ebooks libres et gratuits : invalid_page')
        field=field.strip().casefold(); attested_metadata={}
        if field in {'author','subject','category','genre'}:
            if not str(query or '').strip():
                return []
            try:
                form=self._fetch(self.base_url+'ebooks.php')
                names=('id_auteur',) if field=='author' else (('id_categorie',) if field=='category' else (('id_genre',) if field=='genre' else ('id_categorie','id_genre')))
                selector=FilterParser(); selector.feed(form.text); selector.close()
                available=tuple(name for name in names if selector.options.get(name))
                if not available:
                    # HTML changed or filter route unavailable: this is not
                    # evidence that the catalogue contains no matching books.
                    raise ProviderCapabilityError('Ebooks libres et gratuits : field_route_unavailable')
                if field == 'subject' and len(available) < len(names):
                    self.last_search_warnings.append('partial_results : un sélecteur catégorie/genre est indisponible')
                ambiguity=[]
                choice=_filter_choice(form.text,names,query,ambiguity=ambiguity)
                if choice is None: return []
                if ambiguity[0]:
                    self.last_search_warnings.append(
                        'partial_results : plusieurs options du filtre ; une seule interrogée'
                    )
                if field == 'subject' and len(available) == 2:
                    # Both independently selectable routes may match the
                    # query. A single request below then covers just one of
                    # them, not the union of category and genre hits.
                    matching_routes = sum(
                        _filter_choice(form.text, (name,), query) is not None
                        for name in available
                    )
                    if matching_routes > 1:
                        self.last_search_warnings.append(
                            'partial_results : catégorie et genre correspondants ; un seul interrogé'
                        )
                selected,value,label=choice
                attested_metadata=_attested_filter_metadata(selected,label)
                params={'begin':str(page*self.page_size),'offset':str(self.page_size),'option':'search',
                        'id_auteur':'0','id_categorie':'1','id_genre':'1'}
                params[selected]=value
                r=self.client.get(self.base_url+'ebooks.php',params=params); r.raise_for_status()
            except httpx.HTTPError as e: raise _public_search_error(e) from None
        elif field in {'all','title','titre'}:
            if field == 'all' and str(query or '').strip():
                # The upstream form sends the term as a titre selector. It
                # does not attest a catalogue-wide author/subject search.
                # Publish this limitation even when that title search has
                # legitimately returned no rows.
                self.last_search_warnings.append(
                    'partial_results : recherche générale limitée au titre'
                )
            if query or page:
                try:
                    r=self.client.post(self.base_url+'ebooks.php',data=self._search_payload(query,page,self.page_size)); r.raise_for_status()
                except httpx.HTTPError as e: raise _public_search_error(e) from None
            else:
                r=self._fetch(self.base_url+'ebooks.php')
        else:
            raise ProviderCapabilityError('Ebooks libres et gratuits : unsupported_field')
        out=[]
        for card in Parser(self.base_url).rows(r.text):
            detail=card['detail']; epub=card['epub']; rid=hashlib.sha256(detail.encode()).hexdigest()[:24]
            metadata={'license_note':'Vérifier les droits selon le pays',**attested_metadata}
            if card.get('ebook_release_date'):
                metadata['ebook_release_date']=card['ebook_release_date']
                metadata['ebook_release_evidence']='ebooksgratuits.catalogue.parution'
            if card.get('territorial_rights_warning'):
                metadata['territorial_rights_warning']=True
                metadata['territorial_rights_evidence']='ebooksgratuits.catalogue.warning'
                metadata['license_note']='Attention : téléchargement non autorisé dans certains pays ; vérifier les droits locaux'
            out.append(SearchResult(id='ebooksgratuits:'+rid,record_id=rid,docbase='EBOOKSGRATUITS',medium='digital',title=card['title'],author=card['author'] or None,type='Livre numérique',format='EPUB',publisher='Ebooks libres et gratuits',published_at=None,description=card['description'] or None,thumbnail_url=card['image'] or None,friendly_url=detail,scenario='EBOOKSGRATUITS_CATALOG',source_provider='ebooksgratuits',source_credential=self.credential_id,download_url=epub,content_type='ebook',source_collection='public',owned=False,downloadable=True,metadata=metadata))
        return out[:self.page_size]
    def _search_opds(self,query,scope='all',page=0,field='all'):
        return _opds_search(self,query,scope=scope,page=page,field=field)
    def holdings(self, item, search_terms=''):
        """Read-only EPUB-offer evidence, never successful delivery evidence.

        Search and older cached rows may lack a valid EPUB offer. Reuse the
        download admission predicates without contacting the provider or
        pretending the file has been retrieved successfully.
        """
        url = getattr(item, 'download_url', None)
        metadata = getattr(item, 'metadata', None)
        opds = type(metadata) is dict and metadata.get('acquisition_source') == 'opds-calibre'
        verified = False
        if (getattr(item, 'source_provider', None) == self.provider_id
                and type(url) is str
                and getattr(item, 'format', None) == 'EPUB'
                and getattr(item, 'downloadable', None) is True):
            allowed = (_safe_opds_link(self.opds_feed_url, url, 'download')
                       if opds else _safe_catalog_link(self.base_url, url, 'epub'))
            if allowed == url:
                # An old XML OPDS "friendly_url" remains harmless. For the
                # legacy HTML backend, a claimed detail URL must also be
                # admissible before the UI can advertise its offer.
                friendly = getattr(item, 'friendly_url', None)
                verified = bool(
                    opds or not friendly
                    or _safe_catalog_link(self.base_url, friendly, 'detail') == friendly
                )
        return ItemDetail(
            record_id=item.record_id, docbase='EBOOKSGRATUITS',
            title=item.title, author=item.author, type=item.type,
            year=None, thumbnail_url=item.thumbnail_url,
            availability_status='available' if verified else 'unknown',
            availability_label=('Offre EPUB publiée ; téléchargement non vérifié'
                                if verified else 'Offre EPUB non vérifiée'),
            holdings=[], source_provider=self.provider_id,
            source_credential=self.credential_id,
        )
    def download_direct(self,item,target_dir):
        if item.source_provider!=self.provider_id or not item.download_url: raise ProviderError('Ebooks libres et gratuits : offre inconnue')
        # A forged or stale record cannot redirect the broker to a foreign host.
        opds_item = isinstance(getattr(item,'metadata',None),dict) and item.metadata.get('acquisition_source') == 'opds-calibre'
        if opds_item:
            epub=_safe_opds_link(self.opds_feed_url,item.download_url,'download')
            # A legacy/cached OPDS row may still expose its XML feed as
            # friendly_url. It is NOT an HTML page, but neither does that
            # make a separately attested EPUB acquisition invalid. Ignore
            # the unverified Web link completely: never use it as Referer,
            # return value or an implicit navigation/download target.
            detail=(_opds_public_html_link(self.opds_feed_url,item.friendly_url)
                    if item.friendly_url else None)
        else:
            epub=_safe_catalog_link(self.base_url,item.download_url,'epub')
            detail=_safe_catalog_link(self.base_url,item.friendly_url,'detail') if item.friendly_url else None
        if (epub != item.download_url
                or (not opds_item and item.friendly_url
                    and detail != item.friendly_url)):
            raise ProviderCapabilityError('Ebooks libres et gratuits : invalid_catalog_link')
        if self.transport is None: raise ProviderError('Ebooks libres et gratuits : transport.http indisponible')
        d=Path(target_dir); d.mkdir(parents=True,exist_ok=True)
        dest=d/_public_epub_filename(item.title,item.download_url)
        headers={
            'Accept':'application/epub+zip, application/zip;q=0.8, application/octet-stream;q=0.5, */*;q=0.2',
            # Never send a historical www-site referer to the OPDS origin.
            'Referer': (detail or self.opds_feed_url)
                       if opds_item else (item.friendly_url or self.base_url),
        }
        source={
            'url':item.download_url,
            'headers':headers,
            'max_bytes':self.maximum,
            # COPS may redirect to another public fetch URL on its own
            # origin. Reject foreign hosts/downgrades before the second GET.
            'max_redirects':2 if opds_item else None,
            'allowed_content_types':('application/epub+zip','application/zip','application/octet-stream'),
            'expected_format':'epub',
            'overwrite':True,
        }
        if opds_item:
            source['redirect_policy']='same_origin'
            source['classify_http_status']=True
        failure = None
        try:
            dest = Path(self.transport.fetch(source, dest))
        except Cancelled:
            raise  # Cancellation is cooperative control flow, never a failed EPUB.
        except (TemporaryUnavailable, UnsupportedOperation, Conflict) as exc:
            # Keep the observed broker failure in the shared raw Core/plugin
            # journal; the closed UI-facing result below is not a substitute
            # for its real URL/status/cause when diagnosing a partial outage.
            _DOWNLOAD_TRACE.error(
                "EPUB fetch failed provider=%s record_id=%s url=%s",
                self.provider_id, item.record_id, item.download_url,
                exc_info=True,
            )
            failure = _public_download_error(exc)
        except Exception:
            _DOWNLOAD_TRACE.error(
                "EPUB fetch failed provider=%s record_id=%s url=%s",
                self.provider_id, item.record_id, item.download_url,
                exc_info=True,
            )
            # Keep the established non-authorizing, closed business outcome.
            # Only the raw journal retains the original exception/traceback.
            failure = ProviderError('Ebooks libres et gratuits : download_unavailable')
        if failure is not None:
            # Raise OUTSIDE the handler: "from None" suppresses traceback
            # display but still retains the private error in __context__.
            raise failure
        return dest,(detail or item.download_url) if opds_item else (item.friendly_url or self.base_url)
class Plugin(ProviderPlugin):
    descriptor=DESCRIPTOR
    def __init__(self): self._sessions=[]; self.context=None; self.transport=None
    def activate(self,context):
        self.context=context; self.transport=context.service('transport.http')
        if self.transport is None: raise ProviderError('Ebooks libres et gratuits : transport.http requis')
        return self
    def deactivate(self):
        for session in self._sessions:
            try: session.close()
            except Exception: pass
        self._sessions.clear(); self.transport=None; self.context=None
    def create_session(self,*,credential_id,label,credentials,options,config,storage,diagnostics):
        session=EbooksSession(credential_id=credential_id,label=label,credentials=credentials,options=options,config=config,diagnostics=diagnostics,transport=self.transport); self._sessions.append(session); return session
PLUGIN=Plugin()


# Calibre/COPS OPDS 1.x adapter (#1084). The old HTML parser remains available
# solely for local regression/simulation via acquisition_backend="html".
OPDS_FEED = 'https://opds2.ebooksgratuits.org/index.php/feed'
ATOM = '{http://www.w3.org/2005/Atom}'
DC = '{http://purl.org/dc/terms/}'
OPDS_MAX_FEED_BYTES = 2 * 1024 * 1024
# 2 MiB of XML can still contain thousands of tiny nodes or deep nesting.
# Bound the parser independently from the wire size before building a tree.
OPDS_MAX_ELEMENTS = 12000
OPDS_MAX_DEPTH = 96


def _safe_opds_link(feed_url, href, kind):
    """Never let remote Atom/OpenSearch input choose another HTTP origin."""
    if not isinstance(href,str) or not href or len(href)>4096 or any(
        ch.isspace() or unicodedata.category(ch) in {'Cc','Cf','Cs'}
        for ch in href
    ):
        return None
    try:
        base=urlsplit(feed_url)
        dst=urlsplit(urljoin(feed_url,href))
        def origin(p):
            return (p.scheme.lower(),(p.hostname or '').lower(),
                    p.port if p.port is not None else (443 if p.scheme=='https' else 80))
        if (base.scheme not in {'https','http'} or origin(base)!=origin(dst)
                or base.username or base.password or dst.username or dst.password
                or dst.fragment):
            return None
        # Decode solely for validation; encoded controls and escaped slashes
        # must not bypass endpoint classification.
        from urllib.parse import unquote
        decoded_path=unquote(dst.path)
        decoded_query=unquote(dst.query)
        # Encoded spaces are legitimate inside OpenSearch query text; reject
        # controls and format characters, not a normal multiword search.
        if (any(ch.isspace() or unicodedata.category(ch) in {'Cc','Cf','Cs'}
                for ch in decoded_path)
                or any(unicodedata.category(ch) in {'Cc','Cf','Cs'}
                       for ch in decoded_query)):
            return None
        path=dst.path.lower()
        if kind == 'download':
            # Unlike feed navigation, the EPUB URL was checked only against
            # a raw /index.php/fetch prefix. Encoded dot-segments, slashes or
            # double-encoding can be interpreted as a DIFFERENT route by a
            # public reverse proxy after validation. Do not hand such URLs
            # to the broker, including when they come from a cached row.
            if ('%2f' in path or '%5c' in path or '%25' in path
                    or '\\' in decoded_path
                    or any(segment in {'.', '..'}
                           for segment in decoded_path.split('/'))):
                return None
        # Calibre/COPS publishes a dedicated fetch route. A suffix .epub
        # alone is not enough: /index.php/account/private.epub can otherwise
        # masquerade as a public acquisition endpoint on the same origin.
        # Keep the historically recognized /fetch.php alias, but refuse
        # other action URLs even when their name contains "fetch".
        if kind=='download' and not (
            path.startswith('/index.php/fetch/')
            or path in {'/index.php/fetch', '/fetch.php', '/index.php/fetch.php'}
        ):
            return None
        if kind=='feed':
            # A same-origin URL is not necessarily a feed. Previously any
            # /index.php/* action qualified, including /index.php/account/*
            # and mutating routes linked from untrusted OPDS XML.
            allowed_feed = (
                path == '/index.php/feed'
                or path.startswith('/index.php/feed/')
                or path in {'/feed', '/feed.php', '/index.php/feed.php',
                            '/index.php/opensearch.xml'}
            )
            # Encoded dot segments/slashes must not turn an apparent feed
            # route into an account/admin action after server decoding.
            parts = decoded_path.casefold().split('/')
            if (not allowed_feed or '\\' in decoded_path
                    or any(segment in {'.', '..', 'account', 'admin',
                                       'customer', 'login', 'checkout',
                                       'session'} for segment in parts)
                    or '%25' in path
                    or '%2f' in path or '%5c' in path):
                return None
        return dst.geturl()
    except ValueError:
        return None



def _opds_source_link(session, source_feed_url, raw_href, kind):
    """Resolve an Atom link against ITS document, then recheck trusted root.

    A nested COPS feed can publish ../fetch/42.epub or a relative next
    subsection. Resolving it against the configured /index.php/feed root
    silently changes the URL and misclassifies real offers as unavailable.
    Both checks must succeed: the source document is not authority to
    cross origins, even if a caller supplies a forged feed_url.
    """
    resolved = _safe_opds_link(source_feed_url, raw_href, kind)
    if resolved is None:
        return None
    return (resolved if _safe_opds_link(session.opds_feed_url, resolved, kind)
            == resolved else None)


class _BoundedOPDSTree(ET.TreeBuilder):
    """Deny DTDs (including UTF-16) and unbounded XML element trees."""

    def __init__(self):
        super().__init__()
        self.depth=0
        self.count=0

    def doctype(self, name, pubid, system):
        # A raw b'<!DOCTYPE' byte scan misses UTF-16/XML encodings.
        # This parser callback rejects the declaration before entity use.
        raise ProviderError('Ebooks libres et gratuits : invalid_opds_feed')

    def start(self, tag, attrs):
        self.depth+=1
        self.count+=1
        if self.depth>OPDS_MAX_DEPTH or self.count>OPDS_MAX_ELEMENTS:
            raise ProviderError('Ebooks libres et gratuits : invalid_opds_feed')
        return super().start(tag,attrs)

    def end(self, tag):
        result=super().end(tag)
        self.depth-=1
        return result


def _malformed_opds_protocol():
    """Malformed supplier Atom is not a verified zero or retriable query."""
    failure = ProviderError('Ebooks libres et gratuits : invalid_opds_feed')
    failure.code = 'search_temporarily_unavailable'
    failure.retryable = False
    return failure


def _unverified_html_opds_response():
    """HTML 200 may be SSO/login, not evidence of a public OPDS outage.

    Do not permit stale public search fallback or silently return zero. The
    fixed provider error must not disclose the HTML body or redirect URL.
    """
    return ProviderError('Ebooks libres et gratuits : invalid_opds_feed')


def _is_html_document(root):
    return (type(root.tag) is str
            and root.tag.rsplit('}', 1)[-1].casefold() == 'html')


def _xml_root(content):
    if type(content) is not bytes or len(content)>OPDS_MAX_FEED_BYTES:
        raise ProviderError('Ebooks libres et gratuits : invalid_opds_feed')
    try:
        # Enforce DTD/entity, depth and element budgets during XML parsing.
        # A prior byte-pattern scan alone cannot qualify non-UTF8 XML.
        return ET.fromstring(content,parser=ET.XMLParser(target=_BoundedOPDSTree()))
    except ET.ParseError:
        # A common reverse proxy or login gateway returns HTTP 200 HTML.
        # Even invalid HTML syntax is NOT evidence that the public Atom
        # service itself is unavailable; don't authorize a stale fallback.
        prefix = content.lstrip(b' \t\r\n\xef\xbb\xbf').lower()[:80]
        if prefix.startswith((b'<html', b'<!doctype html')):
            failed = _unverified_html_opds_response()
        else:
            failed = _malformed_opds_protocol()
        # Never raise from inside the ParseError handler: Core deliberately
        # inspects suppressed __context__ for hidden HTTP refusals, and a
        # hidden ParseError would reclassify this HTML login as an outage.
    except (RecursionError, OverflowError, ValueError):
        # Parser/budget refusals remain non-fallback security failures.
        raise ProviderError('Ebooks libres et gratuits : invalid_opds_feed') from None
    raise failed


def _opds_fetch(session,url):
    checked=_safe_opds_link(session.opds_feed_url,url,'feed')
    if checked != url:
        raise ProviderError('Ebooks libres et gratuits : invalid_opds_link')
    # A GET eagerly buffers the ENTIRE response before _xml_root gets to
    # enforce the 2 MiB limit. Stream the raw HTTP body under that bound.
    # Require identity encoding: a compressed 2 MiB body could decompress
    # into an unbounded XML bomb before iter_bytes() returns its first chunk.
    # Do not read a redirect body; no new retries or origins.
    try:
        for _ in range(4):
            with session.client.stream(
                'GET',checked,follow_redirects=False,
                headers={
                    'Accept':'application/atom+xml, application/opensearchdescription+xml',
                    'Accept-Encoding':'identity',
                }
            ) as response:
                if response.status_code in {301,302,303,307,308}:
                    target=_safe_opds_link(
                        session.opds_feed_url,response.headers.get('location',''),'feed'
                    )
                    if not target:
                        raise ProviderError('Ebooks libres et gratuits : invalid_opds_link')
                    checked=target
                    continue
                response.raise_for_status()
                # Identity response only: httpx decompresses entire chunks
                # before applying our byte bound otherwise. Do not accept a
                # server ignoring the explicit Accept-Encoding request.
                if response.headers.get('content-encoding','').strip().casefold() not in {'','identity'}:
                    raise ProviderError('Ebooks libres et gratuits : invalid_opds_feed')
                # httpx.MockTransport and some compatible adapters hand
                # back a Response built with text=/content=, already fully
                # buffered *before* our stream context. Preserve those
                # deterministic fixtures, but still validate their size and
                # XML; real network responses take the bounded raw-stream path.
                if response.is_stream_consumed:
                    if not response.is_closed:
                        raise ProviderError('Ebooks libres et gratuits : invalid_opds_feed')
                    content=response.content
                    if type(content) is not bytes or len(content)>OPDS_MAX_FEED_BYTES:
                        raise ProviderError('Ebooks libres et gratuits : invalid_opds_feed')
                    return _xml_root(content),checked
                size=0
                chunks=[]
                # Content-Length is advisory and may be false or absent.
                # Always enforce the cap on the bytes read from the stream.
                for chunk in response.iter_raw(chunk_size=65536):
                    size+=len(chunk)
                    if size>OPDS_MAX_FEED_BYTES:
                        raise ProviderError('Ebooks libres et gratuits : invalid_opds_feed')
                    chunks.append(chunk)
                return _xml_root(b''.join(chunks)),checked
        raise ProviderError('Ebooks libres et gratuits : invalid_opds_link')
    except httpx.HTTPError as e:
        raise _public_search_error(e) from None


def _opds_links(node,base):
    for elem in node.findall(ATOM+'link'):
        raw=elem.get('href','')
        yield (elem.get('rel','alternate').lower(),elem.get('type','').lower(),raw,
               _safe_opds_link(base,raw,'detail'))


def _opds_find_link(node,base,rel):
    # A *missing* rel=next ends a page set; a declared but unverified
    # rel=next does not prove that the following page has zero books.
    # Fail closed on multiple conflicting next links or invalid origins.
    matches=[]
    for r,mime,raw,_checked in _opds_links(node,base):
        if r != rel:
            continue
        # A backslash is not a valid route separator in the attested
        # COPS Atom next-link contract; URL parsers disagree on its meaning.
        checked=_safe_opds_link(base,raw,'feed') if '\\' not in raw else None
        if not checked:
            raise ProviderError('Ebooks libres et gratuits : invalid_opds_pagination')
        matches.append((checked,mime,raw))
        if len({value[0] for value in matches}) > 1:
            raise ProviderError('Ebooks libres et gratuits : invalid_opds_pagination')
    return matches[0] if matches else (None,None,None)


def _opds_template(href,query):
    """Expand only standard OpenSearch placeholders; no invented endpoint."""
    if not isinstance(href,str) or '{searchTerms' not in href:
        return None
    values={'searchTerms':quote(query,safe=''), 'count':'50',
            'startIndex':'1', 'startPage':'1', 'language':'fr',
            'inputEncoding':'UTF-8', 'outputEncoding':'UTF-8'}
    def repl(match):
        tag=match.group(1).rstrip('?')
        return values.get(tag,'') if match.group(1).endswith('?') or tag in values else match.group(0)
    result=re.sub(r'\{([A-Za-z][A-Za-z0-9]*\??)\}',repl,href)
    return result if '{' not in result and '}' not in result else None


def _opds_search_url(session,root,base,query):
    for rel,mime,raw,_ in _opds_links(root,base):
        if rel!='search':
            continue
        if 'opensearchdescription+xml' in mime:
            address=_opds_source_link(session,base,raw,'feed')
            if not address: continue
            description,description_url=_opds_fetch(session,address)
            if description.tag.rsplit('}',1)[-1]!='OpenSearchDescription':
                raise ProviderError('Ebooks libres et gratuits : invalid_opds_feed')
            for child in description:
                if child.tag.rsplit('}',1)[-1]!='Url': continue
                if 'atom+xml' not in child.get('type','').lower(): continue
                expanded=_opds_template(child.get('template',''),query)
                safe=_opds_source_link(session,description_url,expanded,'feed') if expanded else None
                if safe: return safe
        elif 'atom+xml' in mime:
            expanded=_opds_template(raw,query)
            safe=_opds_source_link(session,base,expanded,'feed') if expanded else None
            if safe: return safe
    raise ProviderCapabilityError('Ebooks libres et gratuits : search_route_unavailable')


def _opds_entry_is_book(session, entry, base):
    """Follow the same definition of a book at the root and search scopes.

    OPDS acquisition entries can be PDF-only (still catalogue notices), and
    EPUB alternate links are accepted only if their URL is qualified as a
    same-origin public download. Navigation entries are not books.
    """
    # An arbitrary "acquisition" rel by itself is not a book. Require
    # a safe same-origin public fetch route AND a qualified ebook MIME,
    # so a foreign/private/missing link cannot suppress root navigation or
    # create a fake public catalogue record. PDF-only acquisitions remain
    # visible as metadata-only records; this plugin downloads EPUB only.
    return any(
        bool(_opds_source_link(session, base, raw, 'download'))
        and (
            ('/acquisition' in rel
             and mime.split(';', 1)[0].strip()
                 in {'application/epub+zip', 'application/pdf',
                     'application/x-pdf',
                     'application/x-mobipocket-ebook'})
            or (rel == 'alternate'
                and mime.split(';', 1)[0].strip() == 'application/epub+zip')
        )
        for rel, mime, raw, _ in _opds_links(entry, base)
    )


def _opds_browse_url(session,root,base):
    candidates=[]
    for entry in root.findall(ATOM+'entry'):
        if _opds_entry_is_book(session,entry,base):
            return base
        title=(entry.findtext(ATOM+'title') or '').casefold()
        for r,m,raw,_ in _opds_links(entry,base):
            if 'atom+xml' not in m or '/acquisition' in r: continue
            href=_opds_source_link(session,base,raw,'feed')
            if href:
                preference=0 if any(x in title for x in ('all books','tous les livres','tous les ebooks','all ebooks')) else 1
                candidates.append((preference,href))
    if not candidates:
        # A root feed with no books or navigable catalogues is not a verified
        # empty search result.
        raise ProviderCapabilityError('Ebooks libres et gratuits : browse_route_unavailable')
    # A generic navigation root does not prove its branches form a union.
    # In particular, choosing the first Authors/Recent/Genres catalogue
    # must never masquerade as an exhaustive field=all browse. A uniquely
    # labelled "All books" route is the only root selection which does not
    # require an explicit partial-coverage warning.
    best_rank, selected = min(candidates, key=lambda item: item)
    best_urls = {href for rank, href in candidates if rank == best_rank}
    if best_rank != 0:
        session.last_search_warnings.append(
            'partial_results : aucune branche Tous les livres attestée ; '
            'un seul sous-catalogue OPDS parcouru'
        )
    elif len(best_urls) > 1:
        session.last_search_warnings.append(
            'partial_results : plusieurs branches Tous les livres ; '
            'une seule interrogée'
        )
    return selected


def _opds_text(elem,tag):
    child=elem.find(tag)
    if child is None:return None
    return html.unescape(' '.join(x.strip() for x in child.itertext() if x.strip()))[:12000] or None


def _opds_public_html_link(feed_url, raw):
    """Only a safe, explicit Atom HTML alternative is a readable book page.

    An Atom/OPDS feed, fetch route, private account operation or unproved
    query URL never qualifies. Never synthesize URLs from book IDs or titles.
    """
    safe = _safe_opds_link(feed_url, raw, 'detail')
    if not safe:
        return None
    from urllib.parse import unquote
    address = urlsplit(safe)
    # Validate nested-encoded aliases exactly as for persisted Core notices.
    # A MIME="text/html" assertion never transforms an OPDS feed into HTML.
    path = unquote(unquote(address.path)).casefold().rstrip('/')
    segments = path.split('/')
    if (address.query
            or any(part in {'.', '..', 'account', 'admin', 'customer',
                            'login', 'checkout', 'session'} for part in segments)
            or path.endswith(('.xml', '.atom', '.opds'))
            or path in {'/feed', '/feed.php', '/fetch.php', '/index.php/feed',
                        '/index.php/fetch', '/index.php/feed.php',
                        '/index.php/fetch.php', '/index.php/opensearch.xml'}
            or path.startswith(('/feed/', '/fetch/',
                                '/index.php/feed/', '/index.php/fetch/'))
            or any('%' in part for part in segments)):
        return None
    return safe


def _opds_author_match(query, names):
    """Match a declared Atom author, never a title or mixed contributors.

    OPDS names may be displayed as "SURNAME, Forename" while the reader
    searches "Forename SURNAME". Preserve the existing case/accent-insensitive
    substring match; for 2+ tokens permit a same-author exact-token
    permutation. Do not infer aliases or join two distinct authors together.
    """
    if type(query) is not str or type(names) not in (list, tuple):
        return False
    wanted = _fold(query).strip()
    if not wanted:
        return False
    words = tuple(re.findall(r'[^\W_]+', wanted, flags=re.UNICODE))
    for name in names[:16]:
        if type(name) is not str or not 1 <= len(name) <= 512:
            continue
        attested = _fold(name)
        if wanted in attested:
            return True
        if len(words) >= 2:
            parts = set(re.findall(r'[^\W_]+', attested, flags=re.UNICODE))
            if all(word in parts for word in words):
                return True
    return False


def _opds_items(session,feed,feed_url):
    items=[]
    for entry in feed.findall(ATOM+'entry'):
        # OPDS search responses may mix navigational scopes with acquisition
        # entries. Never publish a subsection as an undownloadable "book".
        if not _opds_entry_is_book(session,entry,feed_url):
            continue
        title=_opds_text(entry,ATOM+'title')
        if not title:continue
        entry_id=_opds_text(entry,ATOM+'id')
        if not entry_id:continue
        epub=None; alternate=None; thumbnail=None; formats=set()
        for rel,mime,raw,_ in _opds_links(entry,feed_url):
            checked=(_opds_source_link(session,feed_url,raw,'download')
                     if mime.split(';',1)[0].strip()=='application/epub+zip'
                     else None)
            if checked and ('/acquisition' in rel or rel=='alternate'):
                epub=epub or checked; formats.add('EPUB')
            # Only a declared HTML alternative can become "Voir en ligne".
            if rel=='alternate' and mime.split(';',1)[0].strip() in {
                'text/html', 'application/xhtml+xml'
            }:
                alternate=alternate or _opds_public_html_link(
                    feed_url,raw
                )
            if 'image/thumbnail' in rel or rel.endswith('/thumbnail'):
                thumbnail=thumbnail or _opds_source_link(session,feed_url,raw,'detail')
        author_nodes=entry.findall(ATOM+'author')
        authors=[_opds_text(n,ATOM+'name') for n in author_nodes]
        author=', '.join(n for n in authors if n) or None
        categories=[]; genres=[]
        for category in entry.findall(ATOM+'category'):
            label=(category.get('label') or category.get('term') or '').strip()
            if not label:continue
            if 'genre' in category.get('scheme','').casefold():
                genres.append(label)
            else:categories.append(label)
        metadata={'license_note':'Vérifier les droits selon le pays',
                  'acquisition_source':'opds-calibre',
                  'acquisition_evidence':'opds.entry.link',
                  'catalogue_url':session.opds_feed_url}
        if authors:
            # Source-exact individual Atom authors, unlike the joined
            # presentation label. These are public bibliographic facts,
            # never private identities or inferred contributor roles.
            metadata['opds_author_names']=[name for name in authors[:16]
                                          if name and len(name)<=512]
            metadata['opds_author_evidence']='opds.atom.author.name'
        if categories:
            metadata['categories']=list(dict.fromkeys(categories))
            metadata['category_evidence']='opds.atom.category'
        if genres:
            metadata['genres']=list(dict.fromkeys(genres))
            metadata['genre_evidence']='opds.atom.category.genre'
        if formats:metadata['available_formats']=sorted(formats)
        updated=_opds_text(entry,ATOM+'updated')
        if updated: metadata['catalog_updated_at']=updated
        rid=hashlib.sha256((session.opds_feed_url+'|'+entry_id).encode('utf-8')).hexdigest()[:24]
        items.append(SearchResult(
            id='ebooksgratuits:'+rid,record_id=rid,docbase='EBOOKSGRATUITS',
            medium='digital',title=title,author=author,type='Livre numérique',
            format='EPUB' if epub else None,
            publisher=_opds_text(entry,DC+'publisher'),
            published_at=None,
            description=_opds_text(entry,ATOM+'summary') or _opds_text(entry,ATOM+'content'),
            thumbnail_url=thumbnail,
            friendly_url=alternate,
            scenario='EBOOKSGRATUITS_CATALOG',
            source_provider='ebooksgratuits',
            source_credential=session.credential_id,
            download_url=epub,content_type='ebook',source_collection='public',
            owned=False,downloadable=bool(epub),metadata=metadata))
    return items



def _opds_author_navigation_match(query, label):
    """Rank ONLY an author-shaped COPS navigation label in inverse order.

    A navigation entry is not a Book/Atom author assertion. Restrict the
    permutation to the conventional 'Surname, Forename' *single person*
    shape, with no extra author delimiters or descriptive route text. The
    actual returned books still require their own Atom author proof.
    """
    if type(query) is not str or type(label) is not str or len(label) > 256:
        return False
    if (label.count(',') != 1 or any(marker in label for marker in
            (';', '&', '/', '|', '\n', '\r'))):
        return False
    surname, forename = (part.strip() for part in label.split(','))
    # A prose heading like "Books by HUGO, Victor" is not an author
    # identity assertion. It cannot be promoted to an exact person link.
    if _fold(surname).startswith((
            'books ', 'livres ', 'auteur ', 'authors ', 'author ',
            'catalogue ', 'recherche ', 'notices ', 'ouvrages ')):
        return False
    if (not surname or not forename
            or not all(re.fullmatch(r"[^\W_]+(?:[ '\-][^\W_]+)*",
                                     part, flags=re.UNICODE)
                       for part in (surname, forename))):
        return False
    return _opds_author_match(query, (label,))


def _opds_load_acquisition(session,initial,field,needle):
    """Follow bounded COPS navigation: search -> Books/Authors/Tags -> entries.

    COPS search is commonly a *navigation* feed listing result scopes, not a
    list of acquisitions. A search with zero entries is distinct from that.
    Never recursively crawl the catalogue or claim union of result scopes.
    """
    url=initial
    seen=set()
    wanted=('author','auteur') if field=='author' else (
        ('tag','étiquette','etiquette','genre','catégor') if field in {'subject','category','genre'}
        else ('book','livre','ebook'))
    for _ in range(5):
        if url in seen:
            raise ProviderError('Ebooks libres et gratuits : invalid_opds_pagination')
        seen.add(url)
        feed,actual=_opds_fetch(session,url)
        if feed.tag!=ATOM+'feed':
            raise (_unverified_html_opds_response() if _is_html_document(feed)
                   else _malformed_opds_protocol()) from None
        entries=feed.findall(ATOM+'entry')
        # The item projector supports an EPUB with rel=alternate as well
        # as OPDS acquisition links. The navigation detector must agree:
        # otherwise a valid page of books becomes browse_route_unavailable.
        # Only a checked public EPUB URL counts, not an arbitrary rel or a
        # same-looking untrusted URL to another origin.
        has_acquisition=any(
            _opds_entry_is_book(session, entry, actual) for entry in entries
        )
        if has_acquisition:
            # COPS can mix direct acquisitions and links to more books.
            # We only publish the qualified direct books; the unseen
            # subsections cannot be counted as searched or empty.
            navigation = any(
                'atom+xml' in mime and '/acquisition' not in rel
                and _opds_source_link(session,actual,raw,'feed')
                for entry in entries
                if not _opds_entry_is_book(session,entry,actual)
                for rel,mime,raw,_ in _opds_links(entry,actual)
            )
            warning = (
                'partial_results : livres et sous-catalogues OPDS mélangés ; '
                'seuls les livres directs sont parcourus'
            )
            if navigation and warning not in session.last_search_warnings:
                session.last_search_warnings.append(warning)
            return feed,actual
        if not entries:
            return feed,actual
        links=[]
        for entry in entries:
            label=_fold(_opds_text(entry,ATOM+'title'))
            identifier=_fold(_opds_text(entry,ATOM+'id'))
            for rel,mime,raw,_ in _opds_links(entry,actual):
                if 'atom+xml' not in mime:
                    continue
                href=_opds_source_link(session,actual,raw,'feed')
                if not href:continue
                if needle and field in {'author','subject','category','genre'} and (
                        _fold(needle) in label
                        or (field == 'author'
                            and _opds_author_navigation_match(needle, _opds_text(entry, ATOM+'title') or ''))):
                    # The inverse-order check only prioritizes a proven
                    # server-issued navigation link; after following it,
                    # book rows STILL require explicit Atom author names.
                    rank=0
                elif any(token in label or token in identifier for token in wanted):
                    rank=1
                elif any(token in label or token in identifier for token in ('book','livre','ebook')):
                    rank=2
                else:
                    rank=3
                links.append((rank,href))
        if not links:
            # Unexpected non-empty navigation is never an empty hit list.
            raise ProviderCapabilityError('Ebooks libres et gratuits : browse_route_unavailable')
        links.sort()
        if links[0][0]>2:
            raise ProviderCapabilityError('Ebooks libres et gratuits : browse_route_unavailable')
        # Distinct same-priority OPDS navigation feeds may each contain
        # books for the requested field. Following the first is a bounded
        # choice, not proof that the entire server catalogue was searched.
        # Never request the other feeds implicitly or claim a corpus zero.
        priority = links[0][0]
        alternatives = {href for rank, href in links if rank == priority}
        warning = (
            'partial_results : plusieurs branches OPDS possibles ; '
            'une seule interrogée'
        )
        if len(alternatives) > 1 and warning not in session.last_search_warnings:
            session.last_search_warnings.append(warning)
        url=links[0][1]
    raise ProviderError('Ebooks libres et gratuits : navigation_limit')


def _opds_search(session,query,scope='all',page=0,field='all'):
    session.last_search_warnings=[]
    if not isinstance(field,str) or field.strip().lower() not in DESCRIPTOR.search_capabilities.fields:
        raise ProviderCapabilityError('Ebooks libres et gratuits : unsupported_field')
    if not isinstance(scope,str) or scope.strip().lower() not in DESCRIPTOR.search_capabilities.scopes:
        raise ProviderCapabilityError('Ebooks libres et gratuits : unsupported_scope')
    if not session.connected:raise ProviderError('Ebooks libres et gratuits : connecter le catalogue')
    if type(page) is not int or not 0<=page<MAX_REQUESTED_PAGES:
        raise ProviderCapabilityError('Ebooks libres et gratuits : invalid_page')
    query = _validated_public_query(query)
    field=field.strip().lower()
    needle=query.strip()
    if field not in {'all','title'} and not needle:
        return []
    root,root_url=_opds_fetch(session,session.opds_feed_url)
    if root.tag!=ATOM+'feed':
        raise (_unverified_html_opds_response() if _is_html_document(root)
               else _malformed_opds_protocol()) from None
    if needle:
        url=_opds_search_url(session,root,root_url,needle)
    else:
        url=_opds_browse_url(session,root,root_url)
    if field!='all':
        session.last_search_warnings.append(
            'partial_results : filtre '+field+' appliqué uniquement à la page OPDS reçue'
        )
    seen=set()
    for index in range(page+1):
        if url in seen:raise ProviderError('Ebooks libres et gratuits : invalid_opds_pagination')
        seen.add(url)
        feed,current=_opds_load_acquisition(session,url,field,needle)
        if index==page:
            rows=_opds_items(session,feed,current)
            if field=='title':
                rows=[row for row in rows if _fold(needle) in _fold(row.title)]
            elif field=='author':
                # Compare within EACH declared Atom author. A joined display
                # string may mix surname+forename in COPS order, or two
                # distinct people into a fictitious author identity.
                rows=[row for row in rows if _opds_author_match(
                    needle,row.metadata.get('opds_author_names',())
                )]
            elif field in {'subject','category','genre'}:
                def tokens(row):
                    data=row.metadata
                    if field=='category':return data.get('categories',[])
                    # #1917: a category is not proof of a published genre.
                    # Only the broader subject facet may inspect both.
                    if field=='genre':return data.get('genres',[])
                    return data.get('categories',[])+data.get('genres',[])
                rows=[row for row in rows if any(_fold(needle) in _fold(x) for x in tokens(row))]
            if len(rows) > session.page_size:
                # The next API page follows the server's next feed, not the
                # omitted tail of this feed. Never imply exhaustive coverage
                # or fetch more pages/downloads implicitly to fill the gap.
                session.last_search_warnings.append(
                    'partial_results : page OPDS tronquée par la limite locale ; '
                    'des notices de cette page ne sont pas affichées'
                )
            return rows[:session.page_size]
        url,_,_=_opds_find_link(feed,current,'next')
        if not url:
            return []
        url=_safe_opds_link(session.opds_feed_url,url,'feed')
        if not url:raise ProviderError('Ebooks libres et gratuits : invalid_opds_link')
    return []
