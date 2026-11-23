"""#6397: durable private-only reference sidecar for an exact C contribution.

The short-lived raw observation remains evictable. The retained field is
never part of the shared canonical row or the public cursor. No owner,
credential, record ID, signed URL, session token or raw body is copied into
the shared C. Each read requires the original exact private scope and a live
connection binding, then checks the same source/C epoch and eviction fence.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

from ..catalogue_link import catalogue_notice_url
from .notice_cover_repository import _source_fingerprint

FIELD = "authenticated_web_references"
_PREFIX = "notice.c.web.private.v1:"
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _key(doc):
    return _PREFIX + doc.source_key.removeprefix("notice-c:v1:")


def _scope_digest(doc):
    encoded = json.dumps([
        "private-web-scope.v1", doc.kind, doc.identity_id, doc.provider_id,
        doc.credential_id, doc.account_id, doc.source_key,
    ], ensure_ascii=True, separators=(",", ":"))
    return sha256(encoded.encode("ascii")).hexdigest()


def _epoch(value):
    return type(value) is int and 1 <= value < 2**63


def validated_private_web_field(raw, provider):
    """Closed durable descriptive shape, no provider callbacks or URL secrets."""
    if (type(provider) is not str or not 1 <= len(provider) <= 64
            or not provider.isascii() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", provider)):
        return None
    if (type(raw) is not dict or set(raw) != {"schema", "connection_epoch", "links"}
            or type(raw["schema"]) is not int or raw["schema"] != 1
            or not _epoch(raw["connection_epoch"])
            or type(raw["links"]) is not list or not 1 <= len(raw["links"]) <= 8):
        return None
    allowed = {
        provider + ".authenticated_search_record_identifier",
        provider + ".authenticated_search_product_card",
    }
    links = []
    for row in raw["links"]:
        if (type(row) is not dict or set(row) != {"url", "evidence"}
                or type(row["evidence"]) is not str or row["evidence"] not in allowed):
            return None
        url = row["url"]
        if (type(url) is not str or not 0 < len(url) <= 512
                or "?" in url or "#" in url or "%" in url
                or catalogue_notice_url({"friendly_url": url}) != url):
            return None
        try:
            parts = urlsplit(url)
            if (parts.scheme != "https" or parts.port not in (None, 443)
                    or parts.username is not None or parts.password is not None
                    or parts.query or parts.fragment):
                return None
        except ValueError:
            return None
        if any(link["url"] == url for link in links):
            return None
        links.append({"url": url, "evidence": row["evidence"]})
    return {"schema": 1, "connection_epoch": raw["connection_epoch"], "links": links}


def retain_private_web_field_txn(db, source, original):
    """Inside the existing canonical source write, copy only the declared field."""
    obs = source.observation
    doc = source.document
    if obs.key.visibility != "private":
        return
    key = _key(doc)
    field = None
    if (obs.parser_id == "core.search.authenticated-web"
            and obs.parser_version == "1" and obs.status == "complete"
            and obs.key.branch_id == "search.authenticated.catalogue"
            and doc.identity_id is not None and doc.account_id is not None):
        field = validated_private_web_field(obs.fields_copy().get(FIELD), doc.provider_id)
    if field is None:
        # A replacement contribution is never allowed to retain older links.
        db.execute("DELETE FROM metadata WHERE key=?", (key,))
        return
    payload = {
        "schema": 1, "scope": _scope_digest(doc),
        "source_epoch": source.epoch, "source_revision": obs.source_revision,
        "proof": _source_fingerprint(obs, tuple(original["field_path"]),
                                     original["cover_inventory_complete"]),
        "field": field,
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    db.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) "
        "DO UPDATE SET value=excluded.value WHERE value<>excluded.value",
        (key, raw),
    )
    # The public C retains only this opaque digest, never the field/scope.
    return sha256(raw.encode("ascii")).hexdigest()


def retained_private_web_field_txn(db, doc, member, fence):
    """An evicted exact source can use its scoped retained field, never raw DELETE."""
    if (member.visibility != "private" or member.provider_id != doc.provider_id
            or member.source_ref != doc.source_key or fence is None
            or fence.get("evicted") is not True
            or fence["epoch"] != member.source_epoch + 1):
        return None
    row = db.execute("SELECT value FROM metadata WHERE key=?", (_key(doc),)).fetchone()
    if row is None or type(row[0]) is not str or len(row[0]) > 16384:
        return None
    try:
        if (type(member.private_web_digest) is not str
                or sha256(row[0].encode("ascii")).hexdigest() != member.private_web_digest):
            return None
        from .notice_cover_repository import _unique_object
        raw = json.loads(row[0], object_pairs_hook=_unique_object)
        if (type(raw) is not dict or set(raw) != {
                "schema", "scope", "source_epoch", "source_revision", "proof", "field",
            } or type(raw["schema"]) is not int or raw["schema"] != 1
                or raw["scope"] != _scope_digest(doc)
                or not _epoch(raw["source_epoch"]) or raw["source_epoch"] != member.source_epoch
                or raw["source_revision"] != member.source_revision
                or type(raw["proof"]) is not str or _HASH.fullmatch(raw["proof"]) is None
                or raw["proof"] != fence["proof"]):
            return None
        return validated_private_web_field(raw["field"], doc.provider_id)
    except (TypeError, ValueError, KeyError, UnicodeError, RecursionError):
        return None


__all__ = ["retain_private_web_field_txn", "retained_private_web_field_txn"]
