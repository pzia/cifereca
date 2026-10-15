from __future__ import annotations
import hashlib, html, re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from cifereca_baen_catalog import FREE_LIBRARY, catalog_cards, MAX_PAGE_SIZE
import httpx
from cifereca.plugin_api import Account, ItemDetail, SearchResult, ProviderCapabilities, ProviderDescriptor, ProviderError, ProviderPlugin, ProviderSession
BASE='https://www.baen.com/'; FREE=FREE_LIBRARY
DESCRIPTOR=ProviderDescriptor(id='baen',display_name='Baen',description='Baen Free Library et catalogue de livres électroniques.',state='ready',capabilities=ProviderCapabilities(search=True,holdings=True,direct_download=True),homepage=BASE,notes=('Fonds public qualifié ; achats et bibliothèque privée à qualifier.',))
class BaenSession(ProviderSession):
    def __init__(self,*,config,diagnostics,transport=None,**kw):
        super().__init__(descriptor=DESCRIPTOR,**kw); self.transport=transport; self.client=httpx.Client(timeout=float(self.options.get('timeout_seconds',30)),follow_redirects=True,trust_env=False,headers={'User-Agent':'Cifereca-Baen/0.1.9','Accept-Language':'en-US,en;q=0.8'}); self.maximum=int(self.options.get('max_download_bytes',100*1024*1024)); self.page_size=50
    def connect(self):
        self.connected=True; self._accounts=[Account(display_name='Baen Free Library',unique_id='baen-public',provider_id='baen',credential_id=self.credential_id,provider_label=self.descriptor.display_name,is_primary=True)]; return self.accounts
    def close(self): self.client.close();self.connected=False;self._accounts=[]
    @staticmethod
    def _search_params(query, page, page_size=50):
        # Magento public category: p/product_list_limit. Free Library text
        # matching is applied only locally to the returned page.
        return {'p':max(0,min(100,int(page)))+1,
                'product_list_limit':max(12,min(MAX_PAGE_SIZE,int(page_size)))}
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
        if not self.connected: raise ProviderError('Baen : connecter le catalogue')
        if scope == 'physical': return []
        try:
            response=self.client.get(FREE,params=self._search_params(query,page,self.page_size))
            response.raise_for_status()
            if getattr(response,'url',None):
                final=urlsplit(str(response.url))
                if (final.scheme!='https' or final.hostname not in ('www.baen.com','baen.com')
                        or not final.path.rstrip('/').endswith('/catalog/category/view/s/free-library/id/2012')):
                    raise ProviderError('Baen : catalogue déplacé ou accès non public')
            cards=catalog_cards(response.text,limit=min(MAX_PAGE_SIZE,self.page_size))
        except httpx.HTTPError as exc:
            raise ProviderError('Baen : catalogue inaccessible') from exc
        except ValueError:
            raise ProviderError('Baen : structure du catalogue public non reconnue') from None
        # The Baen public category is not proven to implement a server-side
        # text search. Do not pretend an unmatched page exhausts the catalog.
        words=' '.join(str(query or '').casefold().split()).split()
        out=[]
        for detail,title in cards:
            if words and not all(word in title.casefold() for word in words):
                continue
            rid=hashlib.sha256(detail.encode()).hexdigest()[:24]
            out.append(SearchResult(id='baen:'+rid,record_id=rid,docbase='BAEN',medium='digital',title=title,author=None,type='Livre numérique',format='EPUB',publisher='Baen',published_at=None,description=None,thumbnail_url=None,friendly_url=detail,scenario='BAEN_FREE_LIBRARY',source_provider='baen',source_credential=self.credential_id,download_url=None,content_type='ebook',source_collection='public',owned=False,downloadable=False,metadata={'metadata_evidence':'baen.free_library_catalog','catalog_scope':'public_page_only'}))
        return out
    def holdings(self,item,search_terms=''):
        if item.source_provider==self.provider_id and item.friendly_url:
            try:
                response=self.client.get(item.friendly_url);response.raise_for_status()
                self._enrich_item_from_detail(item,response.text)
            except httpx.HTTPError:
                pass
        return ItemDetail(record_id=item.record_id,docbase='BAEN',title=item.title,author=item.author,type=item.type,year=item.published_at,thumbnail_url=item.thumbnail_url,availability_status='available',availability_label='Baen Free Library — ouvrir la fiche pour vérifier le format',holdings=[],source_provider='baen',source_credential=self.credential_id)
    def download_direct(self,item,target_dir):
        if item.source_provider!=self.provider_id or not item.friendly_url: raise ProviderError('Baen : fiche inconnue')
        try:r=self.client.get(item.friendly_url);r.raise_for_status()
        except httpx.HTTPError as e: raise ProviderError('Baen : fiche inaccessible') from e
        self._enrich_item_from_detail(item,r.text)
        # Baen exposes a public free-download action on some Free Library fiches.
        links=re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',r.text,re.I|re.S)
        candidate=None
        for href,label in links:
            txt=' '.join(re.sub('<[^>]+>',' ',html.unescape(label)).split()).casefold()
            if 'download' in txt or 'epub' in txt or href.lower().endswith('.epub'):
                candidate=urljoin(item.friendly_url,href); break
        if not candidate: raise ProviderError('Baen : aucun lien EPUB public explicite sur cette fiche')
        if self.transport is None: raise ProviderError('Baen : transport.http indisponible')
        d=Path(target_dir);d.mkdir(parents=True,exist_ok=True);dest=d/((re.sub(r'[^\w .-]','_',item.title).strip() or item.record_id)+'.epub')
        source={
            'url':candidate,
            'headers':{'Accept':'application/epub+zip, application/zip;q=0.8, application/octet-stream;q=0.5, */*;q=0.2','Referer':item.friendly_url},
            'max_bytes':self.maximum,
            'allowed_content_types':('application/epub+zip','application/zip','application/octet-stream'),
            'expected_format':'epub',
            'overwrite':True,
        }
        try: dest=Path(self.transport.fetch(source,dest))
        except ProviderError: raise
        except Exception as e: raise ProviderError('Baen : téléchargement inaccessible ou EPUB invalide') from e
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
