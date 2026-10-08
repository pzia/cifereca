"""Shared attribute vocabulary for rows, notices and search controls."""
from dataclasses import dataclass
from cifereca.core.search import available_formats, support_kind
from cifereca.language import normalize_language_tag, language_label

CONTENT_FIELDS=frozenset({'title','author','subject','series','collection'})
FORM_FIELDS=frozenset({'source','format','support','publisher','isbn','language','date'})
LABELS={'author':'Auteur','subject':'Sujet','series':'Série','collection':'Collection','publisher':'Éditeur','source':'Source','format':'Format','support':'Support','isbn':'ISBN','language':'Langue'}
LIST_FACET_MAX_CHARS=24
LIST_FACET_MAX_VISIBLE=6
LIST_FACET_FIELD_LIMITS={'author':2,'format':2,'language':1,'collection':1,'source':1}

def compact_facet_label(value, max_chars=LIST_FACET_MAX_CHARS):
    """Keep pathological facet metadata from dictating list-row width."""
    text=str(value or '').strip()
    if len(text)<=max_chars:return text
    if max_chars<=1:return '…'[:max_chars]
    return text[:max_chars-1].rstrip()+'…'

@dataclass(frozen=True)
class BookFacet:
    field:str
    label:str
    value:str
    @property
    def group(self):return 'content' if self.field in CONTENT_FIELDS else 'form'
    @property
    def tooltip(self):
        if self.field=='_overflow':return self.value
        return ('Fond' if self.group=='content' else 'Forme')+' · '+LABELS.get(self.field,self.field)+' · rechercher '+self.label

def project_list_facets(facets,max_visible=LIST_FACET_MAX_VISIBLE):
    """Dense, stable list projection with explicit overflow."""
    selected=[];hidden=[];seen=set();field_counts={}
    for facet in facets:
        label=str(facet.label or '').strip()
        if not label:continue
        key=label.casefold()
        if key in seen:continue
        limit=LIST_FACET_FIELD_LIMITS.get(facet.field)
        if limit is not None and field_counts.get(facet.field,0)>=limit:
            hidden.append(facet);continue
        if len(selected)>=max_visible:
            hidden.append(facet);continue
        selected.append(facet);seen.add(key)
        field_counts[facet.field]=field_counts.get(facet.field,0)+1
    if hidden:
        if len(selected)>=max_visible:
            hidden.insert(0,selected.pop())
        hidden_labels=[];hidden_seen=set()
        for facet in hidden:
            label=str(facet.label or '').strip()
            key=label.casefold()
            if label and key not in hidden_seen:
                hidden_seen.add(key);hidden_labels.append(label)
        selected.append(BookFacet('_overflow',f'+{len(hidden_labels)}','Autres attributs : '+', '.join(hidden_labels)))
    return selected

def compact_row_meta(meta,visible_labels):
    """Remove metadata segments already represented by visible facets/badges."""
    hidden={str(value or '').strip().casefold() for value in visible_labels if str(value or '').strip()}
    out=[];seen=set()
    for part in str(meta or '').split(' · '):
        value=part.strip()
        key=value.casefold()
        if not value or key in hidden or key in seen:continue
        seen.add(key);out.append(value)
    return ' · '.join(out)

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
