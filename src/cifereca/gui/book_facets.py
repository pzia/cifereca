"""Shared attribute vocabulary for rows, notices and search controls."""
from dataclasses import dataclass
from cifereca.core.search import available_formats, support_kind
from cifereca.language import normalize_language_tag, language_label

CONTENT_FIELDS=frozenset({'title','author','subject','series','collection'})
FORM_FIELDS=frozenset({'source','format','support','publisher','isbn','language','date'})
LABELS={'author':'Auteur','subject':'Sujet','series':'Série','collection':'Collection','publisher':'Éditeur','source':'Source','format':'Format','support':'Support','isbn':'ISBN','language':'Langue'}

@dataclass(frozen=True)
class BookFacet:
    field:str
    label:str
    value:str
    @property
    def group(self):return 'content' if self.field in CONTENT_FIELDS else 'form'
    @property
    def tooltip(self):return ('Fond' if self.group=='content' else 'Forme')+' · '+LABELS.get(self.field,self.field)+' · rechercher '+self.label

def facets_for_book(row,provider_name=lambda value:value):
    metadata=row.get('metadata') or {};out=[];seen=set()
    for field,value in [('author',(metadata.get('epub_metadata') or {}).get('authors') or row.get('author')),('series',metadata.get('series')),('collection',metadata.get('collection')),('subject',metadata.get('subjects')),('publisher',metadata.get('publisher') or row.get('publisher'))]:
        values=value if isinstance(value,(list,tuple)) else [value]
        for value in values:
            if not isinstance(value,str) or not value.strip():continue
            key=(field,value.strip())
            if key not in seen:seen.add(key);out.append(BookFacet(field,value.strip(),value.strip()))
    identifiers=(metadata.get('epub_metadata') or {}).get('identifiers') or metadata.get('identifiers') or []
    isbn_values=[row.get('isbn'),metadata.get('isbn')]
    if isinstance(identifiers,list):
        isbn_values.extend(r.get('value') for r in identifiers if isinstance(r,dict) and str(r.get('scheme') or '').casefold()=='isbn')
    for value in isbn_values:
        if isinstance(value,str) and value.strip() and ('isbn',value.strip()) not in seen:
            seen.add(('isbn',value.strip()));out.append(BookFacet('isbn',value.strip(),value.strip()))
    for raw_language in (metadata.get('languages') or metadata.get('language') or [] if isinstance(metadata.get('languages') or metadata.get('language') or [], (list, tuple)) else [metadata.get('languages') or metadata.get('language')]):
        code=normalize_language_tag(raw_language)
        if code and ('language',code) not in seen:
            seen.add(('language',code));out.append(BookFacet('language',language_label(code),code))
    support=support_kind(row)
    support_labels={'physical':'Physique','digital':'Numérique','unknown':'Support indéterminé'}
    out.append(BookFacet('support',support_labels[support],support))
    for fmt in available_formats(row):out.append(BookFacet('format',fmt.upper(),fmt))
    providers=row.get('providers') or [row.get('provider_id') or row.get('source_provider')]
    if not isinstance(providers,(list,tuple,set)): providers=[providers]
    for provider in providers:
        if provider and ('source',str(provider)) not in seen:
            seen.add(('source',str(provider))); out.append(BookFacet('source',provider_name(str(provider)),str(provider)))
    return out
