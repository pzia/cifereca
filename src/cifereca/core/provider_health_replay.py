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

    def read_recorded(self, method, url):
        """Parser requests only an exact logical read, never captured credentials."""
        if self._closed or self._failed or self._cursor >= len(self._entries):
            self._failed = True
            raise ReplayUnavailable("replay_unmatched_request")
        recorded_method, recorded_url, headers, _, _, _ = self._entries[self._cursor]
        if (method, url) != (recorded_method, recorded_url):
            self._failed = True
            raise ReplayUnavailable("replay_unmatched_request")
        response = self.handle_request(httpx.Request(method, url, headers=headers))
        response.read()
        # Do not expose cookies or other response credentials to the parser.
        return httpx.Response(response.status_code, content=response.content,
                              headers={"content-type": response.headers.get("content-type", "")})

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


def projection_digest(rows):
    """Bounded canonical digest, with explicit empty distinct from unknown."""
    from hashlib import sha256
    if type(rows) is not tuple or len(rows) > 2048:
        return None
    seen = set()
    for row in rows:
        if (type(row) is not tuple or len(row) != 4
                or not all(type(value) is str and len(value) <= 4096 for value in row)
                or not row[0] or not row[1] or row[0] in seen):
            return None
        seen.add(row[0])
    payload = json.dumps(sorted(rows), ensure_ascii=True, separators=(",", ":")).encode()
    if len(payload) > 2_000_000:
        return None
    return sha256(payload).hexdigest()


def loan_snapshot_digest(snapshot, account):
    from ..plugin_api.loan_inventory import LoanInventorySnapshot
    try:
        if (type(snapshot) is not LoanInventorySnapshot or snapshot.status != "success"
                or snapshot.reason_code is not None or not snapshot.observed_at or not snapshot.branches
                or any(branch.completeness != "complete" or branch.pagination_complete is not True
                       or branch.medium == "unknown" for branch in snapshot.branches)
                or (snapshot.scope.provider_id, snapshot.scope.credential_id, snapshot.scope.account_id,
                    snapshot.scope.identity_id) != (account.provider_id, account.credential_id,
                                                    account.unique_id, account.identity_id)):
            return None
        return projection_digest(tuple((row.id, row.record_id, row.title, row.author or "")
                                       for row in snapshot.loans))
    except (ValueError, TypeError, AttributeError):
        return None


def replay_archived_observation(manager, scope, capability, evidence_ref, payload):
    """One generic dispatch to an already active optional parser adapter.

    Never activate a plugin, create a provider session or execute archived source.
    Plugin code is trusted installed code, not an OS-level sandbox.
    """
    unknown = {"outcome": "unknown"}
    try:
        checked = project_archived_http_zip(payload, include_binary=True, source_session_ref=evidence_ref)
        with ZipFile(BytesIO(checked)) as archive:
            meta = json.loads(archive.read("index.json"))["health_observation"]
        baseline = meta.get("business_digest")
        version = meta.get("version")
        if (meta.get("schema") != "cifereca.health-observation/1"
                or meta.get("capability") != capability or type(baseline) is not str
                or len(baseline) != 64 or any(c not in "0123456789abcdef" for c in baseline)
                or type(version) is not str or not version or version == "unknown"):
            return unknown
        with manager.hold_descriptive_lifecycle():
            record = manager._records.get(scope.provider_id)
            if (record is None or record.descriptor.type != "provider.library"
                    or record.descriptor.id != scope.provider_id or not record.enabled
                    or record.state not in ("ready", "degraded")):
                return unknown
            service = record.service
            replay_version = record.descriptor.version
            adapter = getattr(service, "health_replay_projection", None)
        if not callable(adapter) or type(replay_version) is not str or not replay_version:
            return unknown
        tape = CapturedReadTransport(checked, evidence_ref=evidence_ref, producer=scope.provider_id)
        try:
            rows = adapter(capability, tape.read_recorded)
            tape.require_complete()
            actual = projection_digest(rows)
        finally:
            tape.close()
        if actual is None:
            return unknown
        with manager.hold_descriptive_lifecycle():
            current = manager._records.get(scope.provider_id)
            if (current is not record or current.service is not service or not current.enabled
                    or current.state not in ("ready", "degraded")
                    or current.descriptor.version != replay_version):
                return unknown
        return {"outcome": "equal" if actual == baseline else "different",
                "observed_version": version, "replay_version": replay_version,
                "rows": len(rows), "empty_attested": not rows}
    except Exception:
        return unknown
