"""Download validation and atomic publication shared within this package."""
import io
import os
from pathlib import Path
import tempfile
import zipfile
from xml.etree import ElementTree as ET
from cifereca.plugin_api import ProviderError


def validate_document(content, fmt):
    if fmt == 'pdf':
        if not content.startswith(b'%PDF-') or b'%%EOF' not in content[-2048:]:
            raise ProviderError('PDF invalide ou tronqué')
    elif fmt == 'epub':
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                if z.read('mimetype').strip() != b'application/epub+zip' or 'META-INF/container.xml' not in z.namelist():
                    raise ValueError('EPUB container missing')
                if z.testzip() is not None: raise ValueError('ZIP damaged')
                container=ET.fromstring(z.read('META-INF/container.xml'))
                roots=[n.attrib.get('full-path') for n in container.iter() if n.tag.split('}')[-1]=='rootfile']
                if not roots or not all(n in z.namelist() for n in roots): raise ValueError('EPUB package missing')
        except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
            raise ProviderError('EPUB invalide ou tronqué') from exc
    elif fmt == 'acsm':
        try:
            document=ET.fromstring(content)
            if document.tag.split('}')[-1] != 'fulfillmentToken': raise ValueError('ACSM token missing')
        except (ValueError, ET.ParseError) as exc:
            raise ProviderError('ACSM invalide ou réponse HTML') from exc
    else: raise ProviderError('Format numérique non pris en charge')


def atomic_write(path, content):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.part',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as handle:
            handle.write(content);handle.flush();os.fsync(handle.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)
