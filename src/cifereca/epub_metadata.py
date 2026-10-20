"""Bounded EPUB metadata/cover inspection, independent of network and DRM."""
from __future__ import annotations
import hashlib, os, re, struct, tempfile, zipfile, zlib
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from .descriptions import description_text, description_element
from .paths import cache_dir

SCHEMA=2
MAX_XML=2*1024*1024
MAX_IMAGE=8*1024*1024
MAX_ENTRIES=10000
# Only our own fixed rejection codes may be returned to the catalog/UI.
# Third-party ZIP/XML/EPUB exceptions can include absolute paths and contents.
_SAFE_EPUB_REJECTION_CODES=frozenset({
    'unsafe_xml', 'ambiguous_or_missing_entry', 'oversized_or_symlink_entry',
    'oversized_entry', 'external_resource', 'invalid_member',
    'escaping_member', 'unsafe_cache_path', 'too_many_entries',
    'missing_opf', 'missing_metadata', 'oversized_directory',
    'unsupported_zip_directory',
})

def cover_cache_dir():
    root=cache_dir()/'epub-covers-v1';root.mkdir(parents=True,exist_ok=True);return root

def _xml(data):
    # UTF-16/32 declarations must not bypass the DTD/entity rejection.
    declarations=data.replace(b'\x00',b'').upper()
    if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:raise ValueError('unsafe_xml')
    return ET.fromstring(data)

def _read(archive,name,limit):
    infos=[i for i in archive.infolist() if i.filename==name]
    if len(infos)!=1:raise ValueError('ambiguous_or_missing_entry')
    info=infos[0]
    if info.file_size>limit or info.file_size/max(info.compress_size,1)>1000 or (info.external_attr>>16)&0o170000==0o120000:raise ValueError('oversized_or_symlink_entry')
    with archive.open(info) as handle:
        data=handle.read(limit+1)
    if len(data)>limit:raise ValueError('oversized_entry')
    return data

def _member(base,href):
    parsed=urlsplit(str(href or ''))
    if parsed.scheme or parsed.netloc or parsed.query:raise ValueError('external_resource')
    value=unquote(parsed.path)
    if not value or value.startswith('/') or '\\' in value or '\x00' in value:raise ValueError('invalid_member')
    parts=list(PurePosixPath(base).parts)
    for part in value.split('/'):
        if part=='..':
            if not parts:raise ValueError('escaping_member')
            parts.pop()
        elif part not in {'','.'}:parts.append(part)
    return '/'.join(parts)

def _image_kind(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        if len(data)<24:return None
        width=int.from_bytes(data[16:20],'big');height=int.from_bytes(data[20:24],'big')
        return 'png' if 0<width<=16000 and 0<height<=16000 and width*height<=40000000 else None
    if data.startswith(b'\xff\xd8\xff'):return 'jpg'
    if data.startswith((b'GIF87a',b'GIF89a')):
        if len(data)<10:return None
        width=int.from_bytes(data[6:8],'little');height=int.from_bytes(data[8:10],'little')
        return 'gif' if 0<width<=16000 and 0<height<=16000 and width*height<=40000000 else None
    if data.startswith(b'RIFF') and data[8:12]==b'WEBP':return 'webp'
    if b'<svg' in data[:1024].lower():
        try:
            root=_xml(data)
            if root.tag.rsplit('}',1)[-1]!='svg':return None
            for node in root.iter():
                if node.tag.rsplit('}',1)[-1].lower() in {'script','foreignobject','style'}:return None
                for key,value in node.attrib.items():
                    key=key.rsplit('}',1)[-1].lower()
                    if key.startswith('on') or 'url(' in value.lower():return None
                    if key in {'href','src'} and not value.startswith('#'):return None
            return 'svg'
        except (ValueError,ET.ParseError):return None
    return None

def cached_cover_url(metadata):
    cover=metadata.get('epub_cover') or {}
    name=cover.get('cache_name')
    if not isinstance(name,str) or not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|gif|webp|svg)',name):return None
    path=cover_cache_dir()/name
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_IMAGE:return None
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=name.split('.')[0] or not _image_kind(data):return None
        return path.resolve().as_uri()
    except OSError:return None

