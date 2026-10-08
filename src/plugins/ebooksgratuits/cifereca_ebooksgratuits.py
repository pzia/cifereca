from __future__ import annotations
import hashlib, html, re, unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, quote_plus, urlsplit
import httpx
from cifereca.plugin_api import Account, ItemDetail, SearchResult, ProviderCapabilities, ProviderDescriptor, ProviderError, ProviderPlugin, ProviderSession

BASE='https://www.ebooksgratuits.com/'
DESCRIPTOR=ProviderDescriptor(id='ebooksgratuits',display_name='Ebooks libres et gratuits',description='Catalogue public francophone et téléchargement EPUB.',state='ready',capabilities=ProviderCapabilities(search=True,holdings=True,direct_download=True),homepage=BASE,notes=('Source publique sans authentification.','Recherche par titre, auteur, catégorie et genre.'))
class Parser(HTMLParser):
    """Small structured parser for catalog <li> cards; no regex-based DOM slicing."""
    def __init__(self, base_url=BASE):
        super().__init__(convert_charrefs=True); self.base_url=str(base_url); self.cards=[]; self._depth=0; self._card=None; self._capture=None
    @staticmethod
    def _attrs(attrs): return {str(k).lower():str(v or '') for k,v in attrs}
    def handle_starttag(self,tag,attrs):
        tag=tag.lower(); a=self._attrs(attrs)
        if tag=='li':
            if self._depth==0: self._card={'title':[],'author':[],'description':[],'detail':'','epub':'','image':''}
            self._depth+=1; return
        if not self._card: return
        classes={x.casefold() for x in a.get('class','').split()}
        if tag=='a':
            href=a.get('href','')
            low=href.casefold()
            if 'details.php' in low: self._card['detail']=urljoin(self.base_url,href)
            if ('newsendbook.php' in low and 'format=epub' in low) or low.endswith('.epub'): self._card['epub']=urljoin(self.base_url,href)
        elif tag=='img' and not self._card['image']:
            src=a.get('src','').strip()
            if src: self._card['image']=urljoin(self.base_url,src)
        if tag in {'span','div','p'}:
            if 'listeb' in classes: self._capture='title'
            elif 'auteur' in classes: self._capture='author'
            elif classes.intersection({'resume','résumé','description','descriptif'}): self._capture='description'
    def handle_data(self,data):
        if self._card and self._capture and data.strip(): self._card[self._capture].append(data.strip())
    def handle_endtag(self,tag):
        tag=tag.lower()
        if tag in {'span','div','p'}: self._capture=None
        if tag=='li' and self._depth:
            self._depth-=1
            if self._depth==0 and self._card is not None:
                card=self._card; self._card=None
                row={k:(' '.join(v).strip() if isinstance(v,list) else v) for k,v in card.items()}
                if row['detail'] and row['epub'] and row['title']: self.cards.append(row)
    def rows(self,text):
        self.cards=[]; self._depth=0; self._card=None; self._capture=None; self.feed(text); self.close(); return list(self.cards)
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
            if self._value and label: self.options.setdefault(self._select,[]).append((self._value,label))
            self._value=None; self._pieces=[]
        elif tag=='select':
            self._select=None; self._value=None; self._pieces=[]

def _fold(value):
    return ''.join(ch for ch in unicodedata.normalize('NFKD',str(value or '').casefold()) if not unicodedata.combining(ch))

def _filter_choice(text,names,query):
    parser=FilterParser(); parser.feed(text); parser.close()
    needle=_fold(query).strip()
    if not needle: return None
    best=None
    for name in names:
        for value,label in parser.options.get(name,()):
            folded=_fold(label)
            if folded==needle: score=0
            elif folded.startswith(needle): score=1
            elif needle in folded: score=2
            else: continue
            candidate=(score,len(folded),name,value,label)
            if best is None or candidate<best: best=candidate
    return None if best is None else best[2:]

def _attested_filter_metadata(selected,label):
    label=str(label or '').strip()
    if not label:return {}
    if selected=='id_genre':
        return {'genres':[label],'genre_evidence':'ebooksgratuits.search_filter'}
    if selected=='id_categorie':
        return {'categories':[label],'category_evidence':'ebooksgratuits.search_filter'}
    return {}

