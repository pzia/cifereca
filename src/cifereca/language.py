from __future__ import annotations

_ALIASES = {
    'fre': 'fr', 'fra': 'fr', 'fr': 'fr',
    'eng': 'en', 'en': 'en',
    'ger': 'de', 'deu': 'de', 'de': 'de',
    'spa': 'es', 'es': 'es',
    'ita': 'it', 'it': 'it',
    'por': 'pt', 'pt': 'pt',
    'dut': 'nl', 'nld': 'nl', 'nl': 'nl',
}

_LABELS_FR = {
    'fr': 'Français', 'en': 'English', 'de': 'Deutsch', 'es': 'Español',
    'it': 'Italiano', 'pt': 'Português', 'nl': 'Nederlands',
}

def normalize_language_tag(value: object) -> str | None:
    text = str(value or '').strip().replace('_', '-')
    if not text:
        return None
    parts = text.split('-')
    primary = _ALIASES.get(parts[0].casefold(), parts[0].casefold())
    if not primary.isalpha() or len(primary) not in {2, 3}:
        return None
    if len(parts) == 1:
        return primary
    rest = []
    for part in parts[1:]:
        if len(part) == 2 and part.isalpha():
            rest.append(part.upper())
        elif len(part) == 4 and part.isalpha():
            rest.append(part.title())
        elif part.isalnum() and 1 <= len(part) <= 8:
            rest.append(part.lower())
        else:
            return None
    return '-'.join([primary, *rest])

def language_label(value: object) -> str:
    tag = normalize_language_tag(value)
    if not tag:
        return str(value or '')
    return _LABELS_FR.get(tag, _LABELS_FR.get(tag.split('-', 1)[0], tag))
