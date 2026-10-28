"""Provider externe Numilog 0.1.79, bibliothèque privée, EPUB direct et ACSM/ADEPT."""
from __future__ import annotations
import hashlib
import ipaddress
import json
import logging
import os
import socket
from pathlib import Path
import tempfile
import threading
import unicodedata
import zipfile
from xml.etree import ElementTree as ET
from urllib.parse import urljoin, urlsplit

import httpx
from cifereca.plugin_api import (HTTPX_TRANSPORT, Account, CredentialField, ItemDetail, ProviderCapabilities,
    ProviderCapabilityError, ProviderConfigurationError, ProviderDescriptor, ProviderSearchCapabilities, ProviderError,
    ProviderPlugin, ProviderSession, SearchResult, inspect_artifact, publish_artifact_file)
from cifereca_numilog_html import (BASE, LIBRARY, ParseError, DeliverySelectionError,
    OwnedDelivery, parse_library, parse_library_page, delivery_url, public_book_url,
    html_response_kind, _attested_public_https)
from cifereca_numilog_filename import publish
from cifereca_numilog_delivery_trace import DeliveryTrace, destination_category, public_http_code

# A descendant of the Core logger shares its canonical journal handler.
_LOG = logging.getLogger("cifereca.plugin.provider.numilog")

VERSION = '0.1.88'
DESCRIPTOR = ProviderDescriptor(id='numilog',display_name='Numilog',
    description='Bibliothèque personnelle Numilog, EPUB direct et fulfillment ACSM via le Core.',state='ready',
    capabilities=ProviderCapabilities(authentication=True,multiple_credentials=True,
        search=True,holdings=True,personal_library=True,direct_download=True),
    search_capabilities=ProviderSearchCapabilities(
        fields=('all','title'), scopes=('all','digital','catalog'),
        pagination='page', page_size=50, max_pages=101,
        notes=(
            'scope=catalog : recherche anonyme brSimple, champ all seulement, pages de 20 observées dans la fixture ; aucun droit de possession.',
            'scopes all/digital : recherche historique limitée aux titres achetés ; title est un post-filtre local, pas un filtre serveur public.',
            'La recherche publique HTTP reste à qualifier en conditions réelles ; la fédération par défaut ne la déclenche pas encore.',
        ),
    ),
    credential_fields=(CredentialField('email','Adresse email Numilog'),CredentialField('password','Mot de passe Numilog',secret=True)),
    homepage=BASE,notes=('Recherche habituelle dans Mes achats ; scope=catalog active la recherche publique anonyme brSimple, sans acquisition.',
        'EPUB direct et Adobe ACSM via le broker public d’acquisition ; LCP, PDF et audio non qualifiés.'))

ALLOWED = {'www.numilog.com','services.numilog.com','www.edenlivres.fr','storage.googleapis.com'}
# Fulfillment ACSM observé : origine HTTPS exacte, jamais un wildcard Numilog.
ALLOWED_ACSM_ORIGINS = {('https', 'entrepot2r.numilog.com', 443)}
REDIRECTS = {301,302,303,307,308}
MAX_HTML = 4*1024*1024

def url_origin(value):
    p=urlsplit(value)
    # An explicit :0 is not an omitted port: it must never inherit 443/80.
    port=p.port if p.port is not None else (443 if p.scheme=='https' else 80 if p.scheme=='http' else None)
    return (p.scheme.casefold(),(p.hostname or '').casefold(),port)

def normalized_base_url(value):
    text=str(value or BASE).strip()
    try:
        p=urlsplit(text)
        host=(p.hostname or '').casefold()
        loopback = host == 'localhost'
        if host and not loopback:
            try: loopback=ipaddress.ip_address(host).is_loopback
            except ValueError: pass
        if (p.scheme=='https' or (p.scheme=='http' and loopback)) and host and not p.username and not p.password and not p.query and not p.fragment and p.path in {'','/'}:
            return f"{p.scheme}://{p.netloc}/"
    except ValueError:
        pass
    raise ProviderConfigurationError('Numilog : base_url invalide ; HTTPS requis, sauf loopback HTTP pour les tests locaux')

def _raw_network_url(value):
    """Reject raw controls before urllib may silently strip CR/LF/TAB.

    A signed ACSM URL must be sent exactly as received. Normalizing a
    malformed redirect could change its signature and obscure the origin of
    a download failure. Percent-encoded bytes remain untouched.
    """
    return (
        type(value) is str and bool(value)
        and not any(
            char.isspace() or unicodedata.category(char) in {'Cc', 'Cf', 'Cs'}
            for char in value
        )
    )


def checked_url(value, allowed_origins=None):
    if not _raw_network_url(value):
        raise ProviderError('Numilog : destination réseau inattendue ; téléchargement arrêté')
    allowed=set(allowed_origins if allowed_origins is not None else ({url_origin('https://'+host+'/') for host in ALLOWED} | ALLOWED_ACSM_ORIGINS))
    try:
        p = urlsplit(value)
        if p.scheme in {'http','https'} and url_origin(value) in allowed and not p.username and not p.password and not p.fragment:
            # A wildcard path under the ACSM fulfillment host is NOT an
            # authorization to query arbitrary private Numilog endpoints.
            if url_origin(value) in ALLOWED_ACSM_ORIGINS and (
                p.path != '/fulfillment/URLLink.acsm' or not p.query
            ):
                raise ProviderError('Numilog : destination réseau inattendue ; téléchargement arrêté')
            return value
    except ValueError:
        pass
    raise ProviderError('Numilog : destination réseau inattendue ; téléchargement arrêté')

def _checked_attested_network_url(value, allowed_origins):
    """Signed delivery is authorized by the owned Links row, not a host list.

    Public HTTPS and DNS are checked before every GET outside the existing
    trusted origins. The check never publishes a hostname or signed query.
    """
    if not _attested_public_https(value):
        raise ProviderError('Numilog : destination réseau de livraison refusée')
    host = urlsplit(value).hostname
    if url_origin(value) not in allowed_origins:
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if (not addresses or any(
                not ipaddress.ip_address(info[4][0]).is_global
                for info in addresses
            )):
                raise ValueError('not_public')
        except (OSError, ValueError, IndexError, TypeError):
            raise ProviderError(
                'Numilog : destination de livraison non publique'
            ) from None
    return value


def checked_redirect(base_url, location, allowed_origins):
    """Validate the unmodified Location before urljoin can normalize it."""
    if not _raw_network_url(location):
        raise ProviderError('Numilog : redirection réseau inattendue ; téléchargement arrêté')
    return checked_url(urljoin(base_url, location), allowed_origins)


def validate_epub(path):
    inspected = inspect_artifact(Path(path), 'epub', strict_structure=True)
    if not inspected.valid:
        raise ProviderError('Numilog : le fichier reçu n’est pas un EPUB valide')
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if 'META-INF/license.lcpl' in names or 'META-INF/rights.xml' in names:
                raise ProviderCapabilityError('Numilog : EPUB protégé Adobe/LCP non pris en charge dans cette version')
            if 'META-INF/encryption.xml' in names:
                encryption = ET.fromstring(z.read('META-INF/encryption.xml'))
                methods = encryption.findall('.//{*}EncryptionMethod')
                fonts = {'http://www.idpf.org/2008/embedding','http://ns.adobe.com/pdf/enc#RC'}
                if not methods or any(m.get('Algorithm') not in fonts for m in methods):
                    raise ProviderCapabilityError('Numilog : chiffrement EPUB non pris en charge dans cette version')
    except ProviderCapabilityError:
        raise
    except (KeyError,OSError,zipfile.BadZipFile,ET.ParseError,RuntimeError,NotImplementedError):
        raise ProviderError('Numilog : le fichier reçu n’est pas un EPUB valide') from None

def _numilog_card_offer_id(credential_id, account_id, right_id, evidence):
    """Opaque exact scoped offer from a real signed MesLivres owned card.

    ProductId and IndexExemplaire come from the DOM-bound provider action.
    Neither is equated to lid. Signed URLs are never exported or hashed:
    refreshed signatures may rotate while the same card remains offered.
    """
    if (type(evidence) is not OwnedDelivery
            or evidence.delivery_kind not in {'acsm', 'epub_direct'}
            or not _attested_public_https(evidence.url)):
        return ''
    coords = (credential_id, account_id, right_id,
              evidence.product_id, evidence.copy_index, evidence.delivery_kind)
    if (not all(type(x) is str and 0 < len(x) <= 256 for x in coords)
            or not all(x.isascii() and x.isdecimal() and len(x) <= 16
                       for x in (evidence.product_id, evidence.copy_index))):
        return ''
    return 'numilog.card:' + hashlib.sha256(
        '\x00'.join(coords).encode('utf-8')
    ).hexdigest()


