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
import os
from pathlib import Path
import re
import secrets
import stat
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from browser_recorder import RecorderError, SAFE_SEGMENTS, site_origin
from browser_recorder_review import _SECRETS, _IDS, _JWT, _variants

ZIP_SCHEMA = "cifereca.browser-recorder.approved-zip/1"
MAX_EXCHANGES = 240
MAX_MEMBERS = 600
MAX_UNCOMPRESSED = 4 * 1024 * 1024
MAX_ZIP_BYTES = 2 * 1024 * 1024
MAX_BODY_OUT = 16000
_FILENAME = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}[.]zip$")
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,80}$")
_SAFE_PATH = SAFE_SEGMENTS | frozenset((
    "api", "v3", "v4", "items", "entries", "products", "details", "authors",
    "pagination", "next", "results", "opds", "fetch", "search.php",
))
_SAFE_CLASS = frozenset((
    "book", "books", "card", "item", "result", "results", "title", "author",
    "price", "container", "row", "list", "catalog", "catalogue", "metadata",
    "detail", "details", "cover", "format", "pagination",
))


def _safe_url(url: str, approved: frozenset[str],
              allowed_origins: frozenset[str]) -> str:
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
        # Publication of a path is not proof of an authentication grant.
        path = "/".join(
            part if part.casefold() in _SAFE_PATH or part in approved
            or part.startswith("PSEUDO_I") or part == ""
            else "~redacted~"
            for part in p.path.split("/")
        )
        query = []
        for key, value in parse_qsl(p.query, keep_blank_values=True,
                                    max_num_fields=120):
            if not _FIELD.fullmatch(key):
                raise ValueError("invalid_key")
            if _SECRETS.search(key):
                query.append((key, "[SECRET]"))
            elif value in approved or value.startswith("PSEUDO_I"):
                query.append((key, value))
            elif value.isdecimal() and len(value) <= 3 and not _IDS.search(key):
                query.append((key, value))
            else:
                query.append((key, "[UNREVIEWED]"))
        return urlunsplit((p.scheme, p.netloc, path,
                           urlencode(query, doseq=False), ""))
    except (ValueError, UnicodeError, TypeError):
        raise RecorderError("bundle_invalid_url") from None