class EbooksSession(ProviderSession):
    def __init__(self,*,config,diagnostics,transport=None,**kw):
        super().__init__(descriptor=DESCRIPTOR,**kw); self.transport=transport; self.base_url=str(self.options.get('base_url') or BASE).rstrip('/')+'/'; self.client=httpx.Client(timeout=float(self.options.get('timeout_seconds',30)),follow_redirects=True,trust_env=False,headers={'User-Agent':'Cifereca-EbooksGratuits/0.1.11','Accept-Language':'fr-FR,fr;q=0.9'}); self.maximum=int(self.options.get('max_download_bytes',100*1024*1024)); self.diagnostics=diagnostics; self.page_size=int(self.options.get('search_page_size',50))
    def connect(self):
        self.connected=True; self._accounts=[Account(display_name='Catalogue public',unique_id='ebooksgratuits',provider_id='ebooksgratuits',credential_id=self.credential_id,provider_label=self.descriptor.display_name,is_primary=True)]; return self.accounts
    def close(self): self.client.close(); self.connected=False; self._accounts=[]
    def _fetch(self,url):
        try:r=self.client.get(url); r.raise_for_status(); return r
        except httpx.HTTPError as e: raise ProviderError('Ebooks libres et gratuits : réseau indisponible') from e
    @staticmethod
    def _search_payload(query,page,page_size=50):
        return {'titre':str(query or '').strip(),'offset':str(max(0,int(page))*int(page_size)),'Submit':'Rechercher'}
    def search(self,query,scope='all',page=0,field='all'):
        if not self.connected: raise ProviderError('Ebooks libres et gratuits : connecter le catalogue')
        if scope == 'physical': return []
        page=max(0,int(page)); field=str(field or 'all').casefold(); attested_metadata={}
        if field in {'author','subject','category','genre'} and str(query or '').strip():
            try:
                form=self._fetch(self.base_url+'ebooks.php')
                names=('id_auteur',) if field=='author' else (('id_categorie',) if field=='category' else (('id_genre',) if field=='genre' else ('id_categorie','id_genre')))
                choice=_filter_choice(form.text,names,query)
                if choice is None: return []
                selected,value,label=choice
                attested_metadata=_attested_filter_metadata(selected,label)
                params={'begin':str(page*self.page_size),'offset':str(self.page_size),'option':'search',
                        'id_auteur':'0','id_categorie':'1','id_genre':'1'}
                params[selected]=value
                r=self.client.get(self.base_url+'ebooks.php',params=params); r.raise_for_status()
            except httpx.HTTPError as e: raise ProviderError('Ebooks libres et gratuits : recherche indisponible') from e
        elif field in {'all','title','titre'}:
            if query or page:
                try:
                    r=self.client.post(self.base_url+'ebooks.php',data=self._search_payload(query,page,self.page_size)); r.raise_for_status()
                except httpx.HTTPError as e: raise ProviderError('Ebooks libres et gratuits : recherche indisponible') from e
            else:
                r=self._fetch(self.base_url+'ebooks.php')
        else:
            return []
        out=[]
        for card in Parser(self.base_url).rows(r.text):
            detail=card['detail']; epub=card['epub']; rid=hashlib.sha256(detail.encode()).hexdigest()[:24]
            out.append(SearchResult(id='ebooksgratuits:'+rid,record_id=rid,docbase='EBOOKSGRATUITS',medium='digital',title=card['title'],author=card['author'] or None,type='Livre numérique',format='EPUB',publisher='Ebooks libres et gratuits',published_at=None,description=card['description'] or None,thumbnail_url=card['image'] or None,friendly_url=detail,scenario='EBOOKSGRATUITS_CATALOG',source_provider='ebooksgratuits',source_credential=self.credential_id,download_url=epub,content_type='ebook',source_collection='public',owned=False,downloadable=True,metadata={'license_note':'Vérifier les droits selon le pays',**attested_metadata}))
        return out[:self.page_size]
    def holdings(self,item,search_terms=''): return ItemDetail(record_id=item.record_id,docbase='EBOOKSGRATUITS',title=item.title,author=item.author,type=item.type,year=None,thumbnail_url=item.thumbnail_url,availability_status='available',availability_label='Téléchargement public EPUB',holdings=[],source_provider=self.provider_id,source_credential=self.credential_id)
    def download_direct(self,item,target_dir):
        if item.source_provider!=self.provider_id or not item.download_url: raise ProviderError('Ebooks libres et gratuits : offre inconnue')
        if self.transport is None: raise ProviderError('Ebooks libres et gratuits : transport.http indisponible')
        d=Path(target_dir); d.mkdir(parents=True,exist_ok=True); dest=d/((re.sub(r'[^\w .-]','_',item.title).strip() or item.record_id)+'.epub')
        source={
            'url':item.download_url,
            'headers':{'Accept':'application/epub+zip, application/zip;q=0.8, application/octet-stream;q=0.5, */*;q=0.2','Referer':item.friendly_url or self.base_url},
            'max_bytes':self.maximum,
            'allowed_content_types':('application/epub+zip','application/zip','application/octet-stream'),
            'expected_format':'epub',
            'overwrite':True,
        }
        try: dest=Path(self.transport.fetch(source,dest))
        except ProviderError: raise
        except Exception as e: raise ProviderError('Ebooks libres et gratuits : téléchargement inaccessible ou EPUB invalide') from e
        return dest,item.friendly_url or self.base_url
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