def _publish_cover(data,kind):
    name=hashlib.sha256(data).hexdigest()+'.'+kind;path=cover_cache_dir()/name
    if path.is_symlink():raise ValueError('unsafe_cache_path')
    if not path.exists() or path.read_bytes()!=data:
        fd,temp=tempfile.mkstemp(prefix='.cover-',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as handle:handle.write(data)
            os.replace(temp,path)
        finally:
            Path(temp).unlink(missing_ok=True)
    return name

def _bounded_directory(stream):
    """Reject huge central directories BEFORE ZipFile allocates their entries.

    Metadata-only audit accepts ordinary single-volume ZIP, not ZIP64. The
    caller owns this already-open stream; never reopen a mutable pathname.
    """
    stream.seek(0,2);size=stream.tell()
    stream.seek(max(0,size-65557));tail=stream.read(65557)
    offset=tail.rfind(b'PK\x05\x06')
    if offset<0 or len(tail)-offset<22:raise zipfile.BadZipFile()
    _,disk,start_disk,on_disk,count,central_size,central_offset,comment=struct.unpack_from('<4s4H2LH',tail,offset)
    if (disk or start_disk or on_disk!=count or count==65535
            or central_size==0xffffffff or central_offset==0xffffffff
            or offset+22+comment!=len(tail)):
        raise ValueError('unsupported_zip_directory')
    if count>MAX_ENTRIES:raise ValueError('too_many_entries')
    if central_size>8*1024*1024:raise ValueError('oversized_directory')
    stream.seek(0)

def inspect_epub(path, *, metadata_only=False):
    """Inspect an EPUB; metadata_only never reads/publishes a cover or cache.

    For race-safe audit use an already-open binary stream. Path callers retain
    the historic cover behavior unless metadata_only is explicitly requested.
    """
    if metadata_only and isinstance(path,(str,os.PathLike)):
        try:
            with open(path,'rb') as stream:
                return inspect_epub(stream,metadata_only=True)
        except OSError:
            return {'epub_metadata_schema':SCHEMA,'epub_metadata':{},'epub_status':'unreadable_metadata','epub_cover_status':'metadata_unreadable'}
    result={'epub_metadata_schema':SCHEMA,'epub_metadata':{},'epub_status':'unavailable'}
    try:
        if metadata_only:_bounded_directory(path)
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist())>MAX_ENTRIES:raise ValueError('too_many_entries')
            container=_xml(_read(archive,'META-INF/container.xml',MAX_XML))
            roots=[node.attrib.get('full-path') for node in container.iter() if node.tag.rsplit('}',1)[-1]=='rootfile' and node.attrib.get('full-path')]
            if not roots:raise ValueError('missing_opf')
            opf=_member('',roots[0]);package=_xml(_read(archive,opf,MAX_XML))
            metadata=next((node for node in package if node.tag.rsplit('}',1)[-1]=='metadata'),None)
            manifest=next((node for node in package if node.tag.rsplit('}',1)[-1]=='manifest'),None)
            if metadata is None:raise ValueError('missing_metadata')
            refinements={}
            for node in metadata:
                if node.tag.rsplit('}',1)[-1]=='meta' and node.attrib.get('refines'):
                    refinements.setdefault(node.attrib['refines'].lstrip('#'),{})[node.attrib.get('property','')]=''.join(node.itertext()).strip()
            values={};original=[];contributors=[];identifiers=[];series=[]
            cover_id=None
            for node in metadata:
                local=node.tag.rsplit('}',1)[-1];raw_value=''.join(node.itertext()).strip();value=raw_value
                if local=='description':value=description_element(node) or ''
                attrs={key.rsplit('}',1)[-1]:value for key,value in node.attrib.items()}
                original.append({'name':local,'value':raw_value,'attributes':attrs})
                if local=='meta':
                    if attrs.get('name')=='cover':cover_id=attrs.get('content')
                    if attrs.get('name')=='calibre:series':series.append({'name':attrs.get('content',''),'index':None})
                    if attrs.get('property')=='belongs-to-collection' and value:
                        ref=refinements.get(attrs.get('id',''),{});series.append({'name':value,'index':ref.get('group-position'),'type':ref.get('collection-type')})
                    continue
                if not value:continue
                values.setdefault(local,[]).append(value)
                if local in {'creator','contributor'}:
                    ref=refinements.get(attrs.get('id',''),{})
                    contributors.append({'name':value,'kind':local,'role':attrs.get('role') or ref.get('role'),'file_as':attrs.get('file-as') or ref.get('file-as')})
                if local=='identifier':
                    identifiers.append({'value':value,'scheme':attrs.get('scheme') or refinements.get(attrs.get('id',''),{}).get('identifier-type'),'source':'epub'})
            index=next((node.attrib.get('content') for node in metadata if node.attrib.get('name')=='calibre:series_index'),None)
            for row in series:
                if row.get('index') is None and index:row['index']=index
            normalized={'title':(values.get('title') or [None])[0],
                'authors':values.get('creator',[]),'contributors':contributors,'identifiers':identifiers,
                'publishers':values.get('publisher',[]),'dates':values.get('date',[]),'languages':values.get('language',[]),
                'subjects':values.get('subject',[]),'description':'\n\n'.join(values.get('description',[])) or None,
                'rights':values.get('rights',[]),'series':series,'original':original,'source':'epub','opf':opf}
            result.update(epub_metadata=normalized,epub_status='ok')
            if metadata_only:
                result['epub_cover_status']='not_requested'
                return result
            items=list(manifest) if manifest is not None else []
            candidates=[node for node in items if 'cover-image' in node.attrib.get('properties','').split()]
            candidates.extend(node for node in items if node.attrib.get('id')==cover_id and node not in candidates)
            # Guide cover pages are authoritative; only the first spine page
            # is used as a final, explicitly labelled image fallback.
            guide=next((node for node in package if node.tag.rsplit('}',1)[-1]=='guide'),None)
            pages=[]
            if guide is not None:
                pages.extend((node.attrib.get('href'),'guide') for node in guide if 'cover' in node.attrib.get('type','').split())
            spine=next((node for node in package if node.tag.rsplit('}',1)[-1]=='spine'),None)
            if spine is not None and list(spine):
                first=list(spine)[0].attrib.get('idref')
                pages.extend((node.attrib.get('href'),'first_spine') for node in items if node.attrib.get('id')==first)
            result['epub_cover_status']='no_usable_image'
            def publish(member,method):
                data=_read(archive,member,MAX_IMAGE);kind=_image_kind(data)
                if not kind:return False
                result['epub_cover']={'source':'epub','member':member,'cache_name':_publish_cover(data,kind),'method':method}
                result['epub_cover_status']='extracted';return True
            def page_image(member,method,depth=0):
                if depth>1:return False
                markup=_xml(_read(archive,member,MAX_XML))
                refs=[]
                for node in markup.iter():
                    tag=node.tag.rsplit('}',1)[-1].lower()
                    if tag not in {'img','image','object'}:continue
                    attrs={k.rsplit('}',1)[-1]:v for k,v in node.attrib.items()}
                    ref=attrs.get('src') or attrs.get('href') or attrs.get('data')
                    if ref and ref not in refs:refs.append(ref)
                if len(refs)>8:return False
                # A spine page with several images is not an unambiguous cover.
                if method=='first_spine' and len(refs)!=1:return False
                for ref in refs:
                    try:
                        target=_member(str(PurePosixPath(member).parent),ref)
                        if publish(target,method):return True
                        if page_image(target,method,depth+1):return True
                    except (ValueError,OSError,ET.ParseError,KeyError,zipfile.BadZipFile,RuntimeError):continue
                return False
            for item in candidates:
                try:
                    member=_member(str(PurePosixPath(opf).parent),item.attrib.get('href'))
                    if publish(member,'declared') or page_image(member,'declared_page'):break
                except (ValueError,OSError,ET.ParseError,KeyError,zipfile.BadZipFile,RuntimeError):continue
            if 'epub_cover' not in result:
                for href,method in pages[:8]:
                    try:
                        member=_member(str(PurePosixPath(opf).parent),href)
                        if publish(member,method) or page_image(member,method):break
                    except (ValueError,OSError,ET.ParseError,KeyError,zipfile.BadZipFile,RuntimeError):continue
    except (ValueError,OSError,ET.ParseError,KeyError,zipfile.BadZipFile,RuntimeError,EOFError,zlib.error) as exc:
        # ValueError is not a safe text contract: a ZIP/EPUB parser may
        # incorporate filenames or manifest values. Keep our own exact
        # codes only, without ever invoking exception __str__/__repr__.
        code=(exc.args[0] if type(exc) is ValueError
              and len(exc.args)==1 and type(exc.args[0]) is str
              and exc.args[0] in _SAFE_EPUB_REJECTION_CODES
              else 'unreadable_metadata')
        result['epub_status']=code
        result['epub_cover_status']='metadata_unreadable'
    return result

def notice_rows(metadata):
    local=metadata.get('epub_metadata') or {}
    values=[('Contributeurs',[row.get('name','')+(' · '+str(row['role']) if row.get('role') else '') for row in local.get('contributors',[]) if row.get('kind')=='contributor']),('Droits',local.get('rights')),('Éditeur',local.get('publishers') or metadata.get('publisher')),('Publication',local.get('dates') or metadata.get('published_at')),
            ('Langue',local.get('languages') or metadata.get('languages')),('Sujets',local.get('subjects') or metadata.get('subjects')),
            ('Collection / série',[str(row.get('name',''))+(' · '+str(row['index']) if row.get('index') else '') for row in local.get('series',[]) ] or metadata.get('series') or metadata.get('collection')),
            ('Identifiants',[str(row.get('scheme') or 'Identifiant')+' : '+str(row.get('value','')) for row in local.get('identifiers',[])])]
    return [(label,' ; '.join(str(x) for x in value) if isinstance(value,(list,tuple)) else str(value)) for label,value in values if value]
