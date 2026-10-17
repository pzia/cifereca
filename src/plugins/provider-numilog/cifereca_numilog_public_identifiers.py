"""Conservative, offline Numilog product-identifier evidence from visible labels.

The public product page may display paper, EPUB, PDF and generic digital
identifiers. Never treat them as purchased editions or acquisition rights.
No HTTP, cookies, user identity, links or JavaScript are interpreted.
"""
from __future__ import annotations

from html.parser import HTMLParser
import re
import unicodedata

from cifereca_numilog_html import public_book_url

_MAX_HTML_BYTES = 1024 * 1024
_MAX_VISIBLE_TOKENS = 4096
_MAX_TOKEN_LENGTH = 512
_MAX_OBSERVATIONS = 24
_ISBN13 = re.compile(r"(?:97[89])[0-9]{10}\Z")
_LABEL = re.compile(
    r"^(ean|isbn)\s+(papier|numerique|epub|pdf)(?:\s+(sans\s+drm|avec\s+drm))?$"
)
_INLINE = re.compile(
    r"^((?:ean|isbn)\s+(?:papier|numerique|epub|pdf)"
    r"(?:\s+(?:sans\s+drm|avec\s+drm))?)\s*:\s*([0-9 -]{13,24})$"
)


class PublicIdentifierError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__("Numilog public identifiers: " + code)


