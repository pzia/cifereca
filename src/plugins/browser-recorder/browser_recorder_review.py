"""Private, in-RAM identifier review for Browser Recorder P1 #2529.

No filesystem, upload, automatic approval, HTTP replay or ZIP export.
The preview is local only and deliberately NOT an export authorization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import html
import json
import re
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlsplit

from browser_recorder import RecorderError

MAX_CANDIDATES = 256
MAX_TEXT = 32768
_CHOICES = frozenset(("anonymize", "keep", "exclude"))
_SECRETS = re.compile(
    r"password|passwd|secret|session|cookie|authorization|bearer|csrf|xsrf|"
    r"(?:access|refresh|api|private)[_-]?key|token|signature|signed|hash|"
    r"licen[cs]e|acsm|drm|payment|card[_-]?number|cvv|otp|mfa",
    re.IGNORECASE,
)
_IDS = re.compile(
    r"(?:id|uuid|isbn|ean|sku)$|"
    # CamelCase/underscore contact coordinates require individual review too.
    r"(?:phone|telephone|mobile)(?:number|num)?$|"
    r"(?:postalcode|codepostal|zipcode|postcode|zip)$|"
    r"(?:^|[_\-.])(?:uid|pid|sku|isbn|ean|uuid|profile|account|order|"
    r"book|livre|product|edition|exemplaire|user|email|customer|"
    r"commande|purchase|identifiant|phone|telephone|mobile|postal|"
    r"zipcode|postcode|zip|code_postal)(?:$|[_\-.])",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"(?<![\w@])[a-zA-Z0-9.+_-]{1,64}@[a-zA-Z0-9.-]{2,200}\.[a-zA-Z]{2,24}")
_UUID = re.compile(r"(?<![\w-])[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}(?![\w-])")
_JWT = re.compile(r"(?:eyJ[A-Za-z0-9_-]{8,}\.){2}[A-Za-z0-9_-]{8,}")
_PAIRS = re.compile(
    r"""(?P<key>[A-Za-z][A-Za-z0-9_.-]{1,63})\s*(?:=|:)\s*
        (?:(?P<quote>["'])(?P<quoted>[^"'<> \r\n]{1,512})(?P=quote)
        |(?P<plain>[^&\s,;<>}{]{1,512}))""",
    re.VERBOSE,
)
_XML = re.compile(r"<([A-Za-z][\w.:-]{1,63})>\s*([^<>]{1,512})\s*</\1>")
_HTML_INPUT = re.compile(r"<input\b[^>]{0,1024}>", re.IGNORECASE)
_HTML_ATTR = re.compile(r"""([a-zA-Z][\w-]*)\s*=\s*["']([^"'<>]{0,512})["']""")
_AUTH_BEARER = re.compile(
    r"\b(?:Bearer|Basic)\s+([A-Za-z0-9_+./=~-]{6,512})", re.IGNORECASE,
)


def _is_secret(name: str, value: str) -> bool:
    return (bool(_SECRETS.search(name) or _JWT.fullmatch(value))
            or bool(len(value) >= 40 and
                    re.fullmatch(r"[A-Za-z0-9_+\-/=]{40,}", value)))


def _category(name: str, value: str) -> str | None:
    if _EMAIL.fullmatch(value) or "email" in name.lower():
        return "email"
    if _UUID.fullmatch(value):
        return "uuid"
    if not _IDS.search(name):
        return None
    name = name.lower()
    if any(s in name for s in ("account", "profile", "user", "customer", "uid")):
        return "account"
    if any(s in name for s in ("book", "livre", "product", "sku", "isbn", "ean", "pid")):
        return "product"
    if any(s in name for s in ("purchase", "order", "commande")):
        return "order"
    return "identifier"


def _variants(value: str) -> set[str]:
    forms = {value, html.escape(value, quote=True),
             quote(value, safe=""), quote_plus(value, safe=""),
             json.dumps(value, ensure_ascii=True)[1:-1]}
    return {item for item in forms if item}


def _replace(
    text: str, replacements: dict[str, str], *, secret_values=frozenset(),
) -> str:
    """Scrub mandatory secrets even inside unstructured alphanumeric text.

    Boundary matching is useful for ordinary identifiers (BookId 214 must
    not rewrite 1214). It is UNSAFE for an attested password/token: a provider
    can embed the same credential between arbitrary letters or digits.
    Prioritize mandatory secret forms when variant encodings overlap.
    """
    forms = {}
    for original, subst in replacements.items():
        mandatory_secret = original in secret_values
        for form in _variants(original):
            variants = (form, form.lower(), form.upper()) if "%" in form else (form,)
            for variant in variants:
                previous = forms.get(variant)
                if previous is None or (mandatory_secret and not previous[1]):
                    forms[variant] = (subst, mandatory_secret)
    for form in sorted(forms, key=len, reverse=True):
        subst, mandatory_secret = forms[form]
        pattern = re.escape(form)
        if not mandatory_secret:
            pattern = r"(?<![A-Za-z0-9])" + pattern + r"(?![A-Za-z0-9])"
        text = re.sub(pattern, lambda _m, value=subst: value, text)
    return text


@dataclass(repr=False)
class Identifier:
    key: str
    kind: str
    value: str = field(repr=False)
    occurrences: int = 0
    fields: set[str] = field(default_factory=set, repr=False)
    decision: str | None = None

    def __repr__(self):
        return (f"Identifier(key={self.key!r}, kind={self.kind!r}, "
                f"occurrences={self.occurrences}, decision={self.decision!r})")


class IdentifierReview:
    """Independent decisions for every value, mandatory secret exclusion."""

    def __init__(self, trace):
        self.trace = trace
        self._by_value: dict[str, Identifier] = {}
        self._by_key: dict[str, Identifier] = {}
        self._secret_values: set[str] = set()
        self._closed = False
        self._next = 0
        for entry in trace.exchanges:
            self._scan_exchange(entry)

    def __repr__(self):
        return (f"IdentifierReview(candidates={len(self._by_key)}, "
                f"secrets={len(self._secret_values)})")

    def _observe(self, name: str, value, fieldname: str):
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            return
        value = str(value).strip()
        if (not value or len(value) > 512 or value.lower() in
                {"null", "none", "true", "false", "undefined"}
                or any(ord(c) < 32 for c in value)):
            return
        if _is_secret(name, value):
            if len(self._secret_values) >= MAX_CANDIDATES and value not in self._secret_values:
                raise RecorderError("review_limit")
            self._secret_values.add(value)
            candidate = self._by_value.pop(value, None)
            if candidate is not None:
                self._by_key.pop(candidate.key, None)
            return
        if value in self._secret_values:
            return
        kind = _category(name, value)
        if kind is None:
            return
        if value not in self._by_value:
            if self._next >= MAX_CANDIDATES:
                raise RecorderError("review_limit")
            self._next += 1
            candidate = Identifier("I" + str(self._next).zfill(4), kind, value)
            self._by_value[value] = candidate
            self._by_key[candidate.key] = candidate
        candidate = self._by_value[value]
        candidate.occurrences += 1
        if len(candidate.fields) < 10:
            candidate.fields.add(fieldname[:100])

    def _pairs(self, text: str, where: str):
        try:
            items = parse_qsl(text, keep_blank_values=False, max_num_fields=200)
        except ValueError:
            raise RecorderError("review_limit") from None
        for key, value in items:
            self._observe(key, value, where + ":" + key[:40])

    def _json(self, node, where, depth=0, budget=None):
        if budget is None:
            budget = [0]
        budget[0] += 1
        if depth > 16 or budget[0] > 1024:
            raise RecorderError("review_limit")
        if type(node) is dict:
            if len(node) > 256:
                raise RecorderError("review_limit")
            for key, value in node.items():
                if type(key) is not str or len(key) > 128:
                    continue
                if type(value) in (str, int):
                    self._observe(key, value, where + ":" + key[:40])
                else:
                    self._json(value, where + ":" + key[:40], depth + 1, budget)
        elif type(node) is list:
            for value in node:
                self._json(value, where, depth + 1, budget)

    def _text(self, text: str, where: str, mime: str = ""):
        if type(text) is not str or len(text) > MAX_TEXT:
            raise RecorderError("review_limit")
        if "json" in mime:
            try:
                data = json.loads(text)
            except (ValueError, RecursionError):
                pass
            else:
                self._json(data, where)
        if mime == "application/x-www-form-urlencoded":
            self._pairs(text, where)
        for match in _PAIRS.finditer(text):
            self._observe(match.group("key"),
                          html.unescape(unquote(match.group("quoted") or
                                                match.group("plain") or "")),
                          where + ":" + match.group("key")[:40])
        for match in _XML.finditer(text):
            self._observe(match.group(1), match.group(2).strip(), where + ":xml")
        # Form inputs are structured by name/type, not a standalone "value"
        # attribute. An input type=password or name=token is unexportable
        # even if the surrounding markup is otherwise useful for a parser.
        for match in _HTML_INPUT.finditer(text):
            attributes = {
                k.lower(): html.unescape(v)
                for k, v in _HTML_ATTR.findall(match.group())
            }
            if "value" in attributes:
                label = (attributes.get("type") if attributes.get("type") == "password"
                         else attributes.get("name") or "value")
                self._observe(label, attributes["value"], where + ":input")
        # An unstructured Authorization: Bearer VALUE would otherwise only
        # classify the word "Bearer" and leak the token after it.
        for match in _AUTH_BEARER.finditer(text):
            self._observe("access_token", match.group(1), where + ":authorization")
        for pattern, key in ((_EMAIL, "email"), (_UUID, "uuid"),
                             (_JWT, "access_token")):
            for match in pattern.finditer(text):
                self._observe(key, match.group(), where + ":text")

    def _url(self, text, where):
        if type(text) is not str or len(text) > 4096:
            raise RecorderError("review_limit")
        parsed = urlsplit(text)
        self._pairs(parsed.query, where + ":query")
        for part in parsed.path.split("/"):
            if part:
                raw = unquote(part)
                if _UUID.fullmatch(raw) or raw.isdecimal():
                    self._observe("id", raw, where + ":path")
        # Query values were already URL-decoded above. Scanning the raw URL
        # again invents a second "identifier" for its percent-encoded form,
        # breaking one decision = one value across JSON and URL.
        self._text(parsed.path, where + ":pathtext")
        if parsed.fragment:
            # Browser fragments are never HTTP request parameters. A fragment
            # may embed OAuth/session credentials; fail closed on the whole
            # value without offering an option to keep it.
            self._observe("access_token", parsed.fragment, where + ":fragment")

    def _scan_exchange(self, entry):
        prefix = str(entry.sequence)
        self._url(entry.url, prefix + ":url")
        if entry.redirect_to:
            self._url(entry.redirect_to, prefix + ":redirect")
        for direction in ("request", "response"):
            headers = getattr(entry, direction + "_headers", {})
            if len(headers) > 32:
                raise RecorderError("review_limit")
            for k, v in headers.items():
                self._observe(k, v, prefix + ":" + direction + ":header")
            body = getattr(entry, direction + "_body", None)
            if body is not None:
                self._text(body, prefix + ":" + direction + ":body",
                           headers.get("content-type", entry.mime).split(";")[0].lower())

    def candidates(self):
        if self._closed:
            raise RecorderError("review_closed")
        return tuple(self._by_key.values())

    def local_rows(self):
        """Private UI values. DO NOT log or serialize these rows."""
        return [
            {"key": c.key, "value": c.value, "kind": c.kind,
             "occurrences": c.occurrences, "fields": sorted(c.fields),
             "decision": c.decision}
            for c in self.candidates()
        ]

    def decide(self, key: str, action: str):
        if self._closed:
            raise RecorderError("review_closed")
        if key not in self._by_key or action not in _CHOICES:
            raise RecorderError("invalid_decision")
        self._by_key[key].decision = action

    def pending(self) -> int:
        return sum(c.decision is None for c in self.candidates())

    def preview(self):
        """Preview only; never a ready-to-share ZIP or export authorization."""
        if self._closed:
            raise RecorderError("review_closed")
        if self.pending():
            raise RecorderError("review_pending")
        replacements = {value: "[SECRET]" for value in self._secret_values}
        excluded = []
        for candidate in self._by_key.values():
            if candidate.decision == "anonymize":
                replacements[candidate.value] = "PSEUDO_" + candidate.key
            elif candidate.decision == "exclude":
                excluded.append(candidate.value)
        rows = []
        dropped = 0
        for entry in self.trace.exchanges:
            original = [entry.url, entry.redirect_to or "",
                        entry.request_body or "", entry.response_body or ""]
            original.extend(entry.request_headers.values())
            original.extend(entry.response_headers.values())
            if any(any(form in source for form in _variants(value))
                   for value in excluded for source in original):
                dropped += 1
                continue
            def clean(value):
                return None if value is None else _replace(
                    str(value), replacements, secret_values=self._secret_values,
                )
            rows.append({
                "exchange_id": entry.sequence, "method": entry.method,
                "page_id": entry.page_id, "started_ms": entry.started_ms,
                "duration_ms": entry.duration_ms, "url": clean(entry.url),
                "redirect_to": clean(entry.redirect_to), "status": entry.status,
                "mime": entry.mime, "request_body": clean(entry.request_body),
                "response_body": clean(entry.response_body),
                "request_body_state": entry.request_body_state,
                "response_body_state": entry.response_body_state,
                "request_headers": {k: clean(v) for k, v in entry.request_headers.items()},
                "response_headers": {k: clean(v) for k, v in entry.response_headers.items()},
                "network_error": entry.network_error,
            })
        return {
            "schema": "cifereca.browser-recorder.review-preview/1",
            "approved_for_export": False,
            "exchanges": rows, "dropped_exchanges": dropped,
            "secrets_replaced": len(self._secret_values),
            "decisions": [{"key": c.key, "kind": c.kind, "action": c.decision}
                          for c in self._by_key.values()],
            "warnings": ["Prévisualisation locale, non exportable automatiquement.",
                         "Détection incomplète possible : revue humaine obligatoire."],
        }

    def destroy(self):
        if self._closed:
            return
        for candidate in self._by_key.values():
            candidate.value = ""
            candidate.fields.clear()
        self._by_value.clear()
        self._by_key.clear()
        self._secret_values.clear()
        self._closed = True
