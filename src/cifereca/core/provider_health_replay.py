"""Strict local HTTP trace transport for isolated, explicitly admitted replay.

No network transport, session discovery, automatic authentication or fallback.
The caller must supply a fresh offline parser composition; never attach this
transport to a live provider session. Unsupported captures remain unknown.
"""
from io import BytesIO
import json
from zipfile import ZipFile

import httpx

from ..http_trace import project_archived_http_zip


class ReplayUnavailable(RuntimeError):
    """Closed reason; never expose URL, headers or raw capture bytes."""


def _headers(value):
    if type(value) is not list:
        raise ReplayUnavailable("replay_metadata")
    try:
        if any(type(pair) is not list or len(pair) != 2
               or any(type(part) is not str for part in pair) for pair in value):
            raise ValueError
        return tuple((name.lower().encode("latin-1"), val.encode("latin-1"))
                     for name, val in value)
    except (ValueError, UnicodeError):
        raise ReplayUnavailable("replay_metadata") from None


class CapturedReadTransport(httpx.BaseTransport):
    """One exact ordered tape, no substitutions or unrecorded HTTP calls."""

    def __init__(self, payload, *, evidence_ref, producer):
        self._cursor = 0
        self._failed = False
        self._closed = False
        if not all(type(v) is str and v for v in (evidence_ref, producer)):
            raise ReplayUnavailable("replay_scope")
        try:
            checked = project_archived_http_zip(payload, include_binary=True,
                                                source_session_ref=evidence_ref)
            with ZipFile(BytesIO(checked)) as archive:
                index = json.loads(archive.read("index.json"))
                if not index["exchanges"] or index.get("omitted_exchanges") != 0:
                    raise ReplayUnavailable("replay_incomplete")
                entries = []
                for entry in index["exchanges"]:
                    req, res = entry["request"], entry["response"]
                    if (entry.get("task_ref") != evidence_ref or req.get("producer") != producer
                            or req.get("method") not in ("GET", "HEAD")
                            or entry["outcome"] != "complete"
                            or entry["request_body"]["observed_bytes"] != 0
                            or entry["response_body"]["state"] != "complete"
                            or entry["response_body"]["observed_bytes"] != entry["response_body"]["stored_bytes"]):
                        raise ReplayUnavailable("replay_incomplete")
                    headers = _headers(res["headers"])
                    # Compressed transport needs a separately bounded decoder;
                    # never silently certify compressed or truncated responses.
                    if any(k == b"content-encoding" and v.lower().strip() != b"identity"
                           for k, v in headers):
                        raise ReplayUnavailable("replay_encoding")
                    status = res["status_code"]
                    if type(status) is not int or not 100 <= status <= 599:
                        raise ReplayUnavailable("replay_metadata")
                    url = req["url"]
                    if type(url) is not str or httpx.URL(url).scheme not in ("http", "https"):
                        raise ReplayUnavailable("replay_metadata")
                    entries.append((req["method"], url, _headers(req["headers"]),
                                    status, headers, archive.read(entry["response_body"]["blob"])))
                self._entries = tuple(entries)
        except ReplayUnavailable:
            raise
        except Exception:
            raise ReplayUnavailable("replay_invalid") from None

    def handle_request(self, request):
        if self._closed or self._failed or self._cursor >= len(self._entries):
            self._failed = True
            raise ReplayUnavailable("replay_unmatched_request")
        method, url, headers, status, response_headers, body = self._entries[self._cursor]
        if (request.method != method or str(request.url) != url
                or tuple((k.lower(), v) for k, v in request.headers.raw) != headers
                or request.read() != b""):
            self._failed = True
            raise ReplayUnavailable("replay_unmatched_request")
        self._cursor += 1
        return httpx.Response(status, headers=response_headers, stream=httpx.ByteStream(body))

    def require_complete(self):
        if self._closed or self._failed or self._cursor != len(self._entries):
            raise ReplayUnavailable("replay_incomplete")

    def close(self):
        self._closed = True
        self._entries = ()


def compare_business_projection(baseline, replayed, *, baseline_version, replay_version):
    """Compare exact bounded normalized record projections, not HTTP status.

    Inputs must be supplied by an independently qualified parser composition.
    No assertion of parser isolation or capture ownership is made here.
    """
    def normalize(rows):
        if type(rows) is not tuple or len(rows) > 10000:
            raise ValueError
        result = {}
        for row in rows:
            if (type(row) is not tuple or len(row) != 4
                    or not all(type(v) is str and len(v) <= 4096 for v in row)
                    or not row[0] or not row[1]):
                raise ValueError
            key = row[:2]
            if key in result:
                raise ValueError
            result[key] = row[2:]
        return result
    try:
        if not all(type(v) is str and v and v != "unknown"
                   for v in (baseline_version, replay_version)):
            raise ValueError
        before, after = normalize(baseline), normalize(replayed)
        if not before or not after:
            raise ValueError
    except ValueError:
        return {"outcome": "unknown"}
    return {"outcome": "equal" if before == after else "different",
            "added": len(after.keys() - before.keys()),
            "removed": len(before.keys() - after.keys()),
            "changed": sum(before[k] != after[k] for k in before.keys() & after.keys()),
            "version_changed": baseline_version != replay_version}