class _MarkupSkeleton(HTMLParser):
    """Keep DOM selectors/element nesting, not private text/attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.budget = 0

    def _emit(self, text: str):
        self.budget += len(text)
        if self.budget > MAX_BODY_OUT:
            raise RecorderError("bundle_markup_limit")
        self.parts.append(text)

    def handle_starttag(self, tag, attrs):
        if not re.fullmatch(r"[a-z][a-z0-9:-]{0,40}", tag):
            return
        projected = []
        for key, value in attrs[:32]:
            if key in {"class", "rel"} and value:
                tokens = value.split()
                allowed = [token for token in tokens
                           if token.casefold() in _SAFE_CLASS]
                if allowed:
                    projected.append(key + '="' + escape(" ".join(allowed)) + '"')
            elif key in {"type", "name", "role"} and value:
                # All attribute values can be identifiers. Keep only
                # generic element types; never input value, action, href,
                # onclick, style, data-*, placeholder or autofocus.
                generic = {"text", "password", "hidden", "button", "submit",
                           "checkbox", "radio", "form", "book", "title"}
                if value.lower() in generic:
                    projected.append(key + '="' + escape(value.lower()) + '"')
        self._emit("<" + tag + (" " + " ".join(projected) if projected else "") + ">")

    def handle_endtag(self, tag):
        if re.fullmatch(r"[a-z][a-z0-9:-]{0,40}", tag):
            self._emit("</" + tag + ">")

    def handle_data(self, data):
        if data.strip():
            self._emit("[TEXT]")

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
            if type(k) is not str or not _FIELD.fullmatch(k):
                continue
            result[k] = _json_shape(v, name=k, approved=approved,
                                    depth=depth+1, counter=counter)
        return result
    if type(node) is list:
        return [_json_shape(x, name=name, approved=approved,
                            depth=depth+1, counter=counter)
                for x in node[:256]]
    if type(node) in (int, float) and not isinstance(node, bool):
        if _IDS.search(name) and str(node) not in approved:
            return "[UNREVIEWED_ID]"
        return node if abs(node) <= 1e12 else "[NUMBER]"
    if node is None or type(node) is bool:
        return node
    if type(node) is str:
        if node == "[SECRET]" or node == "[UNREVIEWED]":
            return node
        if node in approved or node.startswith("PSEUDO_I"):
            return node
        # Preserve JSON field names/types and relation of approved values,
        # but do not publish unreviewed free text by default.
        return "[UNREVIEWED_ID]" if _IDS.search(name) else "[TEXT]"
    return "[OMITTED]"


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
    if mime in {"text/html", "application/xhtml+xml", "text/xml",
                "application/xml"} or mime.endswith("+xml"):
        try:
            parser = _MarkupSkeleton()
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
                                  else v if v in approved or v.startswith("PSEUDO_I")
                                  else "[UNREVIEWED]")}
            for k, v in pairs if _FIELD.fullmatch(k)
        ], ensure_ascii=True), "captured_form_structure"
    # Unknown plain text may contain licenses, auth material or private prose.
    return None, "body_omitted"


def _safe_headers(headers):
    permitted = {"content-type", "accept", "retry-after",
                 "x-ratelimit-limit", "x-ratelimit-remaining"}
    result = {}
    for key, value in headers.items():
        if key in permitted and type(value) is str:
            if re.fullmatch(r"[A-Za-z0-9 ,;./+_=-]{1,150}", value):
                result[key] = value
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
            "warning": "Revue manuelle obligatoire : risque de données résiduelles.",
        }

    def save(self, directory: Path, *, user_confirmed: bool = False):
        if not self._viewed or user_confirmed is not True:
            raise RecorderError("export_unconfirmed")
        directory = Path(directory)
        if directory.is_symlink():
            raise RecorderError("unsafe_directory")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc,
        ).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4) + ".zip"
        if not _FILENAME.fullmatch(name):
            raise RecorderError("bundle_invalid_filename")
        folder_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            dir_fd = os.open(directory, folder_flags)
            try:
                fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(self._blob)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.close(dir_fd)
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
    files = {}
    timeline = []
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
            "url": _safe_url(entry["url"], approved, allowed_origins),
            "redirect_to": (_safe_url(entry["redirect_to"], approved, allowed_origins)
                            if entry["redirect_to"] else None),
            "mime": entry["mime"], "network_error": entry["network_error"],
            "request_headers": _safe_headers(entry["request_headers"]),
            "response_headers": _safe_headers(entry["response_headers"]),
            "bodies": {},
        }
        for direction in ("request", "response"):
            body, state = _body(entry[direction + "_body"],
                                (entry[direction + "_headers"].get("content-type")
                                 or entry["mime"]).split(";", 1)[0].lower(),
                                approved)
            outgoing["bodies"][direction] = {"state": state, "file": None}
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
    files["manifest.json"] = _member_bytes({
        "schema": ZIP_SCHEMA, "export": "explicit-only", "contains_raw_session": False,
        "exchange_count": len(timeline),
        "excluded_exchanges": preview["dropped_exchanges"],
        "secret_values_suppressed": preview["secrets_replaced"],
        "coverage": "JSON shapes and selected HTML/XML selectors; free text omitted",
        "max_uncompressed": MAX_UNCOMPRESSED,
    })
    files["README.md"] = (
        b"# Cifereca Browser Recorder\n"
        b"Synthetic or explicitly reviewed metadata and structural body excerpts.\n"
        b"All nonapproved values may be omitted; absent fields are not evidence.\n"
        b"Never replay captured requests or infer legal rights/DRM from a trace.\n"
    )
    files["LLM_INSTRUCTIONS.md"] = (
        b"Reconstruct likely endpoint and response schemas from this limited trace.\n"
        b"Clearly mark conjectures. Suggest synthetic fixtures and provider tests.\n"
        b"Never reconstruct credentials, tokens or attempt authenticated replay.\n"
    )
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
