"""Separate explicit file formats from catalogue bibliographic codes.

No inference from title, description, signed URLs or bibliographic codes.
"""
import re
import unicodedata

from cifereca.plugin_api import (
    ClassificationEvidence, DocumentClassification, normalize_language_tag,
)

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


# Syracuse Frmt is bibliographic, NOT an EPUB/PDF/audio codec. These
# positive *combinations* are observed in Paris's official CATALOGUE replies;
# the catalogue route itself does not prove medium (CATALOGUE mixes both).
_PHYSICAL_PAIRS = {
    "livre": ("MONO", "book"),
    "bd": ("BDTP", "comics"),
    "bande dessinee": ("BDTP", "comics"),
    "musique": ("AUDI", "music"),
    "film": ("AUVI", "video"),
    "revue": ("PERI", "periodical"),
}
_DIGITAL_TYPES = {
    "livre numerique": "book",
    "livres numeriques": "book",
    "ebook": "book",
    "livre audio numerique": "audiobook",
}
_BIBLIO_PHYSICAL_CODES = {"MONO", "BDTP", "AUDI", "AUVI", "PERI"}
_TYPED_DIGITAL_FORMATS = {"EPUB", "KEPUB", "PDF", "MP3", "M4A"}


def _attested(value: str, source: str, raw: str) -> ClassificationEvidence:
    return ClassificationEvidence(value, "attested", source, raw)


def _resource_document_classification(
    resource: dict, docbase: str, formats: list[str],
) -> DocumentClassification:
    """Only positive Type+Frmt+base facts; never infer from CATALOGUE/EBOOKS.

    Unknown/contradictory evidence must not turn into a physical book, a
    digital acquisition offer, or a false branch filter match.
    """
    raw_type = resource.get("Type")
    raw_fmt = resource.get("Frmt")
    kind_text = ""
    if type(raw_type) is str:
        normalized = unicodedata.normalize("NFKD", raw_type)
        kind_text = " ".join(
            "".join(ch for ch in normalized if not unicodedata.combining(ch))
            .casefold().split()
        )
    fmt = raw_fmt.strip().upper() if type(raw_fmt) is str else ""
    base = docbase.strip().upper() if type(docbase) is str else ""
    positive_kind = (
        _DIGITAL_TYPES.get(kind_text) or
        (_PHYSICAL_PAIRS.get(kind_text) or (None, None))[1]
    )
    kind_fact = (
        _attested(positive_kind, "Resource.Type", raw_type)
        if positive_kind else ClassificationEvidence()
    )
    medium = ClassificationEvidence()
    if kind_text in _DIGITAL_TYPES:
        # A proved digital resource on a physical bibliographic manifestation
        # is a conflict, not a digital right or an arbitrary physical result.
        if fmt in _BIBLIO_PHYSICAL_CODES:
            medium = ClassificationEvidence(status="conflict")
        else:
            medium = _attested("digital", "Resource.Type", raw_type)
    elif kind_text in _PHYSICAL_PAIRS:
        expected_code, _ = _PHYSICAL_PAIRS[kind_text]
        if base == "SYRACUSE" and fmt == expected_code:
            medium = _attested(
                "physical", "Resource.Type+Frmt+RscBase",
                f"{raw_type}/{raw_fmt}/{docbase}",
            )
        elif fmt in _BIBLIO_PHYSICAL_CODES | {"MONE"} and (
            fmt != expected_code or base in {"DILICOM", "STORYPLAYR"}
        ):
            medium = ClassificationEvidence(status="conflict")
    # Format metadata from the public catalogue describes an advertised
    # manifestation format, never an actual delivery offer. Do not fill
    # offered_file_formats from a search record.
    typed_format = ClassificationEvidence()
    if medium.value == "digital" and len(formats) == 1:
        if formats[0] in _TYPED_DIGITAL_FORMATS:
            typed_format = _attested(
                formats[0].lower(), "Resource.MediaFormat", formats[0],
            )
    return DocumentClassification(
        medium=medium,
        document_kind=kind_fact,
        digital_format=typed_format,
    )


def projection(resource, raw, scenario, docbase):
    additional = raw.get('AdditionalProperties')
    formats, evidence = explicit_formats(('Resource', resource), ('AdditionalProperties', additional))
    classification = _resource_document_classification(resource, docbase, formats)
    medium = (
        classification.medium.value
        if classification.medium.status == "attested" else "unknown"
    )
    kind = (
        classification.document_kind.value
        if classification.document_kind.status == "attested" else "unknown"
    )
    content_type = (
        "ebook" if medium == "digital" and kind == "book"
        else "audiobook" if medium == "digital" and kind == "audiobook"
        else "printed_book" if medium == "physical" and kind == "book"
        else kind if medium != "unknown"
        else "unknown"
    )
    metadata = {
        'available_formats': formats, 'format_evidence': evidence,
        'format_source': 'paris_explicit' if formats else 'unknown',
        # Keep a consumer-facing category (ebook) independent of the typed
        # documentary *nature* (book) from DocumentClassification/1.
        'document_kind': content_type if content_type == "ebook" else kind,
        'document_medium': medium,
    }
    if str(scenario).upper() in {"CATALOGUE", "EBOOKS"}:
        metadata['search_scenarios'] = [str(scenario).upper()]
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
    # Never choose arbitrarily between contradictory/multiple advertised formats.
    display_format = formats[0] if len(formats) == 1 else None
    return medium, content_type, display_format, metadata, classification
