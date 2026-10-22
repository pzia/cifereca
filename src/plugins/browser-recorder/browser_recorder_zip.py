"""Bounded, offline ZIP planner for reviewed Browser Recorder traces (P1 #2530).

A ZIP plan is never a network action. Its explicit save method requires the
caller to have inspected the exact frozen bytes. A real GTK export control and
the integrated Chromium security acceptance test are still separate work.
"""
from __future__ import annotations

import hashlib
from html import escape
from html.parser import HTMLParser
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
from xml.etree import ElementTree as ET
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from browser_recorder import RecorderError, SAFE_SEGMENTS, site_origin
from browser_recorder_review import _SECRETS, _IDS, _JWT, _variants
from browser_recorder_devkit import core_contract_kit
from browser_recorder_download import _open_parent_without_symlinks
from browser_recorder_http import _BODY_REASONS

ZIP_SCHEMA = "cifereca.browser-recorder.approved-zip/1"
MAX_EXCHANGES = 240
MAX_MEMBERS = 600
MAX_UNCOMPRESSED = 4 * 1024 * 1024
MAX_ZIP_BYTES = 2 * 1024 * 1024
MAX_BODY_OUT = 16000
MAX_ROUTE_LABELS = 4096
_FILENAME = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}[.]zip$")
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,80}$")
_SAFE_PATH = SAFE_SEGMENTS | frozenset((
    "api", "v3", "v4", "items", "entries", "products", "details", "authors",
    "pagination", "next", "results", "opds", "fetch", "search.php",
    # Stable technical route terms, not arbitrary provider-controlled segments.
    "my-books", "download-options", "download", "downloads", "editions",
    "records", "formats", "holdings", "filters", "availability",
))
_SAFE_CLASS = frozenset((
    "book", "books", "card", "item", "result", "results", "title", "author",
    "price", "container", "row", "list", "catalog", "catalogue", "metadata",
    "detail", "details", "cover", "format", "pagination",
    "book-card", "download-button", "next-page", "search-form",
))


# Provider-controlled field names can themselves contain account IDs or
# credentials even when their VALUES are masked. Keep normal schema labels
# but omit suspicious dynamic names from an externally shared ZIP.
_STANDARD_SECRET_FIELDS = frozenset((
    "password", "passwd", "secret", "session", "sessionid", "cookie",
    "authorization", "bearer", "csrf", "csrftoken", "xsrf", "xsrftoken",
    "accesstoken", "refreshtoken", "apikey", "privatekey", "authkey",
    "token", "signature", "signed", "hash", "license", "licence", "acsm",
    "drm", "payment", "cardnumber", "cvv", "otp", "totp", "mfa",
    "pin", "pincode", "passcode", "verificationcode",
    "securitycode", "accesscode",
))


# These are *values* whose exact semantics are generic to HTTP/catalogue
# protocols, not arbitrary provider data. Never apply this to free text,
# personal identifiers or URL signatures.
_PUBLIC_ENUMS = {
    "format": frozenset(("epub", "kepub", "pdf", "mobi", "html", "xml",
                         "json", "txt", "audiobook")),
    "sort": frozenset(("title", "author", "date", "updated", "relevance",
                       "price", "asc", "desc")),
    "order": frozenset(("asc", "desc")),
    "direction": frozenset(("asc", "desc")),
    "availability": frozenset(("ready", "available", "unavailable",
                               "borrowed", "pending", "expired")),
    "status": frozenset(("ready", "available", "unavailable", "pending",
                         "completed", "failed", "success", "error",
                         "active", "inactive")),
    "state": frozenset(("ready", "available", "unavailable", "pending",
                        "completed", "failed", "active", "inactive")),
    "type": frozenset(("ebook", "audiobook", "book", "loan", "purchase")),
    "mediatype": frozenset(("application/epub+zip", "application/pdf")),
}
_SAFE_HTML_SELECTORS = frozenset((
    "search-form", "download-button", "next-page", "book-card",
    "results-list", "search-input", "book-detail",
))


def _public_enum(name: str, value: str) -> bool:
    return (type(name) is str and type(value) is str
            and value.casefold() in _PUBLIC_ENUMS.get(name.casefold(), ()))


