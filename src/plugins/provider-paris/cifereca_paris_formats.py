"""Separate explicit file formats from catalogue bibliographic codes.

No inference from title, description, signed URLs or bibliographic codes.
"""
import re
import unicodedata

from cifereca.plugin_api import normalize_language_tag

ALIASES = {
    'epub':'EPUB', 'application/epub+zip':'EPUB',
    'pdf':'PDF', 'application/pdf':'PDF',
    'kepub':'KEPUB', 'kepub.epub':'KEPUB',
    'mp3':'MP3', 'audio/mpeg':'MP3',
    'm4a':'M4A', 'audio/mp4':'M4A',
    'ogg':'OGG', 'audio/ogg':'OGG',
    'flac':'FLAC', 'audio/flac':'FLAC',
    'wav':'WAV', 'audio/wav':'WAV', 'opus':'OPUS',
}
FORMAT_FIELDS = ('MediaFormat', 'Format', 'MimeType', 'Mimetype', 'ContentType', 'Frmt')
AUDIO = {'MP3', 'M4A', 'OGG', 'FLAC', 'WAV', 'OPUS'}


def _formats(value):
    if isinstance(value, (list, tuple)):
        return [fmt for item in value for fmt in _formats(item)]
    if not isinstance(value, str):
        return []
    value = value.strip().casefold()
    if value in ALIASES:
        return [ALIASES[value]]
    # MIME parameters do not describe extra formats.
    if value.split(';', 1)[0].strip() in ALIASES and '/' in value.split(';', 1)[0]:
        return [ALIASES[value.split(';', 1)[0].strip()]]
    tokens = re.split(r'\s*[,;|]\s*|\s+/\s+|(?<=epub)/(?=pdf)|(?<=pdf)/(?=epub)', value)
    if len(tokens) > 1 and all(token in ALIASES for token in tokens):
        return [ALIASES[token] for token in tokens]
    return []


def explicit_formats(*containers):
    formats, evidence = [], []
    for source, fields in containers:
        if not isinstance(fields, dict):
            continue
        for key in FORMAT_FIELDS:
            found = _formats(fields.get(key))
            if found:
                evidence.append(source + '.' + key)
                for fmt in found:
                    if fmt not in formats:
                        formats.append(fmt)
    return formats, evidence


def projection(resource, raw, scenario, docbase):
    additional = raw.get('AdditionalProperties')
    formats, evidence = explicit_formats(('Resource', resource), ('AdditionalProperties', additional))
    # The scenario is explicit provenance supplied by the catalogue request.
    # EBOOKS can return records under bases other than DILICOM.
    kind = unicodedata.normalize('NFKD', str(resource.get('Type') or '')).casefold()
    kind = ''.join(c for c in kind if not unicodedata.combining(c)).strip()
    digital_types = {'livre numerique', 'livres numeriques', 'ebook', 'livre audio numerique', 'ressource electronique'}
    digital = docbase.upper() == 'DILICOM' or str(scenario).upper() == 'EBOOKS' or kind in digital_types
    metadata = {'available_formats':formats, 'format_evidence':evidence,
                'format_source':'paris_explicit' if formats else 'unknown'}
    language_values = []
    for fields in (resource, additional if isinstance(additional, dict) else {}):
        for key in ('Language', 'Languages', 'Lang', 'Langue', 'LanguageCode'):
            value = fields.get(key)
            if isinstance(value, str):
                language_values.extend(part.strip() for part in re.split(r'[,;|]', value) if part.strip())
            elif isinstance(value, (list, tuple)):
                language_values.extend(str(part).strip() for part in value if str(part).strip())
    languages = list(dict.fromkeys(
        canonical for raw in language_values
        if (canonical := normalize_language_tag(raw)) is not None
    ))
    if languages:
        metadata['languages'] = languages
        metadata['language'] = languages[0]
    code = resource.get('Frmt')
    if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,32}', code.strip()) and not _formats(code):
        metadata['paris_bibliographic_format'] = code.strip()
    if digital:
        content_type = 'audiobook' if ('audio' in kind or (formats and all(fmt in AUDIO for fmt in formats))) else 'ebook'
    else:
        content_type = 'physical'
    # Never choose arbitrarily between contradictory/multiple advertised formats.
    display_format = formats[0] if len(formats) == 1 else None
    return digital, content_type, display_format, metadata
