"""#7163: offline review of existing private HTTP-trace ZIP; no network or raw export.

Usage (on the owner's machine):
  PYTHONPATH=src/plugins/provider-numilog python -m cifereca_numilog_capture_replay capture.zip

Only closed aggregate counters are printed. The capture ZIP is never modified,
copied or uploaded. No authentication headers, URLs, bodies or file paths escape.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile, BadZipFile
import argparse
import json
import re
import zlib

from cifereca.plugin_api import ProviderError
from cifereca_numilog_public import MAX_HTML_BYTES, PublicSearchParseError, _query
from cifereca_numilog_public_client import _parse_search_body

_MAX_INDEX = 4 * 1024 * 1024
_MAX_TRANSPORT = 4 * 1024 * 1024
_REF = re.compile(r"[0-9a-f]{32}\Z")
# HTTP names are ASCII tokens, unlike ambiguous capture metadata.
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,4096}\Z")
_LAYOUT_PREFIX = "Numilog : public_catalog_layout_unverified:"


def _headers(value):
    if type(value) is not list or len(value) > 64:
        return None
    result = []
    for pair in value:
        if (type(pair) not in (list, tuple) or len(pair) != 2
                or any(type(v) is not str or len(v) > 4096 for v in pair)):
            return None
        if (_HEADER_NAME.fullmatch(pair[0]) is None
                or any((ord(char) < 32 and char != "\t") or ord(char) == 127
                       for char in pair[1])):
            return None
        name = pair[0].casefold()
        # A duplicated Content-Encoding/Type or auth header is ambiguous:
        # dict(headers) would silently select one value during replay.
        if any(previous == name for previous, _ in result):
            return None
        result.append((name, pair[1]))
    return tuple(sorted(result))

def _private_request_header(name):
    """Conservative transport-only credential fence for anonymous GET counts.

    A request marked public by its producer can still carry a private
    session/credential through an extension header. Neither the replay
    statistics nor the exact-GET coalescing evidence may claim it anonymous.
    """
    normalized = "".join(ch for ch in name if ch.isalnum()).casefold()
    return (
        not normalized or any(ord(ch) > 127 for ch in name)
        or normalized.endswith("key")
        or any(part in normalized for part in (
            "cookie", "authorization", "token", "session", "credential",
            "secret", "signature", "ticket", "password", "apikey",
            "accesskey", "oauth", "saml", "bearer", "private", "sso",
        ))
        or normalized.startswith(("xauth", "xamz", "xgoog"))
    )


def _bounded_decode(raw, coding):
    if coding in ("", "identity"):
        return raw if len(raw) <= MAX_HTML_BYTES else None
    if coding in ("gzip", "x-gzip", "deflate"):
        decoder = zlib.decompressobj(
            16 + zlib.MAX_WBITS if coding in ("gzip", "x-gzip") else zlib.MAX_WBITS
        )
        try:
            data = decoder.decompress(raw, MAX_HTML_BYTES + 1)
            return (data if decoder.eof and not decoder.unconsumed_tail
                    and not decoder.unused_data and len(data) <= MAX_HTML_BYTES
                    else None)
        except zlib.error:
            return None
    if coding == "br":
        # The 20/11 transport captures are Brotli, not already decoded HTML.
        # Use an optional decoder in bounded chunks; never decompress a whole
        # untrusted body before checking the expanded size.
        try:
            import brotli
        except ImportError:
            return None
        try:
            decoder = brotli.Decompressor()
            chunks = []
            size = 0
            for offset in range(0, len(raw), 16 * 1024):
                part = decoder.process(raw[offset:offset + 16 * 1024])
                size += len(part)
                if size > MAX_HTML_BYTES:
                    return None
                chunks.append(part)
            if not decoder.is_finished():
                return None
            return b"".join(chunks)
        except (ValueError, RuntimeError, AttributeError, brotli.error):
            # The optional Brotli package raises its own Exception subclass
            # for invalid wire bytes. A corrupt historical response is an
            # unqualified transport, never an uncaught replay failure.
            return None
    return None


def analyse_capture(path):
    """Return only closed counts. Never return capture coordinates or text."""
    counts = Counter()
    reasons = Counter()
    transport_keys = set()
    observed_intervals = defaultdict(list)
    try:
        with ZipFile(path) as archive:
            # ZIP permits duplicate member names, while getinfo() silently
            # selects only the final one. A replay must never certify counts
            # from an ambiguous index or body chosen by archive ordering.
            # A capture is bounded to 1000 exchanges; even with separate
            # request and response bodies it needs at most 2001 members.
            # Reject oversized ZIP directories before indexing their names.
            if len(archive.infolist()) > 2001:
                raise ValueError("oversized_trace_member_inventory")
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("ambiguous_trace_members")
            index = archive.getinfo("http/index.json")
            if index.file_size > _MAX_INDEX:
                raise ValueError("invalid_trace_index")
            trace = json.loads(archive.read(index))
            if (type(trace) is not dict
                    or trace.get("schema") != "cifereca.http-trace/1"
                    or type(trace.get("exchanges")) is not list
                    or len(trace["exchanges"]) > 1000):
                raise ValueError("invalid_trace_index")
            for entry in trace["exchanges"]:
                counts["exchanges"] += 1
                if type(entry) is not dict:
                    counts["unqualified"] += 1
                    continue
                req = entry.get("request")
                if type(req) is not dict or req.get("method") != "GET":
                    counts["unqualified"] += 1
                    continue
                # Never treat a private request as eligible for public
                # coalescing, even when the URL happens to be identical.
                headers = _headers(req.get("headers"))
                if (headers is None or req.get("producer") != "provider.numilog.public"
                        or any(_private_request_header(key) for key, _ in headers)):
                    counts["unqualified"] += 1
                    continue
                url = req.get("url")
                if type(url) is not str:
                    counts["unqualified"] += 1
                    continue
                try:
                    parsed_url = _query(url, allow_advanced=True)
                except PublicSearchParseError:
                    counts["unqualified"] += 1
                    continue
                body_request = entry.get("request_body")
                if type(body_request) is not dict or body_request.get("stored_bytes") != 0:
                    counts["unqualified"] += 1
                    continue
                # Only brSimple GETs can use the installed anonymous
                # _coalesced_public_search transport. Advanced Ra evidence
                # is still replayed below, but must never inflate the number
                # of network requests that our deduplication could prevent.
                if parsed_url["mode"] == "brSimple":
                    counts["eligible_public_get"] += 1
                    # Exact transport equivalence, not normalization of query,
                    # session, endpoint, charset or different headers.
                    transport_key = (url, headers)
                    transport_keys.add(transport_key)
                    started = entry.get("started_at")
                    duration = entry.get("duration_ms")
                    if (type(started) is str and type(duration) in (int, float)
                            and not isinstance(duration, bool) and 0 <= duration <= 60000):
                        try:
                            instant = datetime.fromisoformat(started)
                            if instant.tzinfo is not None:
                                start = instant.timestamp()
                                observed_intervals[transport_key].append(
                                    (start, start + duration / 1000.0))
                        except (ValueError, OverflowError, OSError):
                            pass
                else:
                    counts["advanced_get_not_coalescible"] += 1
                response = entry.get("response")
                info = entry.get("response_body")
                if type(response) is not dict or type(info) is not dict:
                    reasons["invalid_receipt"] += 1
                    continue
                if response.get("status_code") != 200:
                    reasons["http_non_200"] += 1
                    continue
                rh = _headers(response.get("headers"))
                if rh is None:
                    reasons["invalid_receipt"] += 1
                    continue
                values = dict(rh)
                if values.get("content-type", "").split(";", 1)[0].strip().lower() not in (
                        "text/html", "application/xhtml+xml"):
                    reasons["invalid_mime"] += 1
                    continue
                # Only bytes as observed on the wire may be decoded with
                # Content-Encoding. A recorder may instead store decompressed
                # body bytes; counting those as a Brotli/deflate failure would
                # give a false supplier-layout diagnosis.
                if info.get("representation") != "transport_bytes":
                    reasons["body_representation_unverified"] += 1
                    continue
                ref = entry.get("exchange_ref")
                if (type(ref) is not str or _REF.fullmatch(ref) is None
                        or info.get("state") != "complete"):
                    reasons["body_incomplete"] += 1
                    continue
                member = "http/bodies/" + ref + "-response.bin"
                if info.get("blob") != member:
                    reasons["invalid_receipt"] += 1
                    continue
                try:
                    meta = archive.getinfo(member)
                    if (meta.file_size > _MAX_TRANSPORT or
                            info.get("stored_bytes") != meta.file_size):
                        raise ValueError("body_size")
                    raw = archive.read(meta)
                except (KeyError, ValueError):
                    reasons["body_unavailable"] += 1
                    continue
                if info.get("sha256") != sha256(raw).hexdigest():
                    reasons["body_digest_mismatch"] += 1
                    continue
                coding = values.get("content-encoding", "identity").strip().lower()
                decoded = _bounded_decode(raw, coding)
                if decoded is None:
                    reasons["encoding_unavailable_or_invalid"] += 1
                    continue
                try:
                    parsed = _parse_search_body(decoded, url)
                    if parsed is not None:
                        counts["parser_qualified"] += 1
                except ProviderError as exc:
                    message = str(exc)
                    # Never emit arbitrary exceptions, HTML, URLs or source
                    # strings; reasons are already constrained in the parser.
                    if message.startswith(_LAYOUT_PREFIX):
                        reasons["layout:" + message[len(_LAYOUT_PREFIX):]] += 1
                    else:
                        reasons["parser_refused"] += 1
            counts["distinct_exact_public_get"] = len(transport_keys)
            counts["repeated_exact_public_get_observed"] = (
                counts["eligible_public_get"] - len(transport_keys))
            overlaps = 0
            for intervals in observed_intervals.values():
                active_end = float("-inf")
                for start, end in sorted(intervals):
                    if start < active_end:
                        overlaps += 1
                    active_end = max(active_end, end)
            counts["overlapping_exact_public_get_observed"] = overlaps
    except (OSError, BadZipFile, KeyError, ValueError, TypeError,
            UnicodeError, json.JSONDecodeError):
        return {"status": "invalid_capture"}
    return {"status": "reviewed", "counts": dict(sorted(counts.items())),
            "reasons": dict(sorted(reasons.items()))}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline Numilog capture review")
    parser.add_argument("capture", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(analyse_capture(args.capture), ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
