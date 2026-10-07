"""Small bounded diagnostics: no raw process output, argv or stdin persistence."""
import re


def output_bytes(value):
    if value is None:return b''
    if isinstance(value,bytes):return value
    if isinstance(value,str):return value.encode('utf-8')
    return b''


def operation_family(args):
    pair=tuple(str(x) for x in args[:2])
    allowed={('download','request'),('acsm','return'),('profile','list'),('profile','show'),('loans','list'),('loans','expired')}
    if pair in allowed:return ' '.join(pair)
    if args and args[0] in {'contract','capabilities','status'}:return str(args[0])
    return 'other'


def stderr_summary(data):
    """Classify safe, known causes; omit arbitrary fragments entirely.

    Exposing an unknown stderr line after heuristic token replacement can leak
    credentials or a dumped acquisition request. Only fixed labels are emitted.
    """
    if not data:return 'vide'
    text=data[:65536].decode('utf-8',errors='replace')
    known=['UnicodeEncodeError','UnicodeDecodeError','JSONDecodeError','PermissionError',
           'FileNotFoundError','ConnectionError','TimeoutError','HTTPError','OSError','ValueError','RuntimeError']
    found=[name for name in known if re.search(r'\b'+name+r'\s*:',text)]
    if found:
        result=', '.join(found[:3])
        if any(x.startswith('Unicode') for x in found):
            match=re.search(r"'(ascii|utf-8|latin-1|charmap)' codec",text,re.I)
            if match:result+=' (codec='+match.group(1).lower()+')'
        return result
    status=re.search(r'\bHTTP\s+(4\d\d|5\d\d)\b',text,re.I)
    if status:return 'HTTP '+status.group(1)
    return 'non vide ; contenu non structuré omis'


def safe_message(value):
    """Redact a declared public JSON error message, never an output dump."""
    text=str(value)
    if any(x in text for x in ('<fulfillmentToken','<licenseToken','ContentKeys','content_keys')):
        return 'Erreur Kobo-Adept ; contenu sensible omis'
    text=re.sub(r'https?://[^\s<>"\']+', '<url masquée>',text,flags=re.I)
    text=re.sub(r'(?i)\bBearer\s+[^\s,;]+','Bearer <masqué>',text)
    text=re.sub(r'''(?ix)(["']?(?:password|api_token|token|cookie|secret|authorization|session|readerid|contentkey|content_key|key)["']?\s*[:=]\s*)(?:["'][^"']*["']|[^\s,;}]+)''',r'\1<masqué>',text)
    text=re.sub(r'\b[A-Za-z0-9+/=_-]{40,}\b','<valeur masquée>',text)
    return text[:400]