def _fold(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def _isbn13(value: str) -> str | None:
    digits = value.replace(" ", "").replace("-", "")
    if not _ISBN13.fullmatch(digits):
        return None
    checksum = (10 - sum(
        int(digit) * (1 if index % 2 == 0 else 3)
        for index, digit in enumerate(digits[:12])
    ) % 10) % 10
    return digits if checksum == int(digits[-1]) else None


class _VisibleLabels(HTMLParser):
    """Bounded visible text with a product-title anchor, never whole-page EANs."""

    _IGNORED = frozenset({
        "script", "style", "template", "noscript", "form", "iframe",
        "nav", "footer", "header", "aside", "dialog", "svg", "button",
    })

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []
        self.ignored: list[str] = []
        self._h1_depth = 0
        self.title_end: int | None = None
        self.h1_count = 0

    def handle_starttag(self, tag, attrs):
        if self.ignored:
            if tag in self._IGNORED:
                self.ignored.append(tag)
            return
        if tag in self._IGNORED:
            self.ignored.append(tag)
            return
        if tag == "h1":
            self.h1_count += 1
            self._h1_depth += 1

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag == "h1" and self._h1_depth:
            self._h1_depth -= 1
            self.title_end = len(self.tokens)

    def handle_data(self, data):
        if self.ignored:
            return
        for raw in data.splitlines():
            value = " ".join(raw.split())
            if not value:
                continue
            if len(value) > _MAX_TOKEN_LENGTH:
                continue
            if len(self.tokens) >= _MAX_VISIBLE_TOKENS:
                raise PublicIdentifierError("visible_text_too_large")
            self.tokens.append(value)


# Headings identify the product text area on the observed public pages.
# Generic device/DRM help and recommendations are NOT product evidence.
_STOP_HEADINGS = frozenset({
    "guide des formats", "auteur(s) du livre", "avis clients",
    "suggestions personnalisees", "restez informe(e)",
    "details sur l'accessibilite", "details sur l accessibilite",
})
_MAX_PRODUCT_TOKENS = 320
_MAX_DETAIL_TOKENS = 96


def _product_tokens(reader: _VisibleLabels) -> list[str]:
    """Two bounded areas: product description and the explicit Détails block.

    The HTML layout itself is NOT yet production-qualified. This conservative
    anchor deliberately returns empty for unrecognized markup, rather than
    searching the whole page and mislabelling related titles or generic help.
    """
    if reader.h1_count != 1 or reader.title_end is None:
        return []
    start = reader.title_end
    tokens = reader.tokens
    if start >= len(tokens):
        return []
    limit = min(len(tokens), start + _MAX_PRODUCT_TOKENS)
    detail_index = next(
        (i for i in range(start, limit)
         if _fold(tokens[i]).strip(" :") == "details"),
        None,
    )
    if detail_index is None:
        return []
    summary_end = next(
        (i for i in range(start, detail_index)
         if _fold(tokens[i]).strip(" :") in _STOP_HEADINGS),
        detail_index,
    )
    details_end = next(
        (i for i in range(detail_index + 1, min(limit, detail_index + _MAX_DETAIL_TOKENS))
         if _fold(tokens[i]).strip(" :") in _STOP_HEADINGS),
        min(limit, detail_index + _MAX_DETAIL_TOKENS),
    )
    return tokens[start:summary_end] + tokens[detail_index:details_end]


def _role(label: str) -> tuple[str, str | None] | None:
    match = _LABEL.fullmatch(_fold(label))
    if not match:
        return None
    kind = match.group(2)
    role = {
        "papier": "paper",
        "numerique": "digital_unspecified",
        "epub": "epub",
        "pdf": "pdf",
    }[kind]
    drm = match.group(3)
    if drm and role not in {"epub", "pdf"}:
        return None
    return role, drm.replace(" ", "_") if drm else None


def extract_public_identifiers(html: str, product_url: str) -> dict:
    """Extract only explicit label/valid EAN pairs, never inferred format rights.

    This is a conservative *candidate* parser: labels in real upstream HTML
    remain unqualified until independent live fixtures confirm its structure.
    Multiple conflicting EANs for the same role fail closed.
    """
    if (type(product_url) is not str
            or public_book_url(product_url) != product_url
            or not product_url.startswith("https://www.numilog.com/")):
        raise PublicIdentifierError("invalid_product_url")
    if type(html) is not str or len(html.encode("utf-8", "replace")) > _MAX_HTML_BYTES:
        raise PublicIdentifierError("html_too_large")
    reader = _VisibleLabels()
    reader.feed(html)
    reader.close()
    # Only an unambiguous, bounded product region can supply identifiers;
    # a generic footer/sidebar or another product's recommendation cannot.
    identifiers: dict[str, str] = {}
    evidence: dict[str, dict] = {}
    count = 0
    tokens = _product_tokens(reader)
    for index, text in enumerate(tokens):
        candidate = _fold(text)
        inline = _INLINE.fullmatch(candidate)
        label = inline.group(1) if inline else candidate.rstrip(":").strip()
        matched = _role(label)
        if matched is None:
            continue
        role, drm = matched
        if inline:
            raw_value = inline.group(2)
        else:
            if index + 1 >= len(tokens):
                continue
            raw_value = _fold(tokens[index + 1])
            if not re.fullmatch(r"[0-9 -]{13,24}", raw_value):
                continue
        ean = _isbn13(raw_value)
        if ean is None:
            continue
        count += 1
        if count > _MAX_OBSERVATIONS:
            raise PublicIdentifierError("too_many_identifiers")
        if role in identifiers and identifiers[role] != ean:
            raise PublicIdentifierError("conflicting_identifier")
        if role in evidence and evidence[role]["public_offer_drm_label"] != drm:
            raise PublicIdentifierError("conflicting_identifier")
        identifiers[role] = ean
        evidence[role] = {
            "kind": "observed_public_label",
            "label": " ".join(label.split()),
            "source_url": product_url,
            "observation_scope": "bounded_product_body_or_details",
            "public_offer_format": role if role in {"epub", "pdf"} else None,
            "public_offer_drm_label": drm,
            "owned_format_proven": False,
        }
    return {
        "product_url": product_url,
        "identifiers": identifiers,
        "identifier_evidence": evidence,
    }


def validated_public_identifiers(detail: dict, product_url: str) -> tuple[dict, dict]:
    """Validate evidence again before merging externally supplied detail data."""
    if type(detail) is not dict:
        raise PublicIdentifierError("evidence_conflict")
    identifiers = detail.get("public_identifiers", {})
    evidence = detail.get("identifier_evidence", {})
    allowed = {"paper", "epub", "pdf", "digital_unspecified"}
    if (type(identifiers) is not dict or type(evidence) is not dict
            or set(identifiers) != set(evidence)
            or not set(identifiers).issubset(allowed)):
        raise PublicIdentifierError("evidence_conflict")
    normalized, normalized_evidence = {}, {}
    for role, value in identifiers.items():
        if type(value) is not str or _isbn13(value) != value:
            raise PublicIdentifierError("evidence_conflict")
        proof = evidence[role]
        if type(proof) is not dict:
            raise PublicIdentifierError("evidence_conflict")
        label = proof.get("label")
        assigned = _role(label) if type(label) is str else None
        if (assigned is None or assigned[0] != role
                or proof.get("kind") != "observed_public_label"
                or proof.get("source_url") != product_url
                or proof.get("observation_scope") != "bounded_product_body_or_details"
                or proof.get("public_offer_format") != (role if role in {"epub", "pdf"} else None)
                or proof.get("public_offer_drm_label") != assigned[1]
                or proof.get("owned_format_proven") is not False):
            raise PublicIdentifierError("evidence_conflict")
        normalized[role] = value
        normalized_evidence[role] = {
            "kind": "observed_public_label",
            "label": " ".join(label.split()),
            "source_url": product_url,
            "observation_scope": "bounded_product_body_or_details",
            "public_offer_format": proof["public_offer_format"],
            "public_offer_drm_label": proof["public_offer_drm_label"],
            "owned_format_proven": False,
        }
    return normalized, normalized_evidence
