"""Parseurs du HTML observé ; aucun JavaScript exécuté."""
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit, parse_qs

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

def parse_library_page(text, base_url=LIBRARY):
    root = Parser(text).root
    tables = [n for n in root.walk() if n.attrs.get('id') == 'myTable']
    if len(tables) != 1:
        raise ParseError('Bibliothèque absente : session expirée ou page modifiée')
    items, keys = [], set()
    for card in tables[0].children:
        # Le modèle n'est pas un livre possédé, même s'il est déplacé dans la table.
        if card.attrs.get('id') == 'ModelItemMesLivres':
            continue
        nodes = list(card.walk())
        links = [n for n in nodes if n.tag == 'a' and 'LnkDownloadBook' in classes(n)]
        if not links:
            continue
        titles = [n.text() for n in nodes if 'pTitle' in classes(n) and n.text()]
        images = [n.attrs.get('src') for n in nodes if n.tag == 'img' and 'imgCouv' in classes(n)]
        for link in links:
            href = urljoin(base_url, link.attrs.get('href') or '')
            url = urlsplit(href)
            if url.scheme != 'https' or url.hostname != 'www.numilog.com' or url.username or url.password or url.port not in {None,443} or url.path != '/Pages/Compte/readBook.ashx':
                raise ParseError('Lien de bibliothèque inattendu')
            params = parse_qs(url.query)
            lid = params.get('lid',[])
            if len(lid) != 1 or not re.fullmatch(r'\d{1,20}',lid[0]):
                raise ParseError('Identifiant du livre absent ou inattendu')
            # Le paramètre m distingue lecture/download : reprendre le href exact.
            variant = tuple((k,tuple(params[k])) for k in ('m','format','FormatId','FORMAT_ID') if k in params)
            key = (lid[0],variant)
            if key in keys:
                continue
            keys.add(key)
            cover = urljoin(BASE,images[0]) if images and images[0] else None
            if cover and (urlsplit(cover).scheme != 'https' or urlsplit(cover).hostname != 'couverture.numilog.com' or urlsplit(cover).query):
                cover = None
            items.append({'lid':lid[0],'variant':repr(key[1]),'title':titles[0] if titles else 'Livre Numilog '+lid[0], 'cover':cover, 'href':href})

    next_links=[]
    for node in root.walk():
        if node.tag != 'a' or 'next' not in (node.attrs.get('rel') or '').split():
            continue
        href=str(node.attrs.get('href') or '').strip()
        if not href:
            continue
        resolved=urljoin(base_url,href)
        parsed=urlsplit(resolved)
        if parsed.scheme!='https' or parsed.hostname!='www.numilog.com' or parsed.username or parsed.password or parsed.port not in {None,443}:
            raise ParseError('Lien de pagination inattendu')
        if parsed.path.lower()!='/pages/compte/meslivres.aspx':
            raise ParseError('Lien de pagination inattendu')
        next_links.append(resolved)
    next_links=list(dict.fromkeys(next_links))
    if len(next_links)>1:
        raise ParseError('Pagination ambiguë dans la bibliothèque')
    return items,(next_links[0] if next_links else None)


def parse_library(text):
    items,_next=parse_library_page(text,LIBRARY)
    return items

QUOTED = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''',re.X)

def delivery_url(text, base_url):
    root = Parser(text).root
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
            # Pas de concaténation ou expression JS : URL littérale seulement.
            if not isinstance(value,str) or not value.startswith('https://services.numilog.com/'):
                continue
            url = urljoin(base_url,unescape(value))
            parsed = urlsplit(url)
            if parsed.hostname == 'services.numilog.com' and parsed.scheme == 'https' and not parsed.username and not parsed.password and parsed.port in {None,443}:
                candidates.add(url)
    if len(candidates) != 1:
        raise ParseError('Lien de livraison absent ou ambigu : page modifiée ou session expirée')
    return candidates.pop()