class NumilogSession(ProviderSession):
    def __init__(self, *, config, diagnostics, acquisition_broker=None, identity_broker=None, **kwargs):
        super().__init__(descriptor=DESCRIPTOR,**kwargs)
        self.diagnostics = diagnostics
        self.acquisition_broker = acquisition_broker
        self.identity_broker = identity_broker
        self.scope = hashlib.sha256(self.credential_id.encode()).hexdigest()[:20]
        self.base_url = normalized_base_url(self.options.get('base_url') or BASE)
        self.library_url = urljoin(self.base_url,'Pages/Compte/MesLivres.aspx')
        self.allowed_origins = {url_origin('https://'+host+'/') for host in ALLOWED} | ALLOWED_ACSM_ORIGINS
        self.allowed_origins.add(url_origin(self.base_url))
        timeout = float(self.options.get('timeout_seconds',30))
        self.maximum = int(self.options.get('max_download_bytes',100*1024*1024))
        if not 1 <= timeout <= 120 or not 1024 <= self.maximum <= 512*1024*1024:
            raise ProviderConfigurationError('Numilog : limites réseau invalides')
        self.client = HTTPX_TRANSPORT.client(producer="provider.numilog", timeout=timeout,follow_redirects=False,trust_env=False,
            headers={'User-Agent':'Cifereca-Numilog/'+VERSION,'Accept-Language':'fr-FR,fr;q=0.9'})
        self.lock = threading.RLock()
        # Only public anonymous responses are cached; owned rights are always
        # reloaded from the authenticated inventory on each explicit request.
        from cifereca_numilog_public_cache import PublicCatalogueCache
        self._owned_public_cache = PublicCatalogueCache()

    def _diag(self,operation,**detail):
        if self.diagnostics is None:
            return
        try:
            self.diagnostics.add('NUMILOG',operation,'ok',detail=detail,level='detailed')
        except Exception:
            # Diagnostics are auxiliary: a broken sink must never alter
            # provider/network/download semantics.
            return

    def _session_event(self, reason):
        # These literal codes are the complete support payload. Never log
        # provider exceptions, URLs, credentials, account IDs or HTML here.
        allowed = {
            'connect_started', 'connect_succeeded', 'credentials_missing',
            'connect_failed', 'session_closed', 'not_connected',
            'library_redirect', 'library_invalid', 'library_http_error',
            'delivery_login_form_detected',
        }
        reason = reason if type(reason) is str and reason in allowed else 'unknown'
        connected = bool(self.connected)
        self._diag('session_state', reason=reason, connected=connected)
        try:
            level = logging.INFO if reason in {
                'connect_started', 'connect_succeeded', 'session_closed',
            } else logging.WARNING
            _LOG.log(level, 'TRACE numilog session connected=%s reason=%s', connected, reason)
        except Exception:
            # An optional log handler cannot change authentication semantics.
            pass

    @staticmethod
    def _status(response):
        if response.status_code in {401,403}:
            raise ProviderError('Numilog : accès refusé ou contrôle du navigateur ; session HTTP à qualifier')
        if response.status_code == 429:
            raise ProviderError('Numilog : trop de requêtes ; réessayer plus tard')
        if response.status_code == 410:
            raise ProviderError('Numilog : lien de livraison expiré ; relancer le téléchargement')
        if response.status_code != 200:
            raise ProviderError('Numilog : réponse HTTP '+str(response.status_code))

    @staticmethod
    def _small(response):
        parts, count = [], 0
        for chunk in response.iter_bytes():
            count += len(chunk)
            if count > MAX_HTML:
                raise ProviderError('Numilog : réponse textuelle trop volumineuse')
            parts.append(chunk)
        return b''.join(parts)

    def _page(self,url,*,payload=None,private_library_origin=None):
        # A private MesLivres request is not a generic allowed download.
        # Other Numilog/partner origins are valid for DRM fulfillment only;
        # they must never become evidence of owned purchases. In particular,
        # reject an external 302 BEFORE sending cookies or any second GET.
        method = 'POST' if payload is not None else 'GET'
        for _ in range(10):
            checked_url(url,self.allowed_origins)
            if (private_library_origin is not None
                    and url_origin(url) != private_library_origin):
                raise ProviderError('Numilog : origine de bibliothèque inattendue ; accès arrêté')
            headers = {'Referer':self.base_url}
            if payload is not None:
                headers.update({'Origin':self.base_url.rstrip('/'),'X-Requested-With':'XMLHttpRequest','Accept':'application/json'})
            with self.client.stream(method,url,json=payload,headers=headers) as r:
                self._diag('HTTP',method=method,origin_kind=destination_category(url)[0],status=r.status_code)
                if r.status_code in REDIRECTS:
                    if payload is not None:
                        raise ProviderError('Numilog : redirection inattendue pendant la connexion')
                    target = checked_redirect(
                        str(r.url), r.headers.get('location',''), self.allowed_origins,
                    )
                    if (private_library_origin is not None
                            and url_origin(target) != private_library_origin):
                        # Do not query an allowed CDN, partner or ACSM host
                        # under a forged /MesLivres.aspx path and then parse
                        # its HTML as the owner's authenticated inventory.
                        raise ProviderError('Numilog : origine de bibliothèque inattendue ; accès arrêté')
                    url = target
                    continue
                self._status(r)
                return str(r.url),self._small(r).decode('utf-8-sig',errors='replace')
        raise ProviderError('Numilog : trop de redirections')

    def connect(self):
        with self.lock:
            self._owned_public_cache.clear()
            self.connected = False
            self._session_event('connect_started')
            self._accounts = []
            self.client.cookies.clear()
            email = str(self.credentials.get('email') or '').strip()
            password = str(self.credentials.get('password') or '')
            if not email or not password:
                self._session_event('credentials_missing')
                raise ProviderConfigurationError('Numilog : email et mot de passe requis')
            try:
                self._page(self.base_url)
                _, text = self._page(urljoin(self.base_url,'Login.Aspx/AuthUser'),payload={'frmLogin':email,'frmPassword':password})
                result = json.loads(text).get('d')
                if isinstance(result,str):
                    result = json.loads(result)
                if not isinstance(result,dict) or result.get('AuthSuccess') is not True:
                    raise ProviderError('Numilog : connexion refusée')
                url, text = self._page(
                    self.library_url, private_library_origin=url_origin(self.library_url),
                )
                if (url_origin(url) != url_origin(self.library_url)
                        or urlsplit(url).path.lower() != '/pages/compte/meslivres.aspx'):
                    raise ProviderError('Numilog : session non reconnue par la bibliothèque')
                parse_library(text,url,cover_origins=self.allowed_origins)
            except (httpx.HTTPError,ValueError,TypeError,AttributeError):
                self.client.cookies.clear()
                self._session_event('connect_failed')
                raise ProviderError('Numilog : connexion impossible ou réponse inattendue') from None
            except ProviderError:
                self.client.cookies.clear()
                self._session_event('connect_failed')
                raise
            self.connected = True
            self._accounts = [Account(display_name=self.label or 'Compte Numilog',unique_id='numilog:'+self.scope,
                provider_id='numilog',credential_id=self.credential_id,provider_label='Numilog')]
            self._session_event('connect_succeeded')
            self._diag('connexion')
            return self.accounts

    def close(self):
        with self.lock:
            self._owned_public_cache.clear()
            self.client.cookies.clear()
            self.client.close()
            self.connected = False
            self._accounts = []
            self.credentials.clear()
            self._session_event('session_closed')

    def _rows(self, *, include_delivery=False, delivery_record_id=None,
              enrichment_record_id=None, rights_observation=False):
        # A signed library URL is only ever decoded for a single selected
        # account-scoped book at an explicit action; never during inventory,
        # search, or a broad scan of all owned purchases.
        if include_delivery and (
            type(delivery_record_id) is not str
            or not delivery_record_id.startswith('numilog:'+self.scope+':')
        ):
            raise ProviderError('Numilog : livre ciblé du compte requis pour la livraison')
        if enrichment_record_id is not None and (
            include_delivery or type(enrichment_record_id) is not str
            or not enrichment_record_id.startswith('numilog:'+self.scope+':')
        ):
            raise ProviderError('Numilog : enrichissement ciblé du compte requis')
        if type(rights_observation) is not bool or (
            rights_observation and (include_delivery or enrichment_record_id is not None)
        ):
            raise ProviderError('Numilog : inventaire de preuves incompatible')
        if not self.connected:
            self._session_event('not_connected')
            raise ProviderError('Numilog : connecter le compte avant cette opération')
        # Scope every authenticated MesLivres page before another private
        # GET, including the legacy library/search and targeted enrichment.
        # A legacy read with an unresolved broker may remain readable but
        # its *exact* candidate owners and live account instances must stay
        # unchanged throughout pagination. Only typed rights or explicitly
        # selected delivery requires one attested owner up front.
        strict_owner = rights_observation or (
            include_delivery and self.identity_broker is not None
        )
        protected_read = rights_observation or self.identity_broker is not None
        read_owner = self._inventory_owner_or_none() if protected_read else None
        read_account = (
            self._legacy_inventory_account_binding() if protected_read else None
        )

        def read_scope_current():
            return (not protected_read or (
                self.connected is True
                and self._inventory_owner_or_none() == read_owner
                and self._legacy_inventory_account_binding() == read_account
            ))

        if strict_owner and (
            type(read_owner) is not tuple or len(read_owner) != 3
            or read_owner[0] != 'broker'
            or type(read_owner[2]) is not tuple or len(read_owner[2]) != 1
            or not read_owner[2][0] or read_account is None
        ):
            raise ProviderCapabilityError(
                'Numilog : propriétaire unique non attesté pour les achats'
            )
        try:
            url = self.library_url
            rows = []
            seen_pages = set()
            seen_items = set()
            ambiguous_rights = set()
            for page_index in range(100):
                if not read_scope_current():
                    raise ProviderCapabilityError(
                        'Numilog : identité propriétaire modifiée ; ' + (
                            'observations arrêtées' if rights_observation
                            else 'livraison arrêtée' if include_delivery
                            else 'inventaire arrêté'
                        )
                    )
                if url in seen_pages:
                    raise ProviderError('Numilog : boucle de pagination de bibliothèque')
                seen_pages.add(url)
                final_url,text = self._page(
                    url, private_library_origin=url_origin(self.library_url),
                )
                if not read_scope_current():
                    raise ProviderCapabilityError(
                        'Numilog : identité propriétaire modifiée ; ' + (
                            'observations arrêtées' if rights_observation
                            else 'livraison arrêtée' if include_delivery
                            else 'inventaire arrêté'
                        )
                    )
                if (url_origin(final_url) != url_origin(self.library_url)
                        or urlsplit(final_url).path.lower() != '/pages/compte/meslivres.aspx'):
                    self.connected = False
                    self._session_event('library_redirect')
                    raise ProviderError('Numilog : session expirée ; reconnecter le compte')
                structural = {} if include_delivery else None
                page_rows,next_url = parse_library_page(
                    text,final_url,cover_origins=self.allowed_origins,
                    delivery_evidence=include_delivery,
                    delivery_diagnostics=structural,
                    delivery_selector=(
                        (lambda row: self._id(row) == delivery_record_id)
                        if include_delivery else None
                    ),
                    strict_public_enrichment_selector=(
                        (lambda row: self._id(row) == enrichment_record_id)
                        if enrichment_record_id is not None else None
                    ),
                )
                if include_delivery:
                    # Fixed enum + bounded numeric structure only; enough to
                    # distinguish an out-of-card JS object from a bound one,
                    # without capturing private account HTML or signed URLs.
                    self._diag('bibliothèque livraison',phase='owned_card',
                        page_kind='meslivres',page_index=page_index+1,
                        **structural)
                for row in page_rows:
                    key=(row['lid'],row['variant'])
                    if key in seen_items:
                        # Pagination may repeat a book or splice another
                        # purchase into the same lid/format. On an explicit
                        # signed delivery, duplicate identity is ambiguous,
                        # even if the display title appears to match.
                        if include_delivery and self._id(row) == delivery_record_id:
                            raise ProviderError(
                                'Numilog : livre dupliqué entre pages ; livraison non autorisée'
                            )
                        if (enrichment_record_id is not None
                                and self._id(row) == enrichment_record_id):
                            raise ProviderError(
                                'Numilog : édition dupliquée entre pages ; enrichissement arrêté'
                            )
                        if rights_observation:
                            # Duplicated owned-card coordinates across pages
                            # cannot attest one unambiguous purchase right.
                            # Retain the valid sibling editions but remove
                            # *all* occurrences of this ambiguous edition.
                            ambiguous_rights.add(key)
                        continue
                    seen_items.add(key)
                    rows.append(row)
                if not next_url:
                    if rights_observation and ambiguous_rights:
                        rows = [
                            row for row in rows
                            if (row['lid'], row['variant']) not in ambiguous_rights
                        ]
                    self._diag('pagination bibliothèque',pages=page_index+1,items=len(rows))
                    return rows
                url=next_url
            raise ProviderError('Numilog : bibliothèque trop paginée (>100 pages)')
        except ParseError as exc:
            self._session_event('library_invalid')
            raise ProviderError('Numilog : '+str(exc)) from None
        except httpx.HTTPError:
            self._session_event('library_http_error')
            raise ProviderError('Numilog : bibliothèque inaccessible ou structure modifiée') from None

    def _id(self,row):
        variant = hashlib.sha256(row['variant'].encode()).hexdigest()[:16]
        return 'numilog:'+self.scope+':'+row['lid']+':'+variant

    def _normalize(self,row):
        key = self._id(row)
        public_url = public_book_url(row.get('public_url'), self.library_url)
        return SearchResult(id=key,record_id=key,docbase='NUMILOG',medium='digital',title=row['title'],
            author=None,type='Livre numérique',format=None,publisher=None,published_at=None,description=None,
            thumbnail_url=row['cover'],friendly_url=public_url,scenario='NUMILOG_LIBRARY',source_provider='numilog',
            source_credential=self.credential_id,download_url=None,content_type='ebook',source_collection='library',
            owned=True,downloadable=True,metadata={'format_unverified':True,**({'public_url_evidence':'numilog.library_title_link'} if public_url else {})})

    def _legacy_inventory_account_binding(self):
        """Pin live Core Account *instances*, not only their owner labels.

        Legacy callers can still use library/search with zero active Account
        rows; the binding then records that exact empty live list. This does
        not grant rights: the typed #3252 snapshot has stricter admission.
        """
        accounts = self._accounts
        if type(accounts) not in (list, tuple):
            return None
        return (
            id(accounts),
            tuple(
                (id(account), getattr(account, "unique_id", None),
                 getattr(account, "identity_id", None),
                 getattr(account, "provider_id", None),
                 getattr(account, "credential_id", None))
                for account in accounts
            ),
            len(accounts),
        )

    def library(self):
        with self.lock:
            # The HTTP client is serialized with reconnect/close, but the Core
            # identity broker may reassign this account while MesLivres is
            # loading. Pin the owner across GET and normalization; never
            # publish a positive purchase observation under a new identity.
            # Standalone legacy sessions without a broker remain supported.
            owner = self._inventory_owner_or_none()
            account_binding = self._legacy_inventory_account_binding()
            if account_binding is None:
                raise ProviderCapabilityError(
                    'Numilog : session de bibliothèque indisponible'
                )
            rows = self._rows()
            normalized = [self._normalize(row) for row in rows]
            if (self._inventory_owner_or_none() != owner
                    or self._legacy_inventory_account_binding() != account_binding):
                raise ProviderCapabilityError(
                    'Numilog : identité propriétaire modifiée ; inventaire arrêté'
                )
            self._diag('bibliothèque',items=len(normalized))
            return normalized

    def rights_observation_snapshot(self, account):
        """Optional #3252 read: purchased Numilog cards, never acquisitions.

        The Core identity broker MUST resolve exactly one owner before and
        after the authenticated MesLivres GET. The legacy library() path may
        remain readable without this broker, but it cannot attest ownership
        for the versioned rights-observation API. One read, no delivery URL.
        """
        from datetime import datetime, timezone
        from cifereca.plugin_api import (
            Account, ProviderRightsScope, ProviderRightObservation,
            ProviderRightsSnapshot,
        )

        with self.lock:
            expected_account = 'numilog:' + self.scope
            if (
                type(account) is not Account or not self.connected
                or account.provider_id != 'numilog'
                or account.credential_id != self.credential_id
                or account.unique_id != expected_account
                or type(account.identity_id) is not str
                or not account.identity_id
                or len(self._accounts) != 1
                or self._accounts[0].unique_id != expected_account
                or self._accounts[0].provider_id != 'numilog'
                or self._accounts[0].credential_id != self.credential_id
            ):
                raise ProviderCapabilityError(
                    'Numilog : portée de preuves d’achats indisponible'
                )
            # A reconnected Account object may replace the previous live
            # session account with identical values while _rows() runs.
            # Pin its *instance* as well as the entire mutable scope.
            bound_account = self._accounts[0]
            bound_scope = (
                bound_account.unique_id, bound_account.identity_id,
                bound_account.provider_id, bound_account.credential_id,
            )
            # Account is a mutable Core model. Pin the caller's exact scope
            # before I/O; the broker owner alone cannot detect a caller rebind.
            caller_scope = (
                account.identity_id, account.provider_id,
                account.credential_id, account.unique_id,
            )
            owner = self._inventory_owner_or_none()
            if (
                type(owner) is not tuple or len(owner) != 3
                or owner[0] != 'broker'
                or owner[2] != (account.identity_id,)
            ):
                raise ProviderCapabilityError(
                    'Numilog : propriétaire unique non attesté pour les achats'
                )
            rows = self._rows(rights_observation=True)
            # Strict authenticated MesLivres: duplicates are ambiguous for
            # rights, unlike legacy library display deduplication.
            if (
                not self.connected
                or self._inventory_owner_or_none() != owner
                or len(self._accounts) != 1
                or self._accounts[0].unique_id != expected_account
                or self._accounts[0].provider_id != 'numilog'
                or self._accounts[0] is not bound_account
                or (bound_account.unique_id, bound_account.identity_id,
                    bound_account.provider_id, bound_account.credential_id) != bound_scope
                or self._accounts[0].credential_id != self.credential_id
                or (account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id) != caller_scope
            ):
                raise ProviderCapabilityError(
                    'Numilog : identité propriétaire modifiée ; observations arrêtées'
                )
            observed = datetime.now(timezone.utc).isoformat()
            observations = []
            for row in rows:
                # Parse results are provider-controlled. Never invent rights
                # from an absent/ambiguous variant or title: use only the
                # canonical MesLivres record ID and no public product match.
                try:
                    if type(row) is not dict:
                        raise ValueError
                    record = self._id(row)
                    if type(record) is not str or not record.startswith(
                        'numilog:' + self.scope + ':'
                    ):
                        raise ValueError
                    observations.append(ProviderRightObservation(
                        edition_id=record, right_id=record, kind='purchase',
                        state='active', evidence_kind='provider_response',
                        evidence_ref='numilog.meslivres',
                        observed_at=observed, delivery='unknown',
                    ))
                except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                    # A malformed authenticated card makes this read
                    # incomplete; never present its absence as a revocation.
                    continue
            if (self._inventory_owner_or_none() != owner or not self.connected
                    or len(self._accounts) != 1 or self._accounts[0] is not bound_account
                    or (bound_account.unique_id, bound_account.identity_id,
                        bound_account.provider_id, bound_account.credential_id) != bound_scope
                    or (account.identity_id, account.provider_id,
                        account.credential_id, account.unique_id) != caller_scope):
                raise ProviderCapabilityError(
                    'Numilog : identité propriétaire modifiée ; observations arrêtées'
                )
            # MesLivres pages are bounded and duplicate rows are discarded
            # by _rows(): do NOT claim an exhaustive entitlement snapshot.
            return ProviderRightsSnapshot(
                scope=ProviderRightsScope(
                    caller_scope[0], 'numilog', self.credential_id,
                    expected_account,
                ),
                rights=tuple(observations),
                status='partial', coverage='partial',
                pagination_complete=False, branch='meslivres',
                observed_at=observed, snapshot_ref='numilog.meslivres',
            )

    def rights_delivery_offer_snapshot(self, account, right_id):
        """Explicit, selected #3252 offer preflight; NEVER a delivery GET.

        This is an opt-in provider method for a future Core-admitted source,
        not called by ordinary library/rights sync. The initial rights read
        establishes one exact positive purchase before parsing any signed
        card; a second, targeted MesLivres read qualifies only this card's
        actual JS delivery action. No signed URL, receipt or file is returned.
        Core must independently persist consent and decide when to deliver.
        """
        from dataclasses import replace

        with self.lock:
            if (type(right_id) is not str or not right_id
                    or len(right_id) > 256
                    or not right_id.startswith('numilog:' + self.scope + ':')):
                raise ProviderCapabilityError('numilog_offer_unobserved')
            # Pin the broker + live Account *before* the first inventory
            # GET; a new valid owner after that read is not the same owner.
            live_list = self._accounts
            live = live_list[0] if len(live_list) == 1 else None
            owner = self._inventory_owner_or_none()
            scope = (
                account.identity_id, account.provider_id,
                account.credential_id, account.unique_id,
            ) if type(account) is Account else None
            bound = (
                live.unique_id, live.identity_id,
                live.provider_id, live.credential_id,
            ) if live is not None else None
            base = self.rights_observation_snapshot(account)
            original = base.exact(right_id, right_id, 'purchase')
            if original is None or not original.provider_attested:
                raise ProviderCapabilityError('numilog_offer_unobserved')
            if (owner is None or owner[0] != 'broker'
                    or owner[2] != (scope[0],)
                    or not self.connected or self._accounts is not live_list
                    or len(self._accounts) != 1 or self._accounts[0] is not live
                    or (live.unique_id, live.identity_id,
                        live.provider_id, live.credential_id) != bound
                    or (account.identity_id, account.provider_id,
                        account.credential_id, account.unique_id) != scope
                    or self._inventory_owner_or_none() != owner):
                raise ProviderCapabilityError('numilog_offer_owner_changed')
            rows = self._rows(
                include_delivery=True, delivery_record_id=right_id,
            )
            if (not self.connected or self._inventory_owner_or_none() != owner
                    or self._accounts is not live_list
                    or len(self._accounts) != 1 or self._accounts[0] is not live
                    or (live.unique_id, live.identity_id,
                        live.provider_id, live.credential_id) != bound
                    or (account.identity_id, account.provider_id,
                        account.credential_id, account.unique_id) != scope):
                raise ProviderCapabilityError('numilog_offer_owner_changed')

            selected = []
            for row in rows:
                try:
                    if type(row) is dict and self._id(row) == right_id:
                        selected.append(row)
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
            if len(selected) != 1:
                # A page failure or missing edition cannot revoke earlier
                # ownership, but it cannot claim any executable offer.
                return replace(base, rights=(original,))
            offer_id = _numilog_card_offer_id(
                account.credential_id, account.unique_id,
                right_id, selected[0].get('delivery'),
            )
            qualified = replace(
                original,
                delivery='available' if offer_id else 'unknown',
                delivery_offer_id=offer_id,
            )
            return replace(base, rights=(qualified,))

    def public_product_detail(self, product_url):
        """Explicit anonymous product GET, sharing the public session HTTP budget.

        Both public entrypoints previously skipped the budget used by owned
        enrichment. A caller could issue unlimited uncached GETs against the
        commercial site, or bypass a 429 cooldown by switching entrypoints.
        """
        from cifereca_numilog_public_client import fetch_public_detail
        # Qualify *before* the cache: a hit is not permission to accept an
        # untrusted URL that the HTTP transport would refuse on a miss.
        if (type(product_url) is not str
                or public_book_url(product_url, base_url='https://www.numilog.com/') != product_url
                or not product_url.startswith('https://www.numilog.com/')):
            raise ProviderCapabilityError('Numilog : invalid_public_product_url')
        return self._owned_public_cache.get(
            ('detail', product_url), lambda: fetch_public_detail(product_url),
        )

    def search_public(self, query, *, page=0, field='all'):
        """Explicit anonymous search with ONE shared public HTTP/cache budget.

        The brSimple HTML fixture proves text-only search. This path shares
        cache, cooldown and in-flight budget with explicit owned enrichment,
        but never uses account cookies, private library or download URLs.
        """
        from cifereca_numilog_public_client import fetch_public_search, public_search_url
        if type(field) is not str or field != 'all':
            raise ProviderCapabilityError('Numilog : unsupported_public_field')
        # Parse and bound the query/page before the shared cache is consulted.
        url = public_search_url(query, page)
        result = self._owned_public_cache.get(
            ('search', url),
            lambda: fetch_public_search(query, page=page, field=field),
        )
        return list(result.items)

    def search(self,query,scope='all',page=0,field='all'):
        # Do not silently coerce or clamp untrusted page selectors. Validate
        # before private library I/O or public HTTP, for every search scope.
        if type(page) is not int or not 0 <= page <= 100:
            raise ProviderCapabilityError('Numilog : invalid_page')
        if type(scope) is not str or scope not in {'catalog', 'all', 'digital'}:
            # #1917: an unsupported scope is not a legitimate empty result.
            # Reject before private library I/O and public HTTP, including
            # malformed unhashable inputs that must not raise TypeError.
            raise ProviderCapabilityError('Numilog : unsupported_scope')
        if scope == 'catalog':
            # Strictly separate public commercial results from purchased books.
            # No _rows(), authentication cookies, or ACSM acquisition here.
            return self.search_public(query, page=page, field=field)
        if type(field) is not str or field not in {'all','title','titre'}:
            raise ProviderCapabilityError('Numilog : unsupported_field')
        if type(query) is not str:
            raise ProviderCapabilityError('Numilog : invalid_query')
        def fold(value):
            return ''.join(c for c in unicodedata.normalize('NFKD',value.casefold()) if not unicodedata.combining(c))
        terms = fold(query).split()
        # The library() fence covers HTTP and its own normalization, not
        # subsequent title matching/pagination on the returned private rows.
        # Re-check the same owner around the whole search projection so a
        # Core identity rebind cannot publish stale purchased-book hits.
        with self.lock:
            owner = self._inventory_owner_or_none()
            account_binding = self._legacy_inventory_account_binding()
            matches = [row for row in self.library() if all(t in fold(row.title) for t in terms)]
            start = page*50
            result = matches[start:start+50]
            if (self._inventory_owner_or_none() != owner
                    or self._legacy_inventory_account_binding() != account_binding):
                raise ProviderCapabilityError(
                    'Numilog : identité propriétaire modifiée ; recherche arrêtée'
                )
            return result

    def enrich_owned_public_listing(self, item):
        """Explicit one-page public enrichment of an attested, fresh owned book.

        Not called by library(), search(), sync or holdings() automatically:
        querying the commercial site for a private purchase requires an
        explicit user-initiated enrichment action. No signed delivery URL is
        read, resolved, saved or sent. A failed lookup is not a false match.
        """
        with self.lock:
            # Validate a shallow copy: a stale or revoked caller must not be
            # promoted to owned merely by asking for enrichment.
            from dataclasses import replace
            self._canonical_item(replace(item))
            binding = self._enrichment_identity_snapshot()
            rows = self._rows(enrichment_record_id=item.record_id)
            self._ensure_enrichment_identity(binding)
            matches = [row for row in rows if self._id(row) == item.record_id]
            if len(matches) != 1:
                raise ProviderError(
                    'Numilog : édition achetée non attestée dans le compte courant'
                )
            # Use a fresh owned projection, never the caller's potentially
            # stale rights, metadata or public link.
            owned = self._normalize(matches[0])
            if owned.friendly_url is None:
                return owned
            from cifereca_numilog_public_client import fetch_public_search, public_search_url
            from cifereca_numilog_enrichment import merge_attested_public_listing
            # One bounded anonymous public query, no second-page scan, no
            # private cookie or automatic re-try. Only this anonymous response
            # is cacheable; the fresh purchased book above is NEVER cached.
            title = matches[0]['title']
            public_url = public_search_url(title, 0)
            public_page = self._owned_public_cache.get(
                ('search', public_url),
                lambda: fetch_public_search(title, page=0, field='all'),
            )
            self._ensure_enrichment_identity(binding)
            enriched, _status = merge_attested_public_listing(
                owned, public_page.items,
            )
            self._ensure_enrichment_identity(binding)
            return enriched

    def enrich_owned_public_detail(self, item):
        """Explicit attested purchase -> listing -> public Book enrichment.

        The owned inventory is refreshed by enrich_owned_public_listing before
        any public product GET. A missing/ambiguous listing is NOT permission
        to query an unverified product URL. Neither search nor sync calls this.
        """
        with self.lock:
            binding = self._enrichment_identity_snapshot()
            owned = self.enrich_owned_public_listing(item)
            self._ensure_enrichment_identity(binding)
            attestation = (owned.metadata or {}).get('public_enrichment')
            if (type(attestation) is not dict
                    or attestation.get('status') != 'attested_listing_match'):
                # No product detail was qualified on the FIRST listing page.
                return owned
            from cifereca_numilog_public_client import fetch_public_detail
            from cifereca_numilog_enrichment import merge_attested_owned_public_detail
            # Anonymous product metadata is reusable for 60 seconds, not the
            # account-scoped proof of ownership or private inventory.
            detail = self._owned_public_cache.get(
                ('detail', owned.friendly_url),
                lambda: fetch_public_detail(owned.friendly_url),
            )
            self._ensure_enrichment_identity(binding)
            result = merge_attested_owned_public_detail(owned, detail)
            self._ensure_enrichment_identity(binding)
            return result

    def enrich_owned_direct_public_detail(self, item):
        """Explicit owned library title link -> canonical public Book metadata.

        This path does NOT search brSimple for the owned title. An explicit
        product URL selected within a freshly parsed, authenticated Mes Livres
        title card is the association; a missing first-page search result
        does not invalidate that direct link. The public Book must still
        confirm exactly the same canonical URL and title before any field
        is projected, and it cannot prove the purchased EPUB/PDF variant.
        """
        with self.lock:
            from dataclasses import replace
            self._canonical_item(replace(item))
            binding = self._enrichment_identity_snapshot()
            rows = self._rows(enrichment_record_id=item.record_id)
            self._ensure_enrichment_identity(binding)
            matches = [row for row in rows if self._id(row) == item.record_id]
            if len(matches) != 1:
                raise ProviderError(
                    'Numilog : édition achetée non attestée dans le compte courant'
                )
            # Never accept caller-supplied public links, cached entitlement or
            # arbitrary private delivery references. Only fresh Mes Livres.
            row = matches[0]
            owned = self._normalize(row)
            if owned.friendly_url is None:
                recommendation = row.get('readzis_url')
                if type(recommendation) is not str:
                    return owned
                from cifereca_numilog_public_client import resolve_readzis_recommendation
                # This commercial share visit is allowed ONLY on explicit
                # enrichment. The cache key is a digest, never the link or its
                # private record ID; only a stripped canonical URL is cached.
                key = ('readzis', hashlib.sha256(recommendation.encode()).hexdigest())
                resolved = self._owned_public_cache.get(
                    key, lambda: resolve_readzis_recommendation(
                        recommendation, lid=row['lid'],
                    ),
                )
                self._ensure_enrichment_identity(binding)
                owned = replace(
                    owned, friendly_url=resolved,
                    metadata={**owned.metadata,
                              'public_url_evidence': 'numilog.library_readzis_redirect'},
                )
            from cifereca_numilog_enrichment import merge_attested_owned_direct_public_detail
            # Public product GET is separately budgeted: never follow the
            # tracking/trackid destination from the recommendation redirect.
            detail = self.public_product_detail(owned.friendly_url)
            self._ensure_enrichment_identity(binding)
            result = merge_attested_owned_direct_public_detail(owned, detail)
            self._ensure_enrichment_identity(binding)
            return result

    def holdings(self,item,search_terms=''):
        self._canonical_item(item)
        return ItemDetail(record_id=item.record_id,docbase='NUMILOG',title=item.title,author=item.author,
            type=item.type,year=None,thumbnail_url=item.thumbnail_url,availability_status='owned',
            availability_label='Dans votre bibliothèque Numilog',holdings=[],source_provider='numilog',source_credential=self.credential_id)

    def _canonical_item(self,item):
        """Répare uniquement les références v1 dont la connexion a été omise.

        Le record_id porte le scope stable du compte ; l'étiquette legacy n'est
        pas une preuve d'appartenance à un autre compte. Le téléchargement doit
        toujours retrouver ce record_id dans la bibliothèque fraîche.
        """
        prefix = 'numilog:'+self.scope+':'
        if item.source_provider != 'numilog' or not str(item.record_id).startswith(prefix):
            raise ProviderError('Numilog : livre d’une autre connexion ; relancer la recherche en ligne depuis le compte propriétaire')
        if item.source_credential not in {self.credential_id,None,'','legacy','catalog'}:
            raise ProviderError('Numilog : identifiant de connexion contradictoire ; relancer la recherche en ligne')
        if item.source_credential != self.credential_id:
            item.source_credential = self.credential_id
            self._diag('référence catalogue réparée')
        item.owned = True
        item.downloadable = True
        item.source_collection = 'library'
        # Re-check links restored from older persisted catalogue objects:
        # they are not proof of a public product URL and may carry tokens.
        item.friendly_url = public_book_url(item.friendly_url, self.library_url)

    def _inventory_owner_or_none(self):
        """Freeze all candidate owner bindings, including ambiguous ones.

        _identity_id() intentionally refuses multiple/no owners for delivery.
        Its former None fallback here erased *which* unresolved mapping was
        observed: [A,B] -> [A,C] looked unchanged although the ownership
        evidence changed during MesLivres. Keep legacy inventory readable
        while unresolved, but reject a changed mapping after the read.
        No acquisition rights are inferred from this advisory snapshot.
        """
        broker = self.identity_broker
        if broker is None:
            return None  # Legacy brokerless inventory inspection.
        try:
            accounts = broker.accounts()
            if type(accounts) not in (list, tuple):
                return ('unverified_broker',)
            target = 'numilog:' + self.scope
            owners = []
            for ref in accounts:
                if (getattr(ref, 'provider_id', None) != 'numilog'
                        or getattr(ref, 'account_id', None) != target):
                    continue
                # AccountRef v2 (#3171) proves the credential as well as the
                # upstream account. An identical synthetic account key from
                # another credential is not authority for this session.
                # Legacy v1 refs remain candidates: mixed v1/v2 cannot
                # silently erase a duplicate unqualified owner.
                version = getattr(ref, 'scope_version', 1)
                if type(version) is not int or version not in (1, 2):
                    return ('unverified_broker', id(broker))
                if version == 2:
                    credential = getattr(ref, 'credential_id', None)
                    if type(credential) is not str or not credential:
                        return ('unverified_broker', id(broker))
                    if credential != self.credential_id:
                        continue
                identity = getattr(ref, 'identity_id', None)
                # Bad IDs remain unresolved; never stringify arbitrary
                # provider objects, even into an internal owner snapshot.
                owners.append(identity if type(identity) is str else '')
            # A canonical *multiset* notices both owner changes and duplicate
            # rows, without relying on upstream account order.
            return ('broker', id(broker), tuple(sorted(owners)))
        except Exception:
            return ('unverified_broker', id(broker))

    def _enrichment_identity_snapshot(self):
        """Pin the exact Core broker, candidate multiset and owner per read.

        The old _identity_id() set discarded duplicate mappings and tracked
        only the identity string. Replacing the broker A->A (or adding another
        same-identity account ref) must not publish a stale private purchase
        after a public metadata GET. Brokerless legacy enrichment stays explicit.
        """
        if self.identity_broker is None:
            return None
        owner = self._inventory_owner_or_none()
        if (type(owner) is not tuple or len(owner) != 3
                or owner[0] != 'broker'
                or type(owner[2]) is not tuple or len(owner[2]) != 1
                or type(owner[2][0]) is not str or not owner[2][0]):
            raise ProviderCapabilityError(
                'Numilog : identité propriétaire indisponible ; enrichissement arrêté'
            )
        return owner

    def _ensure_enrichment_identity(self, expected):
        if expected is None:
            return  # Legacy no-broker path; credential/record ID still checked.
        if self._inventory_owner_or_none() != expected:
            raise ProviderCapabilityError(
                'Numilog : identité propriétaire modifiée ; enrichissement arrêté'
            ) from None

    def _identity_id(self):
        """Resolve one strictly typed Core owner before any ADEPT fulfillment.

        A set of identity labels can erase duplicate AccountRefs, and str()
        of a malformed identifier can synthesize a different Core identity.
        Use the same exact multiset/credential-account binding as the public
        rights observations; legacy brokerless delivery stays unavailable.
        """
        if self.identity_broker is None:
            raise ProviderCapabilityError(
                'Numilog : broker identités requis pour le fulfillment ACSM'
            )
        owner = self._inventory_owner_or_none()
        if (type(owner) is not tuple or len(owner) != 3
                or owner[0] != 'broker' or type(owner[2]) is not tuple
                or len(owner[2]) != 1 or type(owner[2][0]) is not str
                or not owner[2][0]):
            raise ProviderCapabilityError(
                'Numilog : identité propriétaire du compte non résolue pour le fulfillment ACSM'
            )
        return owner[2][0]

    def _validate_download_owner(self, expected, *, owner_scope=None):
        # A newly valid binding is not the binding which authorized this
        # delivery. A->A broker replacement is also a new authority, even
        # when the same identity string remains associated with the account.
        try:
            current = self._identity_id()
            scope_stable = (
                owner_scope is None
                or self._inventory_owner_or_none() == owner_scope
            )
        except Exception:
            current, scope_stable = None, False
        if current != expected or not scope_stable:
            raise ProviderCapabilityError(
                'Numilog : rattachement du compte modifié ; téléchargement arrêté'
            ) from None

    def _fulfill_acsm(self,path,*,title=None,trace=None,identity_id=None):
        if self.acquisition_broker is None:
            raise ProviderCapabilityError('Numilog : broker acquisition requis pour traiter un ACSM')
        if identity_id is None:
            identity_id=self._identity_id()
        else:
            self._validate_download_owner(identity_id)
        # Core's owner ID alone cannot distinguish an identity broker swapped
        # A->A while ADEPT imports. Pin the *exact* owner bindings and broker
        # instance around this potentially long-running external operation.
        owner_scope = self._inventory_owner_or_none()
        if (type(owner_scope) is not tuple or len(owner_scope) != 3
                or owner_scope[0] != 'broker'
                or owner_scope[2] != (identity_id,)):
            raise ProviderCapabilityError(
                'Numilog : rattachement du compte modifié ; téléchargement arrêté'
            )
        result=self.acquisition_broker.import_acsm(
            Path(path), identity_id=identity_id, provider_id='numilog', title=title or None)
        if self._inventory_owner_or_none() != owner_scope:
            raise ProviderCapabilityError(
                'Numilog : rattachement du compte modifié ; téléchargement arrêté'
            )
        self._validate_download_owner(identity_id)
        if not isinstance(result,dict):
            raise ProviderError('Numilog : résultat de fulfillment ACSM invalide')
        # The broker is the authority for fulfillment, not for arbitrary
        # provider-visible error text. Only published fixed ADEPT reasons
        # may cross the plugin boundary; never stringify unknown objects.
        status = result.get('status') if type(result.get('status')) is str else ''
        if status=='attention_required':
            if trace is not None:
                trace.fail('adept_attention_required')
                trace.event('adept_fulfillment','adept_attention_required')
            raw_code = result.get('code')
            code = raw_code if type(raw_code) is str and raw_code in {
                'adept_profile_required', 'adept_profile_ambiguous',
            } else 'adept_attention_required'
            raise ProviderCapabilityError('Numilog : fulfillment ACSM nécessite une action ('+code+')')
        path = result.get('path')
        if status not in {'complete','unchanged'} or type(path) is not str or not path:
            raise ProviderError('Numilog : fulfillment ACSM non confirmé')
        produced=Path(path)
        if not produced.is_file():
            raise ProviderError('Numilog : fichier final ADEPT introuvable')
        self._diag('téléchargement',format='ACSM/ADEPT',fulfillment=status)
        return produced,self.library_url

    def _download(self,url,destination,*,title=None,author=None,book_id=None,source='direct_url',identity_id=None,expected_readbook_url=None,from_owned_confirmation=False,
                  authorized_link=None, authorized_kind=None, expected_owner_scope=None):
        with DeliveryTrace(self.diagnostics, source=source, connected_initial=self.connected) as trace:
            trace.fail('adept_unconfirmed')
            owner = identity_id
            if owner is None and self.identity_broker is not None:
                owner = self._identity_id()
            # The direct-EPUB route previously checked only the owner string
            # during network reads and before publication. Preserve the Core
            # broker instance plus exact candidate multiset from the initial
            # fresh owned card, not just its identity label.
            owner_scope = expected_owner_scope
            if owner is not None:
                if owner_scope is None and self.identity_broker is not None:
                    owner_scope = self._inventory_owner_or_none()
                if self.identity_broker is not None and owner_scope is None:
                    raise ProviderCapabilityError(
                        'Numilog : rattachement du compte modifié ; téléchargement arrêté'
                    )
                self._validate_download_owner(owner, owner_scope=owner_scope)
            seen = set()
            # Keep the fresh owned row's readBook coordinates across redirects;
            # confirmation-page product/copy evidence cannot choose another book.
            expected_readbook = (
                expected_readbook_url if type(expected_readbook_url) is str else url
            )
            attested_confirmation_redirect = None
            start_url_for_confirmation = url
            selected_link = (
                authorized_link if type(authorized_link) is str
                and url == authorized_link and source == 'owned_card'
                and authorized_kind in {'acsm', 'epub_direct'}
                and _attested_public_https(authorized_link)
                else None
            )
            selected_kind = authorized_kind if selected_link is not None else None
            signed_redirect_chain = False
            owned_confirmation = (
                from_owned_confirmation is True and source == 'owned_card'
                and url_origin(url) == url_origin(self.library_url)
                and urlsplit(url).path.casefold()
                    == '/pages/commande/confirmationpaiement.aspx'
            )
            if (source == 'direct_url' and selected_link is None
                    and url_origin(url) != url_origin(self.library_url)
                    and url_origin(url) not in ALLOWED_ACSM_ORIGINS
                    and (url_origin(url) not in self.allowed_origins
                         or urlsplit(url).path.casefold()
                         != '/pages/compte/readbook.ashx')
                    and bool(urlsplit(url).query)):
                # The historical foreign-origin readBook navigation is
                # harmless without a private-library referrer; a signed
                # handler is NOT. Even a known Numilog origin cannot make
                # an unbound delivery URL an acquired purchase.
                raise ProviderError(
                    'Numilog : livraison externe non liée à un achat ; téléchargement arrêté'
                )
            for hop in range(12):
                trace.fail('adept_unconfirmed')
                if owner is not None:
                    self._validate_download_owner(owner, owner_scope=owner_scope)
                trace.fail('origin_rejected')
                attested_hop = (url == selected_link or signed_redirect_chain)
                if attested_hop:
                    _checked_attested_network_url(url, self.allowed_origins)
                else:
                    checked_url(url, self.allowed_origins)
                trace.fail('adept_unconfirmed')
                if ((attested_hop and selected_kind == 'acsm')
                        or (not attested_hop
                            and url_origin(url) in ALLOWED_ACSM_ORIGINS)):
                    # A signed link may be single-use. Validate ownership and
                    # the importer BEFORE consuming it, even after an ordinary
                    # readBook 302 or an HTML intermediary (#1883).
                    if owner is None:
                        owner = self._identity_id()
                    else:
                        self._validate_download_owner(owner, owner_scope=owner_scope)
                    if self.acquisition_broker is None:
                        raise ProviderCapabilityError(
                            'Numilog : broker acquisition requis pour traiter un ACSM'
                        )
                trace.fail('redirect_loop')
                if url in seen:
                    raise ProviderError('Numilog : boucle de téléchargement')
                seen.add(url)
                trace.fail('unclassified_error')
                # A book selected in MesLivres is followed as a same-origin
                # navigation from that page. Do not use the readBook URL itself
                # as a referrer: its query identifies a private purchase.
                # All redirects, intermediary pages and signed ACSM endpoints
                # retain the public site root, never a private delivery URL.
                from_library = (
                    hop == 0
                    and url_origin(url) == url_origin(self.library_url)
                    and urlsplit(url).path.casefold() in {
                        '/pages/compte/readbook.ashx',
                        '/pages/commande/confirmationpaiement.aspx',
                    }
                )
                referer = self.library_url if from_library else self.base_url
                # Do not forward the user's Numilog cookies to an arbitrary
                # external publisher host obtained from a trusted Links row.
                headers = {'Referer': referer}
                if attested_hop and url_origin(url) != url_origin(self.library_url):
                    headers['Cookie'] = ''
                with self.client.stream('GET', url, headers=headers) as r:
                    self._diag('HTTP',method='GET',origin_kind=destination_category(url)[0],status=r.status_code)
                    # Validate Location before recording target evidence. A
                    # blocked redirect still gets its ordinary HTTP event
                    # (without a target), followed by origin_rejected.
                    # Validation does not perform another network request.
                    redirect_to = redirect_error = None
                    if r.status_code in REDIRECTS:
                        try:
                            if attested_hop:
                                raw_location = r.headers.get('location','')
                                if not _raw_network_url(raw_location):
                                    raise ProviderError('Numilog : redirection réseau inattendue')
                                redirect_to = urljoin(str(r.url), raw_location)
                                if not _attested_public_https(redirect_to):
                                    raise ProviderError('Numilog : redirection réseau inattendue')
                            else:
                                redirect_to = checked_redirect(
                                    str(r.url), r.headers.get('location',''), self.allowed_origins,
                                )
                        except ProviderError as exc:
                            redirect_error = exc
                    trace.event('delivery_http',
                        'redirect' if r.status_code in REDIRECTS else
                        'http_ok' if r.status_code == 200 else public_http_code(r.status_code),
                        status=r.status_code, mime=r.headers.get('content-type'), hops=hop+1,
                        destination=url, redirect_target=redirect_to,
                        # Boolean metadata from the actual outgoing HTTP request.
                        # It cannot attest cookie validity, only header presence.
                        cookie_header_present=bool(r.request.headers.get('cookie')))
                    trace.fail(public_http_code(r.status_code))
                    if r.status_code in REDIRECTS:
                        # The P0 pre-normalization guard can reject this hop
                        # before another GET. Attribute that failure safely.
                        trace.fail('origin_rejected')
                        if redirect_error is not None:
                            raise redirect_error
                        if (
                            hop == 0 and r.status_code == 302
                            and source == 'readbook' and url == expected_readbook
                            and url_origin(url) == url_origin(self.library_url)
                            and urlsplit(url).path.casefold() == '/pages/compte/readbook.ashx'
                            and url_origin(redirect_to) == url_origin(self.library_url)
                            and urlsplit(redirect_to).path.casefold()
                                == '/pages/commande/confirmationpaiement.aspx'
                        ):
                            attested_confirmation_redirect = redirect_to
                        url = redirect_to
                        signed_redirect_chain = attested_hop
                        trace.fail('redirect_limit')
                        continue
                    self._status(r)
                    trace.fail('mime_unexpected')
                    mime = r.headers.get('content-type','').split(';')[0].lower().strip()
                    if mime == 'text/html':
                        trace.fail('html_oversize')
                        html = self._small(r).decode('utf-8-sig',errors='replace')
                        try:
                            kind = html_response_kind(html)
                        except ParseError:
                            # The parser may reject deep/oversized DOM
                            # structure even when the HTTP body is within
                            # the byte limit. This is invalid HTML evidence,
                            # not proof of a missing cookie or oversize file.
                            trace.fail('html_invalid')
                            trace.event(
                                'html_selection', 'html_invalid',
                                html_kind='invalid', hops=hop+1,
                            )
                            self._diag(
                                'livraison HTML', phase='download',
                                page_kind='delivery_intermediate',
                                kind='invalid', result='invalid_structure',
                            )
                            raise ProviderError(
                                'Numilog : structure HTML de livraison inattendue'
                            ) from None
                        # Only the endpoint *category*, never signed query/HTML,
                        # belongs in the report. This distinguishes readBook from
                        # other intermediates without disclosing a customer's URL.
                        page_path = urlsplit(str(r.url)).path.lower()
                        page_kind = (
                            'readbook' if page_path == '/pages/compte/readbook.ashx'
                            else 'meslivres' if page_path == '/pages/compte/meslivres.aspx'
                            else 'delivery_intermediate'
                        )
                        if kind == 'login':
                            confirmation_probe_code = None
                            confirmation_probe_candidates = None
                            # A login widget is common in Numilog's general
                            # template. On the *attested confirmation route*
                            # alone, permit exactly one signed ACSM only when
                            # its ProductId/copy, ModalDownload and readBook
                            # coordinates are all bound to this fresh owned
                            # card. A mere link or login-shaped page cannot
                            # bypass the normal fail-closed login handling.
                            if (
                                source in {'readbook', 'owned_card'}
                                and page_path == '/pages/commande/confirmationpaiement.aspx'
                                and url_origin(str(r.url)) == url_origin(self.library_url)
                            ):
                                evidence = {}
                                chosen = {}
                                try:
                                    bound_url = delivery_url(
                                        html, str(r.url),
                                        allowed_origins=self.allowed_origins,
                                        evidence=evidence, selection=chosen,
                                        expected_readbook=expected_readbook,
                                        require_bound_confirmation=True,
                                        allow_missing_readbook=(
                                            hop == 1 and attested_confirmation_redirect is not None
                                            and str(r.url) == attested_confirmation_redirect
                                        ),
                                        allow_owned_confirmation_without_readbook=(
                                            hop == 0 and owned_confirmation
                                            and str(r.url) == start_url_for_confirmation
                                        ),
                                    )
                                except DeliverySelectionError as exc:
                                    bound_url = None
                                    confirmation_probe_code = (
                                        'html_delivery_ambiguous' if exc.code == 'ambiguous'
                                        else 'html_delivery_link_missing'
                                    )
                                    confirmation_probe_candidates = evidence.get('allowed_candidates')
                                except ParseError:
                                    bound_url = None
                                    confirmation_probe_code = 'html_invalid'
                                if bound_url is not None:
                                    trace.event(
                                        'html_selection', 'single_allowed_candidate',
                                        html_kind='intermediate', hops=hop+1,
                                        candidates=evidence.get('allowed_candidates'),
                                    )
                                    self._diag(
                                        'livraison HTML', phase='download',
                                        page_kind='delivery_intermediate',
                                        kind='intermediate',
                                        result='owned_confirmation_binding', **evidence,
                                    )
                                    url = bound_url
                                    selected_link = bound_url
                                    selected_kind = chosen.get('kind')
                                    signed_redirect_chain = False
                                    trace.fail('redirect_limit')
                                    continue
                            # A shared site template can include a password form
                            # on a page other than the authentication route.
                            # Either way, *never* select a delivery link from
                            # this ambiguous HTML. Only an exact login endpoint
                            # is enough to suspend this session; neither case
                            # proves that a cookie actually expired.
                            login_endpoint = (
                                page_path == '/login.aspx'
                                and url_origin(str(r.url)) == url_origin(self.library_url)
                            )
                            trace.fail('login_form_detected')
                            # On a known confirmation URL, preserve WHY the
                            # tightly scoped purchase binding failed. A login
                            # form alone is still not a verified session expiry.
                            # Existing closed codes/candidate counts suffice:
                            # no private HTML, signed URL or new HTTP.
                            trace.event(
                                'html_selection',
                                confirmation_probe_code or 'login_form_detected',
                                html_kind='login', hops=hop+1,
                                candidates=confirmation_probe_candidates,
                            )
                            self._diag('livraison HTML',phase='download',
                                       page_kind='login_page' if login_endpoint else page_kind,
                                       kind='login',result='login_form_detected')
                            if login_endpoint:
                                self.connected = False
                                self._session_event('delivery_login_form_detected')
                                raise ProviderError('Numilog : formulaire de connexion reçu au lieu du fichier ; reconnecter le compte puis réessayer explicitement')
                            raise ProviderError('Numilog : formulaire de connexion présent dans une page de livraison non identifiée ; téléchargement arrêté, session non invalidée')
                        if kind == 'error':
                            trace.fail('html_error')
                            trace.event('html_selection','html_error',html_kind='error',hops=hop+1)
                            self._diag('livraison HTML',phase='download',
                                       page_kind=page_kind,kind='error',result='html_error')
                            raise ProviderError('Numilog : page d’erreur reçue au lieu du fichier attendu')
                        evidence = {}
                        chosen = {}
                        trace.fail('html_delivery_link_missing')
                        # A commercial confirmation is a multi-book-capable
                        # order page, even without a login widget. A lone
                        # signed link is not sufficient proof of the selected
                        # purchase: require the same fresh readBook/card/
                        # ModalDownload binding as the login-widget branch.
                        confirmation_page = (
                            page_path == '/pages/commande/confirmationpaiement.aspx'
                            and url_origin(str(r.url)) == url_origin(self.library_url)
                        )
                        try:
                            url = delivery_url(
                                html,str(r.url),allowed_origins=self.allowed_origins,
                                evidence=evidence, selection=chosen,
                                expected_readbook=expected_readbook,
                                require_bound_confirmation=confirmation_page,
                                allow_missing_readbook=(
                                    hop == 1 and attested_confirmation_redirect is not None
                                    and str(r.url) == attested_confirmation_redirect
                                ),
                                allow_owned_confirmation_without_readbook=(
                                    hop == 0 and owned_confirmation
                                    and str(r.url) == start_url_for_confirmation
                                ),
                            )
                        except DeliverySelectionError as exc:
                            trace.fail('html_delivery_ambiguous' if exc.code == 'ambiguous' else 'html_delivery_link_missing')
                            trace.event('html_selection','html_delivery_ambiguous' if exc.code == 'ambiguous' else 'html_delivery_link_missing',html_kind='intermediate',hops=hop+1,candidates=evidence.get('allowed_candidates'))
                            self._diag('livraison HTML',phase='download',
                                       page_kind=page_kind,kind='intermediate',
                                       result=exc.code,**evidence)
                            if exc.code == 'ambiguous':
                                raise ProviderError('Numilog : plusieurs liens de livraison ; téléchargement arrêté') from None
                            raise ProviderError('Numilog : lien de livraison absent ; reconnecter le compte ou signaler la page modifiée') from None
                        except ParseError:
                            trace.fail('html_invalid')
                            trace.event('html_selection','html_invalid',html_kind='invalid',hops=hop+1)
                            self._diag('livraison HTML',phase='download',
                                       page_kind=page_kind,kind='invalid',result='invalid_structure')
                            raise ProviderError('Numilog : structure HTML de livraison inattendue') from None
                        trace.event('html_selection','single_allowed_candidate',html_kind='intermediate',hops=hop+1,candidates=evidence.get('allowed_candidates'))
                        self._diag('livraison HTML',phase='download',
                                   page_kind=page_kind,kind='intermediate',
                                   result='single_allowed_candidate',**evidence)
                        if chosen.get('kind') in {'acsm', 'epub_direct'}:
                            selected_link = url
                            selected_kind = chosen['kind']
                            signed_redirect_chain = False
                        trace.fail('redirect_limit')
                        continue
                    if mime not in {'application/epub+zip','application/octet-stream','application/zip',
                            'application/vnd.adobe.adept+xml','application/xml','text/xml'}:
                        raise ProviderCapabilityError('Numilog : format livré non pris en charge ; EPUB direct ou ACSM Adobe attendus')
                    trace.fail('download_oversize')
                    length = r.headers.get('content-length','')
                    if length.isdigit() and int(length) > self.maximum:
                        raise ProviderError('Numilog : téléchargement trop volumineux')
                    fd,name = tempfile.mkstemp(prefix='.numilog-',suffix='.part',dir=destination.parent)
                    part = Path(name)
                    size = 0
                    try:
                        with os.fdopen(fd,'wb') as output:
                            for chunk in r.iter_bytes():
                                size += len(chunk)
                                if size > self.maximum:
                                    raise ProviderError('Numilog : téléchargement trop volumineux')
                                output.write(chunk)
                        trace.fail('adept_unconfirmed')
                        if owner is not None:
                            self._validate_download_owner(owner, owner_scope=owner_scope)
                        trace.fail('artifact_invalid')
                        if inspect_artifact(part, 'acsm').valid:
                            acsm = part.with_suffix('.acsm')
                            os.replace(part, acsm)
                            try:
                                trace.fail('adept_unconfirmed')
                                result = self._fulfill_acsm(acsm,title=title,trace=trace,identity_id=owner)
                                if owner is not None:
                                    self._validate_download_owner(owner, owner_scope=owner_scope)
                                trace.success('acsm_complete')
                                trace.event('adept_fulfillment', 'acsm_complete', hops=hop+1)
                                return result
                            finally:
                                acsm.unlink(missing_ok=True)
                        if mime in {'application/vnd.adobe.adept+xml','application/xml','text/xml'}:
                            trace.fail('xml_not_acsm')
                            raise ProviderCapabilityError('Numilog : réponse XML non ACSM ; téléchargement arrêté')
                        validate_epub(part)
                        trace.fail('adept_unconfirmed')
                        if owner is not None:
                            self._validate_download_owner(owner, owner_scope=owner_scope)
                        if title:
                            destination = publish(part, destination.parent, title=title, author=author, book_id=book_id or title)
                        else:
                            publish_artifact_file(part, destination, overwrite=True)
                    finally:
                        part.unlink(missing_ok=True)
                    # Publication may call an external plugin and race the
                    # Core owner broker. Never report success to Core after
                    # a swap, even if the bytes have already been staged.
                    if owner is not None:
                        self._validate_download_owner(owner, owner_scope=owner_scope)
                    self._diag('téléchargement',bytes=size,format='EPUB')
                    trace.success('epub_complete')
                    trace.event('artifact_validation','epub_complete',hops=hop+1)
                    # La source rendue au core est stable ; jamais l'URL signée.
                    return destination,self.library_url
            trace.fail('redirect_limit')
            raise ProviderError('Numilog : trop de redirections ou pages intermédiaires')

    def download_direct(self,item,target_dir,*,expected_offer_id=None):
        with self.lock:
            # An optional Core-admitted offer can only narrow the legacy
            # explicit selection; it never grants an automatic acquisition.
            if expected_offer_id is not None and (
                type(expected_offer_id) is not str
                or not expected_offer_id.startswith('numilog.card:')
                or len(expected_offer_id) != len('numilog.card:') + 64
            ):
                raise ProviderCapabilityError('numilog_offer_unobserved')
            self._canonical_item(item)
            owner = self._identity_id() if self.identity_broker is not None else None
            owner_scope = self._inventory_owner_or_none() if owner is not None else None
            # Read a fresh private library only for an explicit user action.
            # Search/sync call _rows() without delivery_evidence, so no signed
            # ACSM link escapes into SearchResult, catalog, or diagnostics.
            rows = self._rows(
                include_delivery=True, delivery_record_id=item.record_id,
            )
            if owner is not None:
                self._validate_download_owner(owner, owner_scope=owner_scope)
            row = next((row for row in rows if self._id(row) == item.record_id),None)
            if row is None:
                raise ProviderError('Numilog : livre absent de la bibliothèque actuelle')
            if expected_offer_id is not None:
                # No readBook fallback, confirmation navigation or signed GET
                # may silently replace the exact action admitted by Core.
                current_offer = _numilog_card_offer_id(
                    self.credential_id, 'numilog:' + self.scope,
                    item.record_id, row.get('delivery'),
                )
                if current_offer != expected_offer_id:
                    raise ProviderCapabilityError('numilog_offer_changed')
            directory = Path(target_dir)
            directory.mkdir(parents=True,exist_ok=True)
            name = 'numilog-'+hashlib.sha256(self._id(row).encode()).hexdigest()[:32]+'.epub'
            try:
                evidence = row.get('delivery')
                # Only the exact fresh row matched by the stable scoped ID can
                # supply a signed URL. ProductId != lid unless independently
                # attested; the DOM card binding is the evidence, not numbers.
                if isinstance(evidence, OwnedDelivery):
                    # A pre-bound account identity is mandatory BEFORE a
                    # signed ACSM GET. The broker will re-check it at import;
                    # direct EPUB fallback retains its existing contract.
                    self._identity_id()
                    if (evidence.delivery_kind == 'acsm'
                            and self.acquisition_broker is None):
                        raise ProviderCapabilityError(
                            'Numilog : broker acquisition requis pour traiter un ACSM'
                        )
                    if not _attested_public_https(evidence.url):
                        raise ProviderError('Numilog : destination réseau inattendue')
                    start = evidence.url
                    self._diag('livraison ACSM',phase='owned_card',kind='card_bound',result='single_allowed_candidate')
                else:
                    # Some owned cards may directly expose a confirmed order
                    # page instead of requiring an initial readBook 302.
                    # The order ref comes *only* from a fresh, uniquely
                    # selected card's explicit canonical HTML href.
                    confirmation = row.get('confirmation')
                    if type(confirmation) is str:
                        checked_url(confirmation, self.allowed_origins)
                        start = confirmation
                        self._diag('livraison ACSM',phase='confirmation',
                                   kind='scoped_link',result='direct_confirmed_href')
                    else:
                        start = row['href']
                        self._diag('livraison ACSM',phase='readbook',kind='scoped_link',result='fallback')
                # This fixed source enum is projected unchanged by the
                # Core. An explicit confirmation href from an owned card
                # is a DIFFERENT first navigation from a readBook 302.
                # Path/cookie/reference metadata remain redacted and
                # the same fresh owned edition is still bound to the ACSM.
                entry_from_owned_card = (
                    isinstance(evidence, OwnedDelivery)
                    or type(row.get('confirmation')) is str
                )
                return self._download(start,directory/name,title=row['title'],author=item.author,
                    book_id=self._id(row),source='owned_card' if entry_from_owned_card else 'readbook',
                    identity_id=owner,expected_owner_scope=owner_scope,
                    expected_readbook_url=row['href'],
                    from_owned_confirmation=type(row.get('confirmation')) is str
                        and not isinstance(evidence, OwnedDelivery),
                    authorized_link=evidence.url if isinstance(evidence, OwnedDelivery) else None,
                    authorized_kind=evidence.delivery_kind
                        if isinstance(evidence, OwnedDelivery) else None)
            except httpx.HTTPError:
                raise ProviderError('Numilog : erreur réseau pendant le téléchargement') from None

class NumilogPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def activate(self,context):
        self.configuration = dict(context.config)
        self.acquisition_broker = context.broker('acquisition')
        self.identity_broker = context.broker('identities')
        return self

    def create_session(self,*,credential_id,label,credentials,options,config,storage,diagnostics):
        return NumilogSession(credential_id=credential_id,label=label,credentials=credentials,
            options={**getattr(self,'configuration',{}),**options},config=config,diagnostics=diagnostics,
            acquisition_broker=getattr(self,'acquisition_broker',None),identity_broker=getattr(self,'identity_broker',None))

PLUGIN = NumilogPlugin()