def _paired_pagination(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Preserve numeric pagination only when two independent fields agree.

    Isolated short numbers can be PINs under misleading keys. Canonical
    decimal limits avoid accidentally treating 0007 as a page counter.
    """
    fields: dict[str, list[str]] = {}
    for key, value in pairs:
        if key.casefold() in {"page", "offset", "limit"}:
            fields.setdefault(key.casefold(), []).append(value)
    limits = fields.get("limit", [])
    if len(limits) != 1 or not limits[0].isascii() or not limits[0].isdecimal():
        return {}
    limit = limits[0]
    if not 1 <= len(limit) <= 3 or str(int(limit)) != limit or not 1 <= int(limit) <= 200:
        return {}
    accepted = {}
    for key, ceiling in (("page", 10000), ("offset", 100000)):
        values = fields.get(key, [])
        if len(values) != 1:
            continue
        value = values[0]
        if (value.isascii() and value.isdecimal() and len(value) <= 6
                and str(int(value)) == value and 0 <= int(value) <= ceiling):
            accepted[key] = value
    if accepted:
        accepted["limit"] = limit
    return accepted


def _safe_field_key(key: str) -> bool:
    if type(key) is not str or not _FIELD.fullmatch(key):
        return False
    # isbn13 remains useful; unreviewed long numeric/hex key fragments do not.
    if re.search(r"[0-9]{5,}", key) or re.search(
        r"(?i)(?:^|[._:])[a-f0-9]{12,}(?:$|[._:])", key,
    ):
        return False
    normalized = re.sub(r"[_.:-]", "", key.casefold())
    if _SECRETS.search(key) and normalized not in _STANDARD_SECRET_FIELDS:
        return False
    return True


def _safe_url(url: str, approved: frozenset[str],
              allowed_origins: frozenset[str],
              route_labels: dict[str, str] | None = None) -> str:
    if type(url) is not str or len(url) > 4096:
        raise RecorderError("bundle_invalid_url")
    try:
        p = urlsplit(url)
        if p.username or p.password or p.fragment and len(p.fragment) > 4096:
            raise ValueError("unsafe")
        if p.scheme or p.netloc:
            try:
                observed_origin = site_origin(url)
            except RecorderError:
                return "[UNSAFE_TARGET]"
            if observed_origin not in allowed_origins:
                # A browser may visit a third-party identity provider as part
                # of the user's journey; its unapproved URL is not shareable.
                return "[OUT_OF_SCOPE]"
        # Unknown segments must stay private, but collapsing every one to
        # the same marker makes distinct endpoints indistinguishable. This
        # mapping exists only while preparing ONE bundle, never in its plan
        # or members; it is not a hash/dictionary oracle for original values.
        if route_labels is None:
            route_labels = {}

        def segment(part):
            decoded = unquote(part, errors="strict")
            if not part or decoded.casefold() in _SAFE_PATH:
                return part
            if decoded in {"[SECRET]", "[UNREVIEWED]"}:
                return "~redacted~"
            if decoded in approved:
                # A reviewed value may include '/', '?', '#' or '%'. Keep
                # it in a SINGLE segment, including when URL-encoded input
                # and JSON bodies use different representations.
                return quote(decoded, safe="")
            if decoded not in route_labels:
                if len(route_labels) >= MAX_ROUTE_LABELS:
                    raise RecorderError("bundle_route_limit")
                number = len(route_labels) + 1
                label = f"~route-{number:04d}~"
                while label in approved or label in route_labels.values():
                    number += 1
                    label = f"~route-{number:04d}~"
                route_labels[decoded] = label
            return route_labels[decoded]

        path = "/".join(segment(part) for part in p.path.split("/"))
        pairs = parse_qsl(p.query, keep_blank_values=True, max_num_fields=120)
        pagination = _paired_pagination(pairs)
        query = []
        for key, value in pairs:
            if not _safe_field_key(key):
                # An unreviewed field NAME must not disclose private material.
                continue
            if _SECRETS.search(key):
                query.append((key, "[SECRET]"))
            elif value in approved:
                query.append((key, value))
            elif key.casefold() in pagination and value == pagination[key.casefold()]:
                query.append((key, value))
            elif _public_enum(key, value):
                query.append((key, value))
            else:
                query.append((key, "[UNREVIEWED]"))
        return urlunsplit((p.scheme, p.netloc, path,
                           urlencode(query, doseq=False), ""))
    except (ValueError, UnicodeError, TypeError):
        raise RecorderError("bundle_invalid_url") from None


class _MarkupSkeleton(HTMLParser):
    """Keep DOM selectors/element nesting, not private text/attributes."""

    def __init__(self, approved=frozenset()):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.budget = 0
        self.approved = approved

    def _emit(self, text: str):
        self.budget += len(text)
        if self.budget > MAX_BODY_OUT:
            raise RecorderError("bundle_markup_limit")
        self.parts.append(text)

    def handle_starttag(self, tag, attrs):
        if (not re.fullmatch(r"[a-z][a-z0-9:-]{0,40}", tag)
                or not _safe_field_key(tag)):
            # Tag names are provider-controlled too; a custom element may
            # embed a private ID/token even when its text and attributes
            # are safely reduced to a structure-only markup skeleton.
            return
        projected = []
        for key, value in attrs[:32]:
            if key in {"class", "rel"} and value:
                tokens = value.split()
                allowed = [token for token in tokens
                           if token.casefold() in _SAFE_CLASS]
                if allowed:
                    projected.append(key + '="' + escape(" ".join(allowed)) + '"')
            elif key in {"id", "data-testid"} and value:
                if value.casefold() in _SAFE_HTML_SELECTORS:
                    projected.append(key + '="' + escape(value) + '"')
            elif key in {"type", "name", "role"} and value:
                # All attribute values can be identifiers. Keep only
                # generic element types; never input value, action, href,
                # onclick, style, data-*, placeholder or autofocus.
                generic = {"text", "password", "hidden", "button", "submit",
                           "checkbox", "radio", "form", "book", "title",
                           "search", "query", "page", "limit", "sort", "format"}
                if value.lower() in generic:
                    projected.append(key + '="' + escape(value.lower()) + '"')
        self._emit("<" + tag + (" " + " ".join(projected) if projected else "") + ">")

    def handle_endtag(self, tag):
        if (re.fullmatch(r"[a-z][a-z0-9:-]{0,40}", tag)
                and _safe_field_key(tag)):
            self._emit("</" + tag + ">")

    def handle_data(self, data):
        value = data.strip()
        if value:
            # A complete text node may contain an explicitly reviewed value.
            # Never publish a substring of mixed, unreviewed private prose.
            self._emit(escape(value) if value in self.approved else "[TEXT]")

    def handle_comment(self, data):
        pass

    def handle_entityref(self, name):
        pass


def _json_shape(node, *, name="", approved=frozenset(), depth=0, counter=None):
    if counter is None:
        counter = [0]
    counter[0] += 1
    if depth > 16 or counter[0] > 1600:
        raise RecorderError("bundle_structure_limit")
    if _SECRETS.search(name):
        return "[SECRET]"
    if type(node) is dict:
        if len(node) > 256:
            raise RecorderError("bundle_structure_limit")
        result = {}
        for k, v in node.items():
            if not _safe_field_key(k):
                continue
            result[k] = _json_shape(v, name=k, approved=approved,
                                    depth=depth+1, counter=counter)
        return result
    if type(node) is list:
        return [_json_shape(x, name=name, approved=approved,
                            depth=depth+1, counter=counter)
                for x in node[:256]]
    if type(node) in (int, float) and not isinstance(node, bool):
        if type(node) is float and not math.isfinite(node):
            return "[NUMBER]"
        # Unfamiliar short numeric fields can carry PINs, OTPs and account
        # fragments just as longer numbers can. Unlike catalogue counters
        # and years, their semantic role was never attested. A value approved
        # elsewhere (e.g. bookId 214) MUST NOT authorize it under a different,
        # non-identifier key.
        if _IDS.search(name):
            return node if str(node) in approved else "[UNREVIEWED_ID]"
        safe_public_counters = {
            "year", "price", "count", "total", "page", "pages",
            "limit", "offset",
        }
        if name.casefold() in safe_public_counters and abs(node) < 10_000:
            return node
        return "[NUMBER]"
    if node is None or type(node) is bool:
        return node
    if type(node) is str:
        if node == "[SECRET]" or node == "[UNREVIEWED]":
            return node
        if node in approved or _public_enum(name, node):
            return node
        # Preserve JSON field names/types and relation of approved values,
        # but do not publish unreviewed free text by default.
        return "[UNREVIEWED_ID]" if _IDS.search(name) else "[TEXT]"
    return "[OMITTED]"


def _xml_structure(raw: str, approved: frozenset[str]) -> str:
    """Project bounded XML, preserving case and reviewed scalar relations.

    No DTD, entity declarations, external resolution, comments or processing
    instructions. Unknown namespace URIs are labels, never original URLs.
    """
    if len(raw) > 32768 or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        raise RecorderError("bundle_xml_unsupported")
    root = ET.fromstring(raw)
    namespaces = {}
    original_namespaces = {
        key[1:].split("}", 1)[0]
        for node in root.iter() for key in (node.tag, *node.attrib)
        if isinstance(key, str) and key.startswith("{")
    }
    public_namespaces = {
        "http://www.w3.org/XML/1998/namespace",
        "http://www.w3.org/2005/Atom",
        "http://purl.org/dc/elements/1.1/",
        "http://purl.org/dc/terms/",
        "http://opds-spec.org/2010/catalog",
    }
    count = [0]

    def name(original):
        if not isinstance(original, str):
            return None, ""
        namespace, local = "", original
        if original.startswith("{"):
            namespace, local = original[1:].split("}", 1)
        if not _safe_field_key(local):
            return None, local
        if namespace and namespace not in public_namespaces:
            if namespace not in namespaces:
                number = len(namespaces) + 1
                label = f"urn:cifereca:xml-namespace-{number:04d}"
                while label in original_namespaces or label in namespaces.values():
                    number += 1
                    label = f"urn:cifereca:xml-namespace-{number:04d}"
                namespaces[namespace] = label
            namespace = namespaces[namespace]
        return ("{" + namespace + "}" + local if namespace else local), local

    def scalar(text, local, secret):
        if text is None or not text.strip():
            return None
        value = text.strip()
        if secret or _SECRETS.search(local):
            return "[SECRET]"
        if value in approved or value in {"[SECRET]", "[UNREVIEWED]"} or _public_enum(local, value):
            return value
        return "[UNREVIEWED_ID]" if _IDS.search(local) else "[TEXT]"

    def project(node, depth=0, secret=False):
        count[0] += 1
        if depth > 16 or count[0] > 1600 or len(node.attrib) > 32:
            raise RecorderError("bundle_structure_limit")
        tag, local = name(node.tag)
        if tag is None:
            return None
        secret = secret or bool(_SECRETS.search(local))
        output = ET.Element(tag)
        for key, value in node.attrib.items():
            attribute, label = name(key)
            if attribute is not None:
                output.set(attribute, scalar(value, label, secret) or "")
        output.text = scalar(node.text, local, secret)
        for child in node:
            projected = project(child, depth+1, secret)
            if projected is not None:
                # Mixed free prose is not approved merely because one of
                # its child elements carries a reviewed identifier.
                projected.tail = scalar(child.tail, local, secret)
                output.append(projected)
        return output

    output = project(root)
    if output is None:
        raise RecorderError("bundle_xml_unsupported")
    serialized = ET.tostring(output, encoding="unicode")
    if len(serialized.encode("utf-8")) > MAX_BODY_OUT:
        raise RecorderError("bundle_markup_limit")
    return serialized


# Allowlisted producer states only; provider text must not become a state.
_CAPTURE_STATES = frozenset((
    "body_omitted", "captured", "binary_skipped", "truncated",
    "redirect", "network_error", "unfinished", "pending",
))


def _body(raw, mime, approved):
    if type(raw) is not str:
        return None, "body_omitted"
    if mime.endswith("+json") or "json" in mime:
        try:
            node = json.loads(raw)
            safe = _json_shape(node, approved=approved)
            serialized = json.dumps(safe, ensure_ascii=True, separators=(",", ":"))
            if len(serialized.encode("utf-8")) > MAX_BODY_OUT:
                return None, "truncated"
            return serialized, "captured_json_structure"
        except (ValueError, TypeError, RecursionError, RecorderError):
            return None, "body_omitted"
    if mime in {"text/xml", "application/xml"} or (
            mime.endswith("+xml") and mime != "application/xhtml+xml"):
        try:
            return _xml_structure(raw, approved), "captured_xml_structure"
        except (ET.ParseError, ValueError, TypeError, RecursionError, RecorderError):
            return None, "body_omitted"
    if mime in {"text/html", "application/xhtml+xml"}:
        try:
            parser = _MarkupSkeleton(approved)
            parser.feed(raw)
            return "".join(parser.parts), "captured_markup_structure"
        except (ValueError, TypeError, RecorderError):
            return None, "body_omitted"
    if mime == "application/x-www-form-urlencoded":
        try:
            pairs = parse_qsl(raw, keep_blank_values=True, max_num_fields=120)
        except ValueError:
            return None, "body_omitted"
        return json.dumps([
            {"field": k, "value": ("[SECRET]" if _SECRETS.search(k)
                                  else v if v in approved
                                  else "[UNREVIEWED]")}
            # The field NAME can contain private account IDs, signed tokens
            # or unreviewed dynamic fragments. JSON bodies and query strings
            # already apply this same structural whitelist.
            for k, v in pairs if _safe_field_key(k)
        ], ensure_ascii=True), "captured_form_structure"
    if mime == "text/plain":
        # Some HTTP APIs label JSON as text/plain. Retain its *reviewed*
        # structure, not arbitrary prose, XML licenses, or raw text. Reuse
        # the exact JSON projection and output limits used for proper MIME.
        try:
            bounded = len(raw.encode("utf-8")) <= 32768
        except UnicodeError:
            return None, "body_omitted"
        if bounded and raw.lstrip().startswith(("{", "[")):
            return _body(raw, "application/json", approved)
    # Unknown plain text may contain licenses, auth material or private prose.
    return None, "body_omitted"


def _safe_headers(headers):
    """Export only structural media types and numeric rate-limit evidence.

    A header's *name* being technical is not a confidentiality decision.
    Arbitrary values such as X-RateLimit-Limit: PRIVATE_TOKEN or MIME
    parameters containing private identifiers must never reach a ZIP.
    """
    if type(headers) is not dict:
        return {}
    result = {}
    mime = re.compile(
        r"(?:application|text)/(?:[a-z0-9.+-]{1,60})", re.ASCII,
    )
    known_types = {
        "application/json", "application/xml", "application/javascript",
        "application/x-www-form-urlencoded", "application/xhtml+xml",
        "text/html", "text/plain", "text/xml", "text/json",
        "text/javascript",
    }

    def structural_type(value):
        if not mime.fullmatch(value):
            return None
        if value in known_types:
            return value
        # RFC 6839 suffixes convey a safe syntax family, but arbitrary
        # vendor subtype bytes could themselves contain private account
        # identifiers. Never serialize those unreviewed raw subtype values.
        if value.startswith("application/") and value.endswith("+json"):
            return "application/*+json"
        if value.startswith("application/") and value.endswith("+xml"):
            return "application/*+xml"
        return None

    for key, value in headers.items():
        if type(key) is not str or type(value) is not str or len(value) > 256:
            continue
        if key == "content-type":
            base = value.split(";", 1)[0].strip().lower()
            projected = structural_type(base)
            if projected is not None:
                result[key] = projected
        elif key == "accept":
            # Explicitly project only syntax families, not a provider-chosen
            # subtype, profile, boundary or other unreviewed identifier.
            parts = [v.split(";", 1)[0].strip().lower()
                     for v in value.split(",")]
            projected = [
                "*/*" if part == "*/*" else structural_type(part)
                for part in parts
            ]
            if 1 <= len(projected) <= 12 and all(x is not None for x in projected):
                result[key] = ", ".join(projected)
        elif key in {"retry-after", "x-ratelimit-limit", "x-ratelimit-remaining"}:
            if re.fullmatch(r"[0-9]{1,9}", value):
                # A technical-looking header can carry a short private code.
                # Preserve only its structural presence, never raw digits.
                result[key] = "[NUMBER]"
    return result


def _member_bytes(payload):
    return json.dumps(payload, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":")).encode("ascii")


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    if len(members) > MAX_MEMBERS or sum(len(x) for x in members.values()) > MAX_UNCOMPRESSED:
        raise RecorderError("bundle_limit")
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED, compresslevel=6) as zipfile:
        for name, body in sorted(members.items()):
            if (not re.fullmatch(r"[A-Za-z0-9_.\-/]{1,110}", name)
                    or ".." in name or name.startswith("/")
                    or not isinstance(body, bytes)):
                raise RecorderError("bundle_invalid_member")
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            zipfile.writestr(info, body)
    result = stream.getvalue()
    if len(result) > MAX_ZIP_BYTES:
        raise RecorderError("bundle_limit")
    return result


class ReviewedBundlePlan:
    """Immutable frozen ZIP bytes; the preview must precede explicit saving."""

    def __init__(self, blob: bytes, members: dict[str, bytes]):
        self._blob = blob
        self._members = dict(members)
        self._viewed = False

    def __repr__(self):
        return f"ReviewedBundlePlan(members={len(self._members)}, bytes={len(self._blob)})"

    def preview(self):
        """Reveal the EXACT frozen UTF-8 members, not only a file count."""
        self._viewed = True
        # This summary comes from the frozen manifest member, not a second
        # calculation that could disagree with the bytes about to be saved.
        coverage = json.loads(self._members["manifest.json"]).get("body_coverage", {})
        warning = "Revue manuelle obligatoire : risque de données résiduelles."
        if coverage.get("exported_structural_bodies") == 0:
            warning += (
                " ZIP sans aucun corps HTTP : impossible de reconstituer "
                "les réponses."
            )
        return {
            "schema": ZIP_SCHEMA,
            "members": [
                {"name": name, "bytes": len(body),
                 "sha256": hashlib.sha256(body).hexdigest(),
                 "text": body.decode("utf-8", "strict")}
                for name, body in sorted(self._members.items())
            ],
            "bytes": len(self._blob),
            "sha256": hashlib.sha256(self._blob).hexdigest(),
            "warning": warning,
            "body_coverage": coverage,
        }

    def save(self, directory: Path, *, user_confirmed: bool = False):
        if not self._viewed or user_confirmed is not True:
            raise RecorderError("export_unconfirmed")
        directory = Path(directory)
        if directory.is_symlink():
            raise RecorderError("unsafe_directory")
        name = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc,
        ).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4) + ".zip"
        if not _FILENAME.fullmatch(name):
            raise RecorderError("bundle_invalid_filename")
        folder_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0))
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0))
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            dir_fd = os.open(directory, folder_flags)
            try:
                fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(self._blob)
                        stream.flush()
                        os.fsync(stream.fileno())
                except BaseException:
                    # No incomplete artifact may masquerade as an exported
                    # archive after a disk-full/flush/fsync interruption.
                    # Unlink using the same no-follow directory descriptor.
                    try:
                        os.unlink(name, dir_fd=dir_fd)
                    except OSError:
                        pass
                    raise
            finally:
                os.close(dir_fd)
        except OSError:
            raise RecorderError("save_failed") from None
        return name



    def save_as(self, path: Path, *, user_confirmed: bool = False):
        """Save to a GTK-chosen absolute filename; never overwrite or follow links.

        The reviewed plan's exact in-memory bytes are unchanged. A failed or
        cancelled chooser cannot write anything. Unlike save(), the directory
        must already exist, since the user chose it explicitly in the dialog.
        """
        if not self._viewed or user_confirmed is not True:
            raise RecorderError("export_unconfirmed")
        path = Path(path)
        name = path.name
        if (not path.is_absolute()
                or re.fullmatch(r"[\w][\w .-]{0,98}\.zip", name) is None
                or ".." in name or ".." in path.parts
                or path.parent.is_symlink()):
            raise RecorderError("bundle_invalid_filename")
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            # Reject symlinks in *every* chosen parent component, not only
            # the final directory. This helper never visits network sites.
            folder = _open_parent_without_symlinks(path.parent)
            try:
                info = os.fstat(folder)
                if (not stat.S_ISDIR(info.st_mode)
                        or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) & 0o022):
                    raise RecorderError("unsafe_directory")
                output = os.open(name, flags, 0o600, dir_fd=folder)
                try:
                    with os.fdopen(output, "wb") as stream:
                        stream.write(self._blob)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.fsync(folder)
                except BaseException:
                    try:
                        os.unlink(name, dir_fd=folder)
                    except OSError:
                        pass
                    raise
            finally:
                os.close(folder)
        except OSError:
            raise RecorderError("save_failed") from None
        return name

def prepare_reviewed_zip(review) -> ReviewedBundlePlan:
    """Read ONLY decisions already taken; unknown material is structurally omitted.

    This stage is deliberately not called by the GTK page: a future export
    button must display the plan preview and require explicit confirmation.
    """
    if review.pending():
        raise RecorderError("review_pending")
    preview = review.preview()
    if preview["approved_for_export"] is not False:
        raise RecorderError("bundle_invalid_preview")
    if len(preview["exchanges"]) > MAX_EXCHANGES:
        raise RecorderError("bundle_limit")
    approved = frozenset(
        [c.value for c in review.candidates() if c.decision == "keep"]
        + ["PSEUDO_" + c.key for c in review.candidates()
           if c.decision == "anonymize"]
    )
    allowed_origins = frozenset(review.trace.origins)
    route_labels: dict[str, str] = {}
    files = {}
    timeline = []
    masked_exchange_routes = 0
    for entry in preview["exchanges"]:
        eid = entry["exchange_id"]
        if type(eid) is not int or not 1 <= eid <= MAX_EXCHANGES:
            raise RecorderError("bundle_invalid_exchange")
        path = f"exchanges/{eid:04d}.json"
        if path in files:
            raise RecorderError("bundle_duplicate_exchange")
        outgoing = {
            "exchange_id": eid, "method": entry["method"],
            "page_id": entry["page_id"], "started_ms": entry["started_ms"],
            "duration_ms": entry["duration_ms"], "status": entry["status"],
            "url": _safe_url(entry["url"], approved, allowed_origins, route_labels),
            "redirect_to": (_safe_url(entry["redirect_to"], approved, allowed_origins, route_labels)
                            if entry["redirect_to"] else None),
            "mime": _safe_headers({"content-type": entry["mime"]}).get("content-type", ""),
            "network_error": entry["network_error"],
            "metadata_only": entry.get("metadata_only", False),
            "request_headers": _safe_headers(entry["request_headers"]),
            "response_headers": _safe_headers(entry["response_headers"]),
            "bodies": {},
        }
        if "~route-" in outgoing["url"]:
            masked_exchange_routes += 1
        for direction in ("request", "response"):
            body, state = _body(entry[direction + "_body"],
                                (entry[direction + "_headers"].get("content-type")
                                 or entry["mime"]).split(";", 1)[0].lower(),
                                approved)
            raw_state = entry.get(direction + "_body_state")
            capture_state = (
                raw_state if type(raw_state) is str
                and raw_state in _CAPTURE_STATES else "unknown"
            )
            reason = entry.get(direction + "_body_reason")
            capture_reason = (
                reason if type(reason) is str and reason in _BODY_REASONS
                else "unknown"
            )
            outgoing["bodies"][direction] = {
                "state": state, "capture_state": capture_state,
                "capture_reason": capture_reason, "file": None,
            }
            if body is not None:
                body_path = f"bodies/{eid:04d}-{direction}.txt"
                files[body_path] = body.encode("utf-8")
                outgoing["bodies"][direction]["file"] = body_path
        files[path] = _member_bytes(outgoing)
        timeline.append({"id": eid, "exchange": path,
                         "start_ms": outgoing["started_ms"],
                         "status": outgoing["status"]})
    files["timeline.json"] = _member_bytes(timeline)
    files["identifiers.json"] = _member_bytes(preview["decisions"])
    # Publish counts, never unreviewed URLs, hosts, header values or bodies.
    # The same bounded keys are present for zero-count categories so a LLM
    # can distinguish "observed none" from "unknown/unreported".
    omission_codes = (
        "out_of_scope", "limit", "binary_skipped", "oversize",
        "unknown_size", "compressed", "network_error", "paused",
    )
    capture_omissions = {}
    for code in omission_codes:
        count = review.trace.omitted.get(code, 0)
        if type(count) is not int or not 0 <= count <= 1_000_000:
            raise RecorderError("bundle_invalid_omissions")
        capture_omissions[code] = count

    # Count the *retained* text fields before review, those still present
    # after explicit review exclusions, and those actually serialized as
    # structural ZIP members. No provider values are published here.
    captured = sum(
        type(getattr(item, direction + "_body")) is str
        for item in review.trace.exchanges
        for direction in ("request", "response")
    )
    reviewed = sum(
        type(item[direction + "_body"]) is str
        for item in preview["exchanges"]
        for direction in ("request", "response")
    )
    exported = sum(name.startswith("bodies/") for name in files)
    if not 0 <= exported <= reviewed <= captured <= 2 * len(review.trace.exchanges):
        raise RecorderError("bundle_invalid_coverage")
    # Closed diagnostic vocabulary: explains why observed exchanges might
    # have no useful bodies, without leaking an origin, path or payload.
    capture_reasons = {code: 0 for code in sorted(_BODY_REASONS)}
    for item in review.trace.exchanges:
        for direction in ("request", "response"):
            reason = getattr(item, direction + "_body_reason", "unknown")
            capture_reasons[reason if type(reason) is str and reason in
                            _BODY_REASONS else "unknown"] += 1
    body_coverage = {
        "captured_text_bodies": captured,
        "exported_structural_bodies": exported,
        "not_retained_in_trace": 2 * len(review.trace.exchanges) - captured,
        "excluded_by_review": captured - reviewed,
        "omitted_during_projection": reviewed - exported,
        "masked_exchange_routes": masked_exchange_routes,
        "exported_exchange_count": len(timeline),
    }

    files["manifest.json"] = _member_bytes({
        "schema": ZIP_SCHEMA, "export": "explicit-only", "contains_raw_session": False,
        "exchange_count": len(timeline),
        "excluded_exchanges": preview["dropped_exchanges"],
        "capture_omissions": capture_omissions,
        "body_coverage": body_coverage,
        "body_capture_reasons": capture_reasons,
        "secret_values_suppressed": preview["secrets_replaced"],
        "coverage": "JSON/XML structures with reviewed scalars and selected HTML selectors; free text omitted",
        "xml_namespaces": "Unknown namespace URIs are opaque labels local to each body; original URIs omitted",
        "route_templates": "Opaque ~route-NNNN~ segments are stable within this archive only; originals omitted",
        "max_uncompressed": MAX_UNCOMPRESSED,
    })
    files["README.md"] = (
        b"# Cifereca Browser Recorder\n"
        b"Synthetic or explicitly reviewed metadata and structural body excerpts.\n"
        b"All nonapproved values may be omitted; absent fields are not evidence.\n"
        b"Opaque ~route-NNNN~ path segments preserve equality, not original route names.\n"
        b"Route labels are local to this archive; no reverse mapping is included.\n"
        b"Never replay captured requests or infer legal rights/DRM from a trace.\n"
    )
    files["LLM_INSTRUCTIONS.md"] = (
        b"Read plugin-dev-kit/manifest.json and plugin-dev-kit/README.md FIRST.\n"
        b"Use plugin-dev-kit/contracts/*.py (actual public Core interfaces)\n"
        b"and plugin-dev-kit/contract-registry.json before proposing code.\n"
        b"Produce endpoint evidence/assumption matrix, fake tests and packaging.\n"
        b"Never reconstruct credentials, tokens or attempt authenticated replay.\n"
    )
    # Offline Core-owned PUBLIC contract sources only; no capture text is
    # interpolated into the documentation. This is part of the exact preview,
    # member limits, ZIP hash and explicit save consent.
    kit = core_contract_kit()
    if set(kit) & set(files):
        raise RecorderError("core_kit_invalid")
    files.update(kit)
    blob = _zip_bytes(files)
    # Final safety assertion before any archive could be offered to a user:
    # even if an upstream review missed a secret, known secret values and
    # their encodings cannot appear in the assembled ZIP member payloads.
    all_plain = b"\n".join(files.values()).decode("utf-8", "replace")
    for secret in review._secret_values:
        if any(form in all_plain for form in _variants(secret)):
            raise RecorderError("bundle_secret_risk")
    if _JWT.search(all_plain) or re.search(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", all_plain,
    ):
        raise RecorderError("bundle_secret_risk")
    return ReviewedBundlePlan(blob, files)
