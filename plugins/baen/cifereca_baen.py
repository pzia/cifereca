from __future__ import annotations
import hashlib, html, os, re, tempfile, zipfile
from pathlib import Path
from urllib.parse import urljoin
import httpx
from cifereca.plugin_api import Account, ItemDetail, SearchResult, ProviderCapabilities, ProviderDescriptor, ProviderError, ProviderPlugin, ProviderSession
BASE='https://www.baen.com/'; FREE=BASE+'allbooks/category/index/id/2012'
DESCRIPTOR=ProviderDescriptor(id='baen',display_name='Baen',description='Baen Free Library et catalogue de livres électroniques.',state='ready',capabilities=ProviderCapabilities(search=True,holdings=True,direct_download=True),homepage=BASE,notes=('Fonds public qualifié ; achats et bibliothèque privée à qualifier.',))
class BaenSession(ProviderSession):
    def __init__(self,*,config,diagnostics,**kw):
        super().__init__(descriptor=DESCRIPTOR,**kw); self.client=httpx.Client(timeout=float(self.options.get('timeout_seconds',30)),follow_redirects=True,trust_env=False,headers={'User-Agent':'Cifereca-Baen/0.1.0','Accept-Language':'en-US,en;q=0.8'}); self.maximum=int(self.options.get('max_download_bytes',100*1024*1024)); self.page_size=50
    def connect(self):
        self.connected=True; self._accounts=[Account(display_name='Baen Free Library',unique_id='baen-public',provider_id='baen',credential_id=self.credential_id,provider_label=self.descriptor.display_name,is_primary=True)]; return self.accounts
    def close(self): self.client.close();self.connected=False;self._accounts=[]
    @staticmethod
    def _search_params(query, page, page_size=50):
        params={'page':max(0,int(page))+1,'pageSize':int(page_size),'hideBundles':0,'hideEarcs':0}
        query=' '.join(str(query or '').split())
        if query: params['q']=query
        return params
    def search(self,query,scope='all',page=0,field='all'):
        if not self.connected: raise ProviderError('Baen : connecter le catalogue')
        if scope == 'physical': return []
        try:r=self.client.get(FREE,params=self._search_params(query,page,self.page_size));r.raise_for_status()
        except httpx.HTTPError as e: raise ProviderError('Baen : catalogue inaccessible') from e
        out=[]; seen=set()
        for href,body in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',r.text,re.I|re.S):
            plain=' '.join(re.sub('<[^>]+>',' ',html.unescape(body)).split())
            if not plain or plain.lower() in {'more...','buy'} or '/allbooks/' not in href: continue
            detail=urljoin(BASE,href)
            if detail in seen: continue
            seen.add(detail); rid=hashlib.sha256(detail.encode()).hexdigest()[:24]
            out.append(SearchResult(id='baen:'+rid,record_id=rid,docbase='BAEN',medium='digital',title=plain,author=None,type='Livre numérique',format='EPUB',publisher='Baen',published_at=None,description=None,thumbnail_url=None,friendly_url=detail,scenario='BAEN_FREE_LIBRARY',source_provider='baen',source_credential=self.credential_id,download_url=None,content_type='ebook',source_collection='public',owned=False,downloadable=False))
        return out[:self.page_size]
    def holdings(self,item,search_terms=''): return ItemDetail(record_id=item.record_id,docbase='BAEN',title=item.title,author=item.author,type=item.type,year=None,thumbnail_url=item.thumbnail_url,availability_status='available',availability_label='Baen Free Library — ouvrir la fiche pour vérifier le format',holdings=[],source_provider='baen',source_credential=self.credential_id)
    def download_direct(self,item,target_dir):
        if item.source_provider!=self.provider_id or not item.friendly_url: raise ProviderError('Baen : fiche inconnue')
        try:r=self.client.get(item.friendly_url);r.raise_for_status()
        except httpx.HTTPError as e: raise ProviderError('Baen : fiche inaccessible') from e
        # Baen exposes a public free-download action on some Free Library fiches.
        links=re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',r.text,re.I|re.S)
        candidate=None
        for href,label in links:
            txt=' '.join(re.sub('<[^>]+>',' ',html.unescape(label)).split()).casefold()
            if 'download' in txt or 'epub' in txt or href.lower().endswith('.epub'):
                candidate=urljoin(item.friendly_url,href); break
        if not candidate: raise ProviderError('Baen : aucun lien EPUB public explicite sur cette fiche')
        d=Path(target_dir);d.mkdir(parents=True,exist_ok=True);dest=d/(re.sub(r'[^\w .-]','_',item.title).strip() or item.record_id)+'.epub'
        fd,tmp=tempfile.mkstemp(prefix='.baen-',suffix='.epub.part',dir=d); os.close(fd); part=Path(tmp)
        try:
            try:
                with self.client.stream('GET',candidate,headers={'Accept':'application/epub+zip, */*;q=0.2'}) as response:
                    response.raise_for_status()
                    mime=str(response.headers.get('content-type') or '').split(';',1)[0].strip().lower()
                    if mime in {'text/html','application/xhtml+xml'}: raise ProviderError('Baen : la réponse de téléchargement est une page HTML')
                    declared=response.headers.get('content-length')
                    if declared and declared.isdigit() and int(declared)>self.maximum: raise ProviderError('Baen : fichier trop volumineux')
                    size=0
                    with part.open('wb') as handle:
                        for chunk in response.iter_bytes():
                            size+=len(chunk)
                            if size>self.maximum: raise ProviderError('Baen : fichier trop volumineux')
                            handle.write(chunk)
                        handle.flush(); os.fsync(handle.fileno())
            except httpx.HTTPError as e: raise ProviderError('Baen : téléchargement inaccessible') from e
            with zipfile.ZipFile(part) as z:
                if z.read('mimetype')!=b'application/epub+zip': raise ValueError
            part.replace(dest)
        except ProviderError:
            part.unlink(missing_ok=True); raise
        except Exception:
            part.unlink(missing_ok=True);raise ProviderError('Baen : fichier reçu non valide comme EPUB')
        return dest,item.friendly_url
class Plugin(ProviderPlugin):
    descriptor=DESCRIPTOR
    def __init__(self): self._sessions=[]; self.context=None
    def activate(self,context): self.context=context; return self
    def deactivate(self):
        for session in self._sessions:
            try: session.close()
            except Exception: pass
        self._sessions.clear(); self.context=None
    def create_session(self,*,credential_id,label,credentials,options,config,storage,diagnostics):
        session=BaenSession(credential_id=credential_id,label=label,credentials=credentials,options=options,config=config,diagnostics=diagnostics); self._sessions.append(session); return session
PLUGIN=Plugin()
