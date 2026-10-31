from __future__ import annotations
import hashlib, html, re
from unicodedata import category
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT
from cifereca.plugin_api import Account, ItemDetail, SearchResult, ProviderCapabilities, ProviderSearchCapabilities, ProviderCapabilityError, ProviderDescriptor, ProviderError, ProviderPlugin, ProviderSession, TemporaryUnavailable, UnsupportedOperation, Conflict, Cancelled
BASE='https://www.baen.com/'; FREE=BASE+'allbooks/category/index/id/2012'
MAX_REQUESTED_PAGES = 101  # Pages 0..100, never a silently clamped page.
UNVERIFIED_SEARCH_WARNING = (
    'partial_results : Baen : recherche et pagination amont non qualifiées'
)


def _trusted_baen_url(value):
    """Baen page and EPUB URLs cannot be supplied by arbitrary HTML hosts."""
    if (type(value) is not str or not value or len(value) > 4096
            or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme == 'https'
                and parts.hostname == 'www.baen.com'
                and parts.port in (None, 443)
                and not parts.username and not parts.password
                and not parts.fragment)
    except ValueError:
        return False


def _listing_product_href(href, attrs, page_html):
    """Select only a plausible product URL; never a category or account link.

    The former substring check accepted any URI merely containing /allbooks/,
    including unrelated query parameters and category navigation. Legacy
    synthetic product paths keep working only as one clean /allbooks/<slug>.
    Canonical root .html products still require verified listing/title context.
    """
    try:
        parsed = urlsplit(urljoin(BASE, href))
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            return False
        path = parsed.path
        legacy = re.fullmatch(r'/allbooks/([A-Za-z0-9][A-Za-z0-9._-]*)', path)
        if legacy is not None:
            return legacy.group(1).casefold() not in {
                'category', 'catalog', 'search', 'account', 'login',
                'checkout', 'cart', 'index', 'page', 'filter',
            }
    except ValueError:
        return False

    classes = re.search(r'(?:^|\s)class\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
    if classes is None or 'product-item-link' not in classes.group(1).split():
        return False
    if not re.fullmatch(r'/[A-Za-z0-9][A-Za-z0-9_-]*\.html', path):
        return False
    return bool(re.search(r'\bFree\s+Library\b', page_html, re.I)
                and re.search(r'\bShowing\s+\d+\s*-\s*\d+\s+of\s+\d+\b',
                              page_html, re.I))

def _baen_explicit_download_offer(html_text, detail_url):
    """Find a trusted download button and whether its EPUB format is proven.

    A generic "Download" button remains eligible for the existing acquisition
    flow, but does not attest EPUB availability in the read-only holdings view.
    A PDF/MOBI link is evidence of a different format, not an EPUB offer.
    """
    links = re.findall(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        html_text, re.I | re.S,
    )
    generic_download = None
    for href, label in links:
        text = ' '.join(re.sub('<[^>]+>', ' ', html.unescape(label)).split()).casefold()
        candidate = urljoin(detail_url, href)
        if not _trusted_baen_url(candidate):
            continue
        path = urlsplit(candidate).path.casefold()
        if path.endswith(('.pdf', '.mobi', '.azw', '.azw3')):
            continue
        epub_attested = 'epub' in text or path.endswith('.epub')
        if epub_attested:
            # Prefer the explicit EPUB offer even when a generic "Download"
            # button appears earlier in the same product page.
            return candidate, True
        if 'download' in text and generic_download is None:
            generic_download = candidate
    return generic_download, False


def _public_epub_filename(title, detail_url):
    """Bounded per-product basename, never a raw provider record_id.

    Multiple Baen products can share a title. Only the previously validated
    public detail URL differentiates local destinations, without disclosing
    its query string or letting an arbitrary ID control filesystem paths.
    """
    stem = re.sub(r'[^\w .-]', '_', title if type(title) is str else '')
    stem = stem.strip(' .')[:48].rstrip(' .')
    if not any(char.isalnum() for char in stem):
        stem = 'Livre'
    digest = hashlib.sha256(detail_url.encode('utf-8')).hexdigest()[:16]
    return f'{stem}-{digest}.epub'


def _reject_redirect(response):
    # httpx.raise_for_status() is not a safe substitute for disabling
    # redirects: refuse to follow a product/search redirect to another host.
    if 300 <= getattr(response, 'status_code', 200) < 400:
        raise ProviderError('Baen : public_redirect_unverified')

# The transport can include an URL, filename, raw MIME or exception chain in
# its error. Only allowlisted transport reasons leave this plugin boundary.
_BAEN_DOWNLOAD_STATUSES = {
    'auth_required': 'download_auth_required',
    'access_denied': 'download_access_denied',
    'not_found': 'download_not_found',
    'rate_limited': 'download_rate_limited',
    'service_unavailable': 'download_service_unavailable',
}
_BAEN_DOWNLOAD_REJECTIONS = {
    'transport.http: réponse HTML refusée': 'download_invalid_content',
    'transport.http: réponse vide': 'download_empty_response',
    'transport.http: artefact protégé refusé': 'download_protected',
    'transport.http: fichier trop volumineux': 'download_too_large',
    'transport.http: redirection hors origine refusée': 'download_redirect_denied',
    'transport.http: redirection HTTPS vers HTTP refusée': 'download_redirect_denied',
    'transport.http: destination de redirection invalide': 'download_redirect_denied',
}


def _baen_download_failure(error):
    """Map only built-in broker exception shapes; never reflect raw strings."""
    raw = (error.args[0] if len(error.args) == 1 and
           type(error.args[0]) is str else '')
    if type(error) is TemporaryUnavailable:
        prefix = 'transport.http: '
        code = raw[len(prefix):] if raw.startswith(prefix) else ''
        result = _BAEN_DOWNLOAD_STATUSES.get(code)
        if result is None and code in {
            'redirection sans destination', 'trop de redirections',
            'redirections non résolues',
        }:
            result = 'download_redirect_limit'
        return ProviderError('Baen : ' + (result or 'download_service_unavailable'))
    if type(error) is UnsupportedOperation:
        result = _BAEN_DOWNLOAD_REJECTIONS.get(raw)
        if result is None and raw.startswith('transport.http: content-type refusé ('):
            result = 'download_invalid_content'
        elif result is None and raw.startswith('transport.http: artefact invalide ('):
            result = 'download_invalid_epub'
        return ProviderError('Baen : ' + (result or 'download_rejected'))
    if type(error) is Conflict:
        return ProviderError('Baen : download_destination_conflict')
    return ProviderError('Baen : téléchargement inaccessible ou EPUB invalide')


DESCRIPTOR=ProviderDescriptor(id='baen',display_name='Baen',description='Baen Free Library et catalogue de livres électroniques.',state='ready',capabilities=ProviderCapabilities(search=True,holdings=True,direct_download=True),search_capabilities=ProviderSearchCapabilities(fields=('all',),scopes=('all','digital'),pagination='page',page_size=50,max_pages=MAX_REQUESTED_PAGES,notes=('Seul le champ général est exposé ; le filtrage serveur de la requête reste non qualifié. Aucun champ spécialisé attesté.','Numéros de page transmis sans preuve de pagination effective ; réponses partielles, y compris une page vide.',)),homepage=BASE,notes=('Couverture de recherche publique non qualifiée ; achats et bibliothèque privée hors périmètre.',))
class BaenSession(ProviderSession):
    def __init__(self,*,config,diagnostics,transport=None,**kw):
        super().__init__(descriptor=DESCRIPTOR,**kw); self.transport=transport; self.client=HTTPX_TRANSPORT.client(producer="provider.baen",timeout=float(self.options.get('timeout_seconds',30)),follow_redirects=False,trust_env=False,headers={'Accept-Language':'en-US,en;q=0.8'}); self.maximum=int(self.options.get('max_download_bytes',100*1024*1024)); self.page_size=50
    def connect(self):
        self.connected=True; self._accounts=[Account(display_name='Baen Free Library',unique_id='baen-public',provider_id='baen',credential_id=self.credential_id,provider_label=self.descriptor.display_name,is_primary=True)]; return self.accounts
    def close(self): self.client.close();self.connected=False;self._accounts=[]
    @staticmethod
    def _search_params(query, page, page_size=50):
        params={'page':page+1,'pageSize':int(page_size),'hideBundles':0,'hideEarcs':0}
        query=' '.join(str(query or '').split())
        if query: params['q']=query
        return params
    @staticmethod
    def _detail_metadata(text):
        title_match=re.search(r'<title[^>]*>\s*(.*?)\s+by\s+(.*?)\s+-\s+WebScription Ebook\s*</title>',str(text or ''),re.I|re.S)
        author=None
        if title_match:
            author=' '.join(re.sub('<[^>]+>',' ',html.unescape(title_match.group(2))).split()) or None
        plain=' '.join(re.sub('<[^>]+>',' ',html.unescape(str(text or ''))).split())
        published=None
        match=re.search(r'\bPublished:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})\b',plain,re.I)
        if match: published=match.group(1)
        isbns=[]
        for value in re.findall(r'\bISBN:\s*([0-9Xx-]{10,17})\b',plain,re.I):
            if value not in isbns: isbns.append(value)
        return {'author':author,'published_at':published,'isbns':isbns}

    @classmethod
    def _enrich_item_from_detail(cls,item,text):
        detail=cls._detail_metadata(text)
        if detail['author'] and not getattr(item,'author',None): item.author=detail['author']
        if detail['published_at'] and not getattr(item,'published_at',None): item.published_at=detail['published_at']
        metadata=getattr(item,'metadata',None)
        if not isinstance(metadata,dict):
            metadata={}
            item.metadata=metadata
        if detail['isbns'] and not metadata.get('isbns'): metadata['isbns']=list(detail['isbns'])
        if detail['author'] or detail['published_at'] or detail['isbns']:
            metadata.setdefault('metadata_evidence','baen.public_product_page')
        return item

    def search(self,query,scope='all',page=0,field='all'):
        self.last_search_warnings = []
        # A direct caller must not mistake a general search for an exact
        # author/ISBN query. The Core broker also checks these declarations.
        if type(field) is not str or field.strip().casefold() != 'all':
            raise ProviderCapabilityError('Baen : unsupported_field')
        if type(scope) is not str or scope.strip().casefold() not in ('all','digital'):
            raise ProviderCapabilityError('Baen : unsupported_scope')
        if type(page) is not int or not 0 <= page < MAX_REQUESTED_PAGES:
            raise ProviderCapabilityError('Baen : invalid_page')
        if query is None:
            query = ''
        if (type(query) is not str or len(query) > 2048
                or any(category(ch) in {'Cc', 'Cf', 'Cs'} for ch in query)):
            raise ProviderCapabilityError('Baen : invalid_query')
        if not self.connected: raise ProviderError('Baen : connecter le catalogue')
        try:
            r=self.client.get(FREE,params=self._search_params(query,page,self.page_size))
            _reject_redirect(r)
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Expose a stable public code, never upstream response bodies or
            # bearer-like query parameters which httpx formats in exceptions.
            status=exc.response.status_code
            if status == 401:
                code='auth_required'
            elif status == 403:
                code='access_denied'
            elif status == 429:
                code='rate_limited'
            else:
                code='service_unavailable'
            failure = ProviderError(f'Baen : {code}')
            if status == 401:
                failure.code = 'search_authentication_required'
            elif status == 403:
                failure.code = 'search_access_denied'
            elif status in {408, 429} or 500 <= status <= 599:
                failure.code = 'search_temporarily_unavailable'
                failure.retryable = False
            # The transport exception can contain a signed URL, private
            # request parameters or a remote body; keep public diagnostics
            # closed even if a caller inspects __cause__.
            raise failure from None
        except httpx.HTTPError as exc:
            failure = ProviderError('Baen : service_unavailable')
            if isinstance(exc, (httpx.TransportError, httpx.DecodingError)):
                failure.code = 'search_temporarily_unavailable'
                failure.retryable = False
            # Redirect loops are not evidence of a technical outage.
            raise failure from None
        out=[]; seen=set()
        for attrs,body in re.findall(r'<a\b([^>]*)>(.*?)</a\s*>',r.text,re.I|re.S):
            href_match = re.search(r'(?:^|\s)href\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
            if href_match is None: continue
            href = html.unescape(href_match.group(1))
            plain=' '.join(re.sub('<[^>]+>',' ',html.unescape(body)).split())
            if not plain or plain.lower() in {'more...','buy'}: continue
            detail=urljoin(BASE,href)
            if not _trusted_baen_url(detail): continue
            if not _listing_product_href(href, attrs, r.text): continue
            if detail in seen: continue
            seen.add(detail); rid=hashlib.sha256(detail.encode()).hexdigest()[:24]
            out.append(SearchResult(id='baen:'+rid,record_id=rid,docbase='BAEN',medium='digital',title=plain,author=None,type='Livre numérique',format=None,publisher='Baen',published_at=None,description=None,thumbnail_url=None,friendly_url=detail,scenario='BAEN_FREE_LIBRARY',source_provider='baen',source_credential=self.credential_id,download_url=None,content_type='ebook',source_collection='public',owned=False,downloadable=False))
        # Neither cards nor zero markers establish that this legacy route
        # applied q/page/pageSize. Keep every successful response partial
        # until the server search and pagination contract is qualified.
        self.last_search_warnings.append(UNVERIFIED_SEARCH_WARNING)
        if not out:
            # Unknown markup additionally cannot attest even an empty page.
            if not (
                re.search(r'\bShowing\s+0\s*-\s*0\s+of\s+0\b', r.text, re.I)
                and re.search(r'\bNo\s+results\b', r.text, re.I)
            ):
                self.last_search_warnings.append(
                    'partial_results : Baen : absence de résultats non attestée'
                )
        return out[:self.page_size]
    def holdings(self,item,search_terms=''):
        availability = 'unknown'
        label = 'Baen : disponibilité EPUB non vérifiée'
        if item.source_provider==self.provider_id and item.friendly_url:
            if not _trusted_baen_url(item.friendly_url):
                raise ProviderCapabilityError('Baen : untrusted_url')
            try:
                response=self.client.get(item.friendly_url)
                _reject_redirect(response)
                response.raise_for_status()
                self._enrich_item_from_detail(item,response.text)
                offer, epub_attested = _baen_explicit_download_offer(
                    response.text, item.friendly_url,
                )
                if epub_attested:
                    availability = 'available'
                    label = 'Lien EPUB public affiché ; téléchargement non vérifié'
                elif offer:
                    label = 'Lien de téléchargement public présent ; format EPUB inconnu'
                else:
                    label = 'Aucun lien EPUB public explicite sur la fiche'
            except httpx.HTTPError:
                label = 'Fiche Baen inaccessible ; disponibilité EPUB inconnue'
        return ItemDetail(record_id=item.record_id,docbase='BAEN',title=item.title,author=item.author,type=item.type,year=item.published_at,thumbnail_url=item.thumbnail_url,availability_status=availability,availability_label=label,holdings=[],source_provider='baen',source_credential=self.credential_id)

    def download_direct(self,item,target_dir):
        if item.source_provider!=self.provider_id or not item.friendly_url: raise ProviderError('Baen : fiche inconnue')
        if not _trusted_baen_url(item.friendly_url):
            raise ProviderCapabilityError('Baen : untrusted_url')
        try:
            r=self.client.get(item.friendly_url)
            _reject_redirect(r)
            r.raise_for_status()
        except httpx.HTTPError:
            # Do not attach a private request URL as a public exception cause.
            failed = True
        else:
            failed = False
        if failed:
            raise ProviderError('Baen : fiche inaccessible')
        self._enrich_item_from_detail(item,r.text)
        # A PDF/MOBI button cannot qualify as a public EPUB offer.
        candidate, _epub_attested = _baen_explicit_download_offer(
            r.text, item.friendly_url,
        )
        if not candidate: raise ProviderError('Baen : aucun lien EPUB public explicite sur cette fiche')
        if self.transport is None: raise ProviderError('Baen : transport.http indisponible')
        d=Path(target_dir);d.mkdir(parents=True,exist_ok=True)
        dest=d/_public_epub_filename(item.title,item.friendly_url)
        source={
            'url':candidate,
            'headers':{'Accept':'application/epub+zip, application/zip;q=0.8, application/octet-stream;q=0.5, */*;q=0.2','Referer':item.friendly_url},
            'max_bytes':self.maximum,
            'allowed_content_types':('application/epub+zip','application/zip','application/octet-stream'),
            'expected_format':'epub',
            'classify_http_status':True,
            'overwrite':True,
        }
        failure = None
        try:
            dest = Path(self.transport.fetch(source, dest))
        except Cancelled:
            # A user cancellation is not a failed download, and must retain
            # its cooperative control-flow semantics.
            raise
        except Exception as exc:
            # Build the public, closed error inside the handler, but raise it
            # OUTSIDE the handler: no implicit __context__/__cause__ back to
            # a signed URL, filename or response from transport.http.
            failure = _baen_download_failure(exc)
        if failure is not None:
            raise failure
        return dest,item.friendly_url
class Plugin(ProviderPlugin):
    descriptor=DESCRIPTOR
    def __init__(self): self._sessions=[]; self.context=None; self.transport=None
    def activate(self,context):
        self.context=context; self.transport=context.service('transport.http')
        if self.transport is None: raise ProviderError('Baen : transport.http requis')
        return self
    def deactivate(self):
        for session in self._sessions:
            try: session.close()
            except Exception: pass
        self._sessions.clear(); self.transport=None; self.context=None
    def create_session(self,*,credential_id,label,credentials,options,config,storage,diagnostics):
        session=BaenSession(credential_id=credential_id,label=label,credentials=credentials,options=options,config=config,diagnostics=diagnostics,transport=self.transport); self._sessions.append(session); return session
PLUGIN=Plugin()
