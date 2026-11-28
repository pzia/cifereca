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
from .notice_consolidator_worker import projection_digest


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
        recorded_method, recorded_url, _, status, headers, body = self._entries[self._cursor]
        if (method, url) != (recorded_method, recorded_url):
            self._failed = True
            raise ReplayUnavailable("replay_unmatched_request")
        self._cursor += 1
        # Do not reconstruct a request or expose captured credentials.
        content_types = [value for key, value in headers if key == b"content-type"]
        if len(content_types) > 1:
            self._failed = True
            raise ReplayUnavailable("replay_metadata")
        return httpx.Response(status, content=body,
                              headers=[(b"content-type", content_types[0])] if content_types else [])

    def isolated_entries(self):
        """Pass only exact read coordinates, content type and body, no headers."""
        from base64 import b64encode
        entries = []
        total = 0
        if self._closed or self._failed or self._cursor or not 1 <= len(self._entries) <= 32:
            raise ReplayUnavailable("replay_incomplete")
        for method, url, _, status, headers, body in self._entries:
            total += len(body)
            if total > 4 * 1024 * 1024:
                raise ReplayUnavailable("replay_body_limit")
            content_types = [value for key, value in headers if key == b"content-type"]
            if len(content_types) > 1:
                raise ReplayUnavailable("replay_metadata")
            content_type = content_types[0].decode("latin-1") if content_types else ""
            entries.append((method, url, status, content_type, b64encode(body).decode("ascii")))
        return entries

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


def _run_isolated_replay(modules, entries, capability):
    from pathlib import Path
    import subprocess
    import sys
    from .notice_consolidator_worker import MAX_REPLAY_REQUEST_BYTES, MAX_RESPONSE_BYTES
    wire = json.dumps({"modules": modules, "entries": entries, "capability": capability},
                      ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(wire) > MAX_REPLAY_REQUEST_BYTES:
        raise ReplayUnavailable("replay_request_limit")
    completed = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).with_name("notice_consolidator_worker.py")),
         "--provider-health-replay"], input=wire, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=5, check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, close_fds=True)
    if completed.returncode or len(completed.stdout) > MAX_RESPONSE_BYTES:
        raise ReplayUnavailable("replay_worker")
    response = json.loads(completed.stdout)
    if type(response) is not dict or set(response) != {"ok", "result"} or response["ok"] is not True:
        raise ReplayUnavailable("replay_worker")
    result = response["result"]
    if (type(result) is not dict or set(result) != {"digest", "rows"}
            or type(result["digest"]) is not str or len(result["digest"]) != 64
            or any(c not in "0123456789abcdef" for c in result["digest"])
            or type(result["rows"]) is not int or not 0 <= result["rows"] <= 2048):
        raise ReplayUnavailable("replay_worker")
    return result


def _declared_modules(manager, record):
    import re
    from .provider_health_source import snapshot_provider_source
    service = record.service
    # Inert class data only. No provider getter or adapter invocation in Core.
    declaration = vars(type(service)).get("health_replay_modules")
    if (type(declaration) is not tuple or not 1 <= len(declaration) <= 16
            or any(type(name) is not str or re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]{0,127}", name) is None
                   for name in declaration) or len(set(declaration)) != len(declaration)):
        raise ReplayUnavailable("replay_unsupported")
    snapshot = snapshot_provider_source(manager, record.descriptor.id)
    if snapshot is None or snapshot["version"] != record.descriptor.version:
        raise ReplayUnavailable("replay_source")
    return tuple((name, snapshot["files"][name + ".py"].decode("utf-8")) for name in declaration)


def replay_archived_observation(manager, scope, capability, evidence_ref, payload):
    """One exact historical capture, one isolated parser, no live session."""
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
            modules = _declared_modules(manager, record)
        if type(replay_version) is not str or not replay_version or replay_version == "unknown":
            return unknown
        tape = CapturedReadTransport(checked, evidence_ref=evidence_ref, producer=scope.provider_id)
        try:
            actual = _run_isolated_replay(modules, tape.isolated_entries(), capability)
        finally:
            tape.close()
        with manager.hold_descriptive_lifecycle():
            current = manager._records.get(scope.provider_id)
            if (current is not record or current.service is not service or not current.enabled
                    or current.state not in ("ready", "degraded")
                    or current.descriptor.version != replay_version
                    or _declared_modules(manager, current) != modules):
                return unknown
        return {"outcome": "equal" if actual["digest"] == baseline else "different",
                "observed_version": version, "replay_version": replay_version,
                "rows": actual["rows"], "empty_attested": actual["rows"] == 0}
    except Exception:
        return unknown
