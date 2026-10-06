"""Provider externe Numilog 0.1.1, bibliothèque privée et EPUB direct."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unicodedata
import zipfile
from xml.etree import ElementTree as ET
from urllib.parse import urljoin, urlsplit

import httpx
from cifereca.plugin_api import (Account, CredentialField, ItemDetail, ProviderCapabilities,
    ProviderCapabilityError, ProviderConfigurationError, ProviderDescriptor, ProviderError,
    ProviderPlugin, ProviderSession, SearchResult)
from cifereca_numilog_html import BASE, LIBRARY, ParseError, parse_library, parse_library_page, delivery_url
from cifereca_numilog_filename import publish

VERSION = '0.1.5'
DESCRIPTOR = ProviderDescriptor(id='numilog',display_name='Numilog',
    description='Bibliothèque personnelle Numilog et téléchargement EPUB direct.',state='ready',
    capabilities=ProviderCapabilities(authentication=True,multiple_credentials=True,
        search=True,holdings=True,personal_library=True,direct_download=True),
    credential_fields=(CredentialField('email','Adresse email Numilog'),CredentialField('password','Mot de passe Numilog',secret=True)),
    homepage=BASE,notes=('Recherche limitée aux titres possédés. Achats sur numilog.com.',
        'EPUB direct uniquement ; Adobe ACSM, LCP, PDF et audio non qualifiés.'))

ALLOWED = {'www.numilog.com','services.numilog.com','www.edenlivres.fr','storage.googleapis.com'}
REDIRECTS = {301,302,303,307,308}
MAX_HTML = 4*1024*1024

def checked_url(value):
    try:
        p = urlsplit(value)
        if p.scheme == 'https' and p.hostname in ALLOWED and p.port in {None,443} and not p.username and not p.password and not p.fragment:
            return value
    except ValueError:
        pass
    raise ProviderError('Numilog : destination réseau inattendue ; téléchargement arrêté')

def validate_epub(path):
    try:
        with zipfile.ZipFile(path) as z:
            if len(z.infolist()) > 20000 or len({i.filename for i in z.infolist()}) != len(z.infolist()):
                raise ValueError()
            if any(i.flag_bits & 1 for i in z.infolist()):
                raise ProviderCapabilityError('Numilog : archive EPUB chiffrée non prise en charge')
            def read(name, maximum):
                if z.getinfo(name).file_size > maximum:
                    raise ValueError()
                return z.read(name)
            if read('mimetype',100) != b'application/epub+zip':
                raise ValueError()
            container = ET.fromstring(read('META-INF/container.xml',1024*1024))
            roots = container.findall('.//{*}rootfile')
            if not roots:
                raise ValueError()
            for root in roots:
                opf = root.get('full-path','')
                if not opf or opf.startswith('/') or '..' in opf.split('/'):
                    raise ValueError()
                if ET.fromstring(read(opf,5*1024*1024)).tag.split('}')[-1] != 'package':
                    raise ValueError()
            names = set(z.namelist())
            if 'META-INF/license.lcpl' in names or 'META-INF/rights.xml' in names:
                raise ProviderCapabilityError('Numilog : EPUB protégé Adobe/LCP non pris en charge dans cette version')
            if 'META-INF/encryption.xml' in names:
                encryption = ET.fromstring(read('META-INF/encryption.xml',1024*1024))
                methods = encryption.findall('.//{*}EncryptionMethod')
                fonts = {'http://www.idpf.org/2008/embedding','http://ns.adobe.com/pdf/enc#RC'}
                if not methods or any(m.get('Algorithm') not in fonts for m in methods):
                    raise ProviderCapabilityError('Numilog : chiffrement EPUB non pris en charge dans cette version')
    except ProviderCapabilityError:
        raise
    except (ValueError,KeyError,OSError,zipfile.BadZipFile,ET.ParseError,RuntimeError,NotImplementedError):
        raise ProviderError('Numilog : le fichier reçu n’est pas un EPUB valide') from None

class NumilogSession(ProviderSession):
    def __init__(self, *, config, diagnostics, **kwargs):
        super().__init__(descriptor=DESCRIPTOR,**kwargs)
        self.diagnostics = diagnostics
        self.scope = hashlib.sha256(self.credential_id.encode()).hexdigest()[:20]
        timeout = float(self.options.get('timeout_seconds',30))
        self.maximum = int(self.options.get('max_download_bytes',100*1024*1024))
        if not 1 <= timeout <= 120 or not 1024 <= self.maximum <= 512*1024*1024:
            raise ProviderConfigurationError('Numilog : limites réseau invalides')
        self.client = httpx.Client(timeout=timeout,follow_redirects=False,trust_env=False,
            headers={'User-Agent':'Cifereca-Numilog/'+VERSION,'Accept-Language':'fr-FR,fr;q=0.9'})
        self.lock = threading.RLock()

    def _diag(self,operation,**detail):
        if self.diagnostics is not None:
            self.diagnostics.add('NUMILOG',operation,'ok',detail=detail,level='detailed')

    @staticmethod
    def _status(response):
        if response.status_code in {401,403}:
            raise ProviderError('Numilog : accès refusé ou contrôle du navigateur ; session HTTP à qualifier')
        if response.status_code == 429:
            raise ProviderError('Numilog : trop de requêtes ; réessayer plus tard')
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

    def _page(self,url,*,payload=None):
        method = 'POST' if payload is not None else 'GET'
        for _ in range(10):
            checked_url(url)
            headers = {'Referer':BASE}
            if payload is not None:
                headers.update({'Origin':BASE.rstrip('/'),'X-Requested-With':'XMLHttpRequest','Accept':'application/json'})
            with self.client.stream(method,url,json=payload,headers=headers) as r:
                self._diag('HTTP',method=method,host=urlsplit(url).hostname,status=r.status_code)
                if r.status_code in REDIRECTS:
                    if payload is not None:
                        raise ProviderError('Numilog : redirection inattendue pendant la connexion')
                    url = urljoin(str(r.url),r.headers.get('location',''))
                    continue
                self._status(r)
                return str(r.url),self._small(r).decode('utf-8-sig',errors='replace')
        raise ProviderError('Numilog : trop de redirections')

    def connect(self):
        with self.lock:
            self.connected = False
            self._accounts = []
            self.client.cookies.clear()
            email = str(self.credentials.get('email') or '').strip()
            password = str(self.credentials.get('password') or '')
            if not email or not password:
                raise ProviderConfigurationError('Numilog : email et mot de passe requis')
            try:
                self._page(BASE)
                _, text = self._page(BASE+'Login.Aspx/AuthUser',payload={'frmLogin':email,'frmPassword':password})
                result = json.loads(text).get('d')
                if isinstance(result,str):
                    result = json.loads(result)
                if not isinstance(result,dict) or result.get('AuthSuccess') is not True:
                    raise ProviderError('Numilog : connexion refusée')
                url, text = self._page(LIBRARY)
                if urlsplit(url).path.lower() != '/pages/compte/meslivres.aspx':
                    raise ProviderError('Numilog : session non reconnue par la bibliothèque')
                parse_library(text)
            except (httpx.HTTPError,ValueError,TypeError,AttributeError):
                self.client.cookies.clear()
                raise ProviderError('Numilog : connexion impossible ou réponse inattendue') from None
            except ProviderError:
                self.client.cookies.clear()
                raise
            self.connected = True
            self._accounts = [Account(display_name=self.label or 'Compte Numilog',unique_id='numilog:'+self.scope,
                provider_id='numilog',credential_id=self.credential_id,provider_label='Numilog')]
            self._diag('connexion')
            return self.accounts

    def close(self):
        with self.lock:
            self.client.cookies.clear()
            self.client.close()
            self.connected = False
            self._accounts = []
            self.credentials.clear()

    def _rows(self):
        if not self.connected:
            raise ProviderError('Numilog : connecter le compte avant cette opération')
        try:
            url = LIBRARY
            rows = []
            seen_pages = set()
            seen_items = set()
            for page_index in range(100):
                if url in seen_pages:
                    raise ProviderError('Numilog : boucle de pagination de bibliothèque')
                seen_pages.add(url)
                final_url,text = self._page(url)
                if urlsplit(final_url).path.lower() != '/pages/compte/meslivres.aspx':
                    self.connected = False
                    raise ProviderError('Numilog : session expirée ; reconnecter le compte')
                page_rows,next_url = parse_library_page(text,final_url)
                for row in page_rows:
                    key=(row['lid'],row['variant'])
                    if key not in seen_items:
                        seen_items.add(key);rows.append(row)
                if not next_url:
                    self._diag('pagination bibliothèque',pages=page_index+1,items=len(rows))
                    return rows
                url=next_url
            raise ProviderError('Numilog : bibliothèque trop paginée (>100 pages)')
        except ParseError as exc:
            raise ProviderError('Numilog : '+str(exc)) from None
        except httpx.HTTPError:
            raise ProviderError('Numilog : bibliothèque inaccessible ou structure modifiée') from None

    def _id(self,row):
        variant = hashlib.sha256(row['variant'].encode()).hexdigest()[:16]
        return 'numilog:'+self.scope+':'+row['lid']+':'+variant

    def _normalize(self,row):
        key = self._id(row)
        return SearchResult(id=key,record_id=key,docbase='NUMILOG',medium='digital',title=row['title'],
            author=None,type='Livre numérique',format=None,publisher=None,published_at=None,description=None,
            thumbnail_url=row['cover'],friendly_url=LIBRARY,scenario='NUMILOG_LIBRARY',source_provider='numilog',
            source_credential=self.credential_id,download_url=None,content_type='ebook',source_collection='library',
            owned=True,downloadable=True,metadata={'format_unverified':True})

    def library(self):
        with self.lock:
            rows = self._rows()
            self._diag('bibliothèque',items=len(rows))
            return [self._normalize(row) for row in rows]

    def search(self,query,scope='all',page=0,field='all'):
        if field not in {'all','title','titre'}:
            raise ProviderCapabilityError('Numilog : recherche par titre uniquement dans cette version')
        if scope not in {'all','digital'}:
            return []
        def fold(value):
            return ''.join(c for c in unicodedata.normalize('NFKD',value.casefold()) if not unicodedata.combining(c))
        terms = fold(query).split()
        matches = [row for row in self.library() if all(t in fold(row.title) for t in terms)]
        start = max(0,int(page))*50
        return matches[start:start+50]

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
        item.friendly_url = LIBRARY

    def _download(self,url,destination,*,title=None,author=None,book_id=None):
        seen = set()
        for _ in range(12):
            checked_url(url)
            if url in seen:
                raise ProviderError('Numilog : boucle de téléchargement')
            seen.add(url)
            with self.client.stream('GET',url,headers={'Referer':BASE}) as r:
                self._diag('HTTP',method='GET',host=urlsplit(url).hostname,status=r.status_code)
                if r.status_code in REDIRECTS:
                    url = urljoin(str(r.url),r.headers.get('location',''))
                    continue
                self._status(r)
                mime = r.headers.get('content-type','').split(';')[0].lower().strip()
                if mime == 'text/html':
                    html = self._small(r).decode('utf-8-sig',errors='replace')
                    try:
                        url = delivery_url(html,str(r.url))
                    except ParseError:
                        raise ProviderError('Numilog : lien de livraison absent ; reconnecter le compte ou signaler la page modifiée') from None
                    continue
                if mime not in {'application/epub+zip','application/octet-stream','application/zip'}:
                    raise ProviderCapabilityError('Numilog : fichier autre qu’un EPUB direct ; Adobe ACSM, LCP, PDF et audio non pris en charge')
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
                    validate_epub(part)
                    if title:
                        destination = publish(part, destination.parent, title=title, author=author, book_id=book_id or title)
                    else:
                        os.replace(part,destination)
                finally:
                    part.unlink(missing_ok=True)
                self._diag('téléchargement',bytes=size,format='EPUB')
                # La source rendue au core est stable ; jamais l'URL signée.
                return destination,LIBRARY
        raise ProviderError('Numilog : trop de redirections ou pages intermédiaires')

    def download_direct(self,item,target_dir):
        with self.lock:
            self._canonical_item(item)
            rows = self._rows()
            row = next((row for row in rows if self._id(row) == item.record_id),None)
            if row is None:
                raise ProviderError('Numilog : livre absent de la bibliothèque actuelle')
            directory = Path(target_dir)
            directory.mkdir(parents=True,exist_ok=True)
            name = 'numilog-'+hashlib.sha256(self._id(row).encode()).hexdigest()[:32]+'.epub'
            try:
                return self._download(row['href'],directory/name,title=row['title'],author=item.author,book_id=self._id(row))
            except httpx.HTTPError:
                raise ProviderError('Numilog : erreur réseau pendant le téléchargement') from None

class NumilogPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def activate(self,context):
        self.configuration = dict(context.config)
        return self

    def create_session(self,*,credential_id,label,credentials,options,config,storage,diagnostics):
        return NumilogSession(credential_id=credential_id,label=label,credentials=credentials,
            options={**getattr(self,'configuration',{}),**options},config=config,diagnostics=diagnostics)

PLUGIN = NumilogPlugin()
