"""Bounded, opt-in HTTPX transport observations for the current process only.

No global HTTPX patch, eager stream reads, network replay or on-disk buffer.
Bodies are transport bytes, before HTTPX response decompression. Providers opt in
by constructing a public TraceHTTPTransport / TraceAsyncHTTPTransport.
"""
from __future__ import annotations

import copy
from io import BytesIO
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import threading
import time
import uuid
import zipfile

import httpx


# Browser-compatible HTTP identity shared by all Core-owned outbound clients.
# Explicit protocol-specific User-Agent headers remain possible by contract.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


def _browser_headers(value):
    """Add the Core browser identity without mutating caller-owned headers.

    HTTPX Headers is case-insensitive and preserves other header values and
    duplicates; an explicitly supplied User-Agent is a deliberate exception.
    """
    headers = httpx.Headers(value)
    explicit = headers.get_list("user-agent")
    if not explicit:
        headers["User-Agent"] = BROWSER_USER_AGENT
    elif len(explicit) != 1 or not explicit[0].strip():
        # A blank or duplicated UA is not a deliberate protocol exception.
        # Reject it before any network I/O instead of silently replacing the
        # Core default or sending an ambiguous browser identity.
        raise ValueError("invalid explicit User-Agent")
    return headers


def _trace_active() -> bool:
    from .logging_utils import diagnostics_level
    return diagnostics_level() == "trace"



# #7263: classify transport bodies without decoding or eagerly reading streams.
# Unknown types may retain a bounded prefix temporarily, never charge it to the
# body/session budget until positively identified as text.
HTTP_CAPTURE_INCLUDE_BINARY = "diagnostics.http_capture_include_binary"


def http_capture_include_binary(config):
    # Missing legacy preferences preserve the former opt-in Trace behaviour.
    # An invalid explicitly saved value never implicitly enables binary capture.
    return config.get(HTTP_CAPTURE_INCLUDE_BINARY, True) is True


def preserve_http_capture_preference(data, *, current):
    """An ordinary Settings draft cannot overwrite this immediate preference."""
    diagnostics = data.get("diagnostics")
    if type(diagnostics) is not dict:
        diagnostics = {}
        data["diagnostics"] = diagnostics
    diagnostics["http_capture_include_binary"] = http_capture_include_binary(current)
    # These settings are immediate Core-owned preferences as well. A Settings
    # dialog opened before their modification must not overwrite them.
    diagnostics["capture_retention_days"] = current.get("diagnostics.capture_retention_days", 90)
    diagnostics["capture_retention_bytes"] = current.get(
        "diagnostics.capture_retention_bytes", 128 * 1024 * 1024)
    return data


_BODY_PROBE_BYTES = 512
_EXCLUDED_STATES = frozenset(("binary_excluded", "body_type_unknown"))
_TEXT_APPLICATIONS = frozenset((
    "application/json", "application/xml", "application/javascript",
    "application/x-javascript", "application/x-www-form-urlencoded",
    "application/graphql", "application/yaml", "application/x-yaml",
    "application/toml", "application/sql",
))
_BINARY_APPLICATIONS = frozenset((
    "application/pdf", "application/epub+zip", "application/zip",
    "application/gzip", "application/x-gzip", "application/x-7z-compressed",
    "application/x-rar-compressed", "application/vnd.rar",
    "application/x-tar", "application/x-bzip2", "application/x-xz",
    "application/wasm", "application/vnd.amazon.ebook",
))
_BINARY_SIGNATURES = (
    b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a",
    b"%PDF-", b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08",
    b"7z\xbc\xaf\x27\x1c", b"Rar!", b"\x7fELF", b"RIFF", b"ID3",
    b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00",
)


def _header(meta, name):
    if not isinstance(meta, dict):
        return ""
    values = [value for key, value in meta.get("headers", ())
              if key.lower() == name]
    return values[0] if values and all(value == values[0] for value in values) else ""


def _mime(meta):
    return _header(meta, "content-type").split(";", 1)[0].strip().lower()


def _body_kind(meta, prefix, representation, *, final=False):
    """Classify MIME first, then bounded signatures for ambiguous bodies."""
    mime = _mime(meta)
    disposition = _header(meta, "content-disposition").lower()
    if (mime.startswith(("image/", "audio/", "video/", "font/"))
            or mime in _BINARY_APPLICATIONS
            or mime.endswith("+zip")
            or re.search(r'\bfilename\*?\s*=\s*[^;\r\n]*\.(?:epub|pdf|zip|png|jpe?g|gif|webp|svg|mp[34]|7z|rar|gz|tar)(?=["\s;]|$)', disposition)):
        return "binary"
    if (mime.startswith("text/") or mime in _TEXT_APPLICATIONS
            or mime.endswith(("+json", "+xml"))):
        return "text"
    # Compression of a declared textual entity is not a binary document.
    # An ambiguous encoded transport cannot be safely sniffed as plaintext.
    if (representation != "httpx_buffered_content"
            and _header(meta, "content-encoding").strip().lower() not in ("", "identity")):
        return "unknown"
    if any(prefix.startswith(signature) for signature in _BINARY_SIGNATURES):
        return "binary"
    if not final and len(prefix) < _BODY_PROBE_BYTES:
        return "pending"
    if not prefix:
        return "unknown"
    if b"\x00" in prefix or any(byte < 32 and byte not in (9, 10, 13) for byte in prefix):
        return "binary"
    # With no trustworthy MIME, retain only recognisable documentary syntax.
    # Arbitrary printable bytes are not proof that a document is text.
    if prefix.lstrip(b"\xef\xbb\xbf \t\r\n").startswith((b"{", b"[", b"<")):
        try:
            prefix.decode("utf-8")
        except UnicodeDecodeError as exc:
            if not (not final and exc.end == len(prefix)
                    and exc.reason == "unexpected end of data"):
                return "unknown"
        return "text"
    return "unknown"


def _exclude_body(body, meta, kind):
    body.update(state="binary_excluded" if kind == "binary" else "body_type_unknown",
                classification=kind, mime=_mime(meta), data=bytearray())


def _project_body_policy(entry):
    """Apply the current export choice to a copy, including historical bodies."""
    for side in ("request", "response"):
        body = entry[side + "_body"]
        if body["state"] in _EXCLUDED_STATES or not body["observed_bytes"]:
            continue
        meta = entry.get(side)
        kind = body.get("classification") or _body_kind(
            meta, bytes(body["data"][:_BODY_PROBE_BYTES]), body["representation"],
            final=len(body["data"]) < _BODY_PROBE_BYTES,
        )
        if kind != "text":
            body["prior_state"] = body["state"]
            _exclude_body(body, meta, kind)


def http_body_member(body, filename):
    """One compatible index/blob projection for both private ZIP exporters."""
    record = {key: value for key, value in body.items() if key != "data"}
    data = bytes(body["data"])
    if body["state"] in _EXCLUDED_STATES:
        if data:
            raise ValueError("excluded_http_body_has_data")
        record.update(stored_bytes=0)
        return record, None
    record.update(blob=filename, stored_bytes=len(data),
                  sha256=hashlib.sha256(data).hexdigest())
    return record, data


class HTTPTraceCapture:
    """Thread-safe bounded RAM archive; it cannot survive a process restart.

    Capture failures never escape into the business request. Export is an
    explicit separate operation and reports its own failures to its caller.
    """

    def __init__(self, *, body_limit=2 * 1024 * 1024,
                 session_limit=32 * 1024 * 1024, exchange_limit=1000,
                 metadata_limit=64 * 1024, include_binary=True):
        for value in (body_limit, session_limit, exchange_limit, metadata_limit):
            if type(value) is not int or value < 1:
                raise ValueError("Capture budgets must be positive integers")
        if type(include_binary) is not bool:
            raise ValueError("include_binary must be a boolean")
        self._include_binary = include_binary
        self.body_limit = body_limit
        self.session_limit = session_limit
        self.exchange_limit = exchange_limit
        self.metadata_limit = metadata_limit
        self.session_ref = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._exchanges = []
        self._used = 0
        self._omitted = 0

    def begin(self, request, producer):
        if not _trace_active():
            return None
        try:
            # Serialize only bounded metadata. Preserve header order/duplicates
            # and reversible bytes (Latin-1), never merge into a dictionary.
            if (len(str(request.url)) + sum(len(k) + len(v) for k, v in request.headers.raw)
                    > self.metadata_limit):
                with self._lock:
                    self._omitted += 1
                return None
            metadata = dict(method=request.method, url=str(request.url),
                            headers=[[k.decode('latin-1'), v.decode('latin-1')]
                                     for k, v in request.headers.raw],
                            producer=producer)
            size = len(json.dumps(metadata, ensure_ascii=True).encode())
            with self._lock:
                if (size > self.metadata_limit or self._used + size > self.session_limit
                        or len(self._exchanges) >= self.exchange_limit):
                    self._omitted += 1
                    return None
                self._used += size
                entry = dict(exchange_ref=uuid.uuid4().hex,
                             started_at=datetime.now(timezone.utc).isoformat(),
                             request=metadata, response=None, outcome="in_progress",
                             duration_ms=None, request_body=self._body(),
                             response_body=self._body())
                from .diagnostic_task_context import diagnostic_task_reference
                task_ref = diagnostic_task_reference()
                if task_ref is not None:
                    entry["task_ref"] = task_ref
                self._exchanges.append(entry)
                return _Exchange(self, entry)
        except Exception:
            return None

    @staticmethod
    def _body():
        return dict(state="not_read", observed_bytes=0, data=bytearray(),
                    representation="transport_bytes")

    def set_include_binary(self, value: bool):
        if type(value) is not bool:
            raise ValueError("include_binary must be a boolean")
        with self._lock:
            self._include_binary = value

    def status(self):
        """Small aggregate view, without copying bodies for a UI refresh."""
        with self._lock:
            return dict(active=_trace_active(), session_ref=self.session_ref,
                        exchanges=len(self._exchanges), used_bytes=self._used,
                        omitted_exchanges=self._omitted, include_binary=self._include_binary)

    def snapshot(self):
        with self._lock:
            snapshot = copy.deepcopy(dict(
                schema="cifereca.http-trace/1", session_ref=self.session_ref,
                representation="transport_bytes_before_response_decompression",
                omitted_exchanges=self._omitted, used_bytes=self._used,
                limits=dict(body_bytes=self.body_limit, session_bytes=self.session_limit,
                            exchanges=self.exchange_limit, metadata_bytes=self.metadata_limit),
                exchanges=self._exchanges))
            snapshot['include_binary'] = self._include_binary
            if not self._include_binary:
                for entry in snapshot['exchanges']:
                    _project_body_policy(entry)
            return snapshot

    def export(self, destination: str | Path):
        """Exclusive local ZIP; retain the established caller error contract."""
        from .core.capture_export import write_capture_zip
        from .core.capture_vault import VaultError

        if not _trace_active():
            raise ValueError("HTTP export requires active Trace")
        destination = Path(destination)
        payload = build_http_trace_zip(self.snapshot())
        try:
            write_capture_zip(destination.absolute(), payload, confirmed=True,
                              max_bytes=HTTP_TRACE_ZIP_LIMIT)
        except VaultError as exc:
            code = exc.args[0]
            if code == "private_zip_exists":
                raise FileExistsError("HTTP export destination exists") from exc
            raise OSError("HTTP export failed") from exc
        return destination


# 32 MiB retained observations plus bounded index/ZIP overhead. No change to
# capture budgets. The same index/blob format serves live and archived export.
HTTP_TRACE_ZIP_LIMIT = 63 * 1024 * 1024


def build_http_trace_zip(snapshot, *, include_kit=False):
    from .core.capture_export import build_capture_zip
    members = {}
    for entry in snapshot["exchanges"]:
        for side in ("request", "response"):
            name = f"bodies/{entry['exchange_ref']}-{side}.bin"
            record, content = http_body_member(entry[side + "_body"], name)
            entry[side + "_body"] = record
            if content is not None:
                members[name] = content
    members["index.json"] = json.dumps(snapshot, ensure_ascii=True, indent=2).encode()
    if include_kit:
        from .core.capture_devkit import core_contract_kit
        kit = core_contract_kit()
        if any(name in members or not name.startswith("plugin-dev-kit/") for name in kit):
            raise ValueError("invalid_http_archive_kit")
        members.update(kit)
        members["README.txt"] = (
            "PRIVATE CIFERECA HTTP TRACE — RAW credentials and URLs may be present.\n"
            "Local explicit export only; review before sharing. No network replay.\n"
            "Use plugin-dev-kit/PROVIDER-DEVELOPMENT.md for the offline Core contracts.\n"
        ).encode("utf-8")
    return build_capture_zip(members, max_bytes=HTTP_TRACE_ZIP_LIMIT,
                             compression=zipfile.ZIP_STORED)


def project_archived_http_zip(payload, *, include_binary, source_session_ref, include_kit=False):
    """Authenticate index/blob consistency and reapply the current binary policy."""
    if type(payload) is not bytes or len(payload) > HTTP_TRACE_ZIP_LIMIT:
        raise ValueError("invalid_http_archive")
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        infos = archive.infolist()
        names = {info.filename for info in infos}
        if (len(infos) != len(names) or len(infos) > 2001
                or "index.json" not in names
                or sum(info.file_size for info in infos) > HTTP_TRACE_ZIP_LIMIT):
            raise ValueError("invalid_http_archive")
        snapshot = json.loads(archive.read("index.json"))
        if (snapshot.get("schema") != "cifereca.http-trace/1"
                or snapshot.get("session_ref") != source_session_ref
                or type(snapshot.get("exchanges")) is not list
                or len(snapshot["exchanges"]) > 1000):
            raise ValueError("invalid_http_archive")
        expected = {"index.json"}
        seen = set()
        for entry in snapshot["exchanges"]:
            ref = entry["exchange_ref"]
            if type(ref) is not str or not re.fullmatch(r"[a-f0-9]{32}", ref) or ref in seen:
                raise ValueError("invalid_http_archive")
            seen.add(ref)
            for side in ("request", "response"):
                body = entry[side + "_body"]
                name = f"bodies/{ref}-{side}.bin"
                if body["state"] in _EXCLUDED_STATES:
                    if "blob" in body or body.get("stored_bytes") != 0:
                        raise ValueError("invalid_http_archive")
                    body["data"] = bytearray()
                else:
                    if body.get("blob") != name or name not in names:
                        raise ValueError("invalid_http_archive")
                    data = archive.read(name)
                    if (type(body.get("observed_bytes")) is not int
                            or len(data) > body["observed_bytes"]
                            or len(data) != body.get("stored_bytes")
                            or hashlib.sha256(data).hexdigest() != body.get("sha256")):
                        raise ValueError("invalid_http_archive")
                    expected.add(name)
                    body["data"] = bytearray(data)
                for key in ("blob", "stored_bytes", "sha256"):
                    body.pop(key, None)
            if not include_binary:
                _project_body_policy(entry)
        if names != expected:
            raise ValueError("invalid_http_archive")
    if include_binary and not include_kit:
        # Bodies deliberately omitted when archived cannot be recovered.
        return payload
    if not include_binary:
        snapshot["include_binary"] = False
    return build_http_trace_zip(snapshot, include_kit=include_kit)


class _Exchange:
    def __init__(self, capture, entry):
        self.capture, self.entry = capture, entry
        self.started = time.monotonic()
        self._probes = {}

    def _append(self, body, chunk):
        if body["state"] == "quota":
            return
        available = min(self.capture.body_limit - len(body["data"]),
                        self.capture.session_limit - self.capture._used)
        saved = chunk[:max(0, available)]
        body["data"].extend(saved)
        self.capture._used += len(saved)
        body["state"] = "partial" if len(saved) == len(chunk) else "quota"

    def _capture(self, side, body, chunk, *, final=False):
        pending = self._probes.pop(side, b"")
        if self.capture._include_binary:
            if pending:
                self._append(body, pending)
            if chunk:
                self._append(body, chunk)
            return
        meta = self.entry.get(side)
        prefix = bytes(body["data"][:_BODY_PROBE_BYTES])
        if len(prefix) < _BODY_PROBE_BYTES:
            prefix += pending[:_BODY_PROBE_BYTES - len(prefix)]
        if len(prefix) < _BODY_PROBE_BYTES:
            prefix += chunk[:_BODY_PROBE_BYTES - len(prefix)]
        kind = body.get("classification") or _body_kind(
            meta, prefix, body["representation"], final=final,
        )
        if kind == "pending":
            # Pending implies all bytes fit in the bounded probe.
            self._probes[side] = pending + chunk
            body["state"] = "partial"
            return
        if kind != "text":
            if body["data"]:
                # Earlier, explicitly included bytes were already charged.
                # Do not rewind the append-only session accounting/window.
                body["prior_state"] = body["state"]
            _exclude_body(body, meta, kind)
            return
        body.update(classification="text", mime=_mime(meta))
        if pending:
            self._append(body, pending)
        if chunk:
            self._append(body, chunk)

    def observe(self, side, chunk):
        try:
            with self.capture._lock:
                body = self.entry[side + '_body']
                if not _trace_active():
                    self._probes.pop(side, None)
                    if body['state'] not in ('complete', 'quota', 'error') and body['state'] not in _EXCLUDED_STATES:
                        body['state'] = 'trace_disabled'
                    return
                body['observed_bytes'] += len(chunk)
                if (body['state'] in ('quota', 'trace_disabled', 'error')
                        or body['state'] in _EXCLUDED_STATES or not chunk):
                    return
                self._capture(side, body, chunk)
        except Exception:
            self.finish_body(side, 'error')

    def finish_body(self, side, state):
        try:
            with self.capture._lock:
                body = self.entry[side + '_body']
                if side in self._probes:
                    if _trace_active() and state == 'complete':
                        self._capture(side, body, b'', final=True)
                    else:
                        self._probes.pop(side, None)
                if (body['state'] not in ('quota', 'trace_disabled', 'error')
                        and body['state'] not in _EXCLUDED_STATES):
                    body['state'] = state if _trace_active() else 'trace_disabled'
        except Exception:
            pass

    def response(self, response):
        try:
            if sum(len(k) + len(v) for k, v in response.headers.raw) > self.capture.metadata_limit:
                with self.capture._lock:
                    self.entry['response'] = dict(status_code=response.status_code, state='quota')
                return
            metadata = dict(status_code=response.status_code,
                            headers=[[k.decode('latin-1'), v.decode('latin-1')]
                                     for k, v in response.headers.raw])
            size = len(json.dumps(metadata).encode())
            with self.capture._lock:
                if not _trace_active():
                    self.entry['response'] = dict(state='trace_disabled')
                elif size > self.capture.metadata_limit or self.capture._used + size > self.capture.session_limit:
                    self.entry['response'] = dict(status_code=response.status_code, state='quota')
                else:
                    self.capture._used += size
                    self.entry['response'] = metadata
        except Exception:
            pass

    def terminal(self, outcome, exc=None):
        try:
            with self.capture._lock:
                if self.entry['outcome'] != 'in_progress':
                    return
                self.entry.update(outcome=outcome,
                                  duration_ms=round((time.monotonic() - self.started) * 1000, 3))
                if exc is not None:
                    # Do not retain arbitrary unbounded exception strings.
                    self.entry['error_type'] = type(exc).__name__[:128]
        except Exception:
            pass


class _SyncStream(httpx.SyncByteStream):
    def __init__(self, stream, exchange, side):
        self.stream, self.exchange, self.side = stream, exchange, side
        self.complete = False

    def __iter__(self):
        try:
            for chunk in self.stream:
                self.exchange.observe(self.side, chunk)
                yield chunk
            self.complete = True
            self.exchange.finish_body(self.side, 'complete')
            if self.side == 'response':
                self.exchange.terminal('complete')
        except GeneratorExit:
            raise
        except BaseException as exc:
            self.exchange.finish_body(self.side, 'error')
            self.exchange.terminal('stream_error', exc)
            raise

    def close(self):
        try:
            self.stream.close()
        finally:
            if not self.complete:
                # Preserve not_read if the caller never iterated the body.
                if self.side == 'response':
                    self.exchange.terminal('closed_early')


class _AsyncStream(httpx.AsyncByteStream):
    def __init__(self, stream, exchange, side):
        self.stream, self.exchange, self.side = stream, exchange, side
        self.complete = False

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                self.exchange.observe(self.side, chunk)
                yield chunk
            self.complete = True
            self.exchange.finish_body(self.side, 'complete')
            if self.side == 'response':
                self.exchange.terminal('complete')
        except GeneratorExit:
            raise
        except BaseException as exc:
            self.exchange.finish_body(self.side, 'error')
            self.exchange.terminal('stream_error', exc)
            raise

    async def aclose(self):
        try:
            await self.stream.aclose()
        finally:
            if not self.complete and self.side == 'response':
                self.exchange.terminal('closed_early')


# Process-only singleton. No buffer to restore, purge, encrypt, or confuse with
# another live instance; a new interpreter always has a new empty session.
HTTP_TRACE = HTTPTraceCapture()


class TraceHTTPTransport(httpx.BaseTransport):
    """Explicit wrapper; HTTPX retains redirects/cookies/timeouts/decoding."""
    def __init__(self, transport=None, *, producer="core", capture=None, **options):
        if type(producer) is not str or not 0 < len(producer) <= 128:
            raise ValueError("producer must be a bounded string")
        self.transport = transport if transport is not None else httpx.HTTPTransport(**options)
        self.producer = producer
        self.capture = capture if capture is not None else HTTP_TRACE

    def handle_request(self, request):
        exchange = self.capture.begin(request, self.producer)
        if exchange is None:
            return self.transport.handle_request(request)
        original = request.stream
        request.stream = _SyncStream(original, exchange, 'request')
        try:
            response = self.transport.handle_request(request)
            exchange.response(response)
            if response.is_stream_consumed:
                exchange.entry['response_body']['representation'] = 'httpx_buffered_content'
                exchange.observe('response', response.content)
                exchange.finish_body('response', 'complete')
                exchange.terminal('complete')
            else:
                response.stream = _SyncStream(response.stream, exchange, 'response')
            return response
        except BaseException as exc:
            exchange.terminal('transport_error', exc)
            raise
        finally:
            request.stream = original

    def close(self):
        self.transport.close()


class TraceAsyncHTTPTransport(httpx.AsyncBaseTransport):
    """Async counterpart, including streaming, cancellation and send()."""
    def __init__(self, transport=None, *, producer="core", capture=None, **options):
        if type(producer) is not str or not 0 < len(producer) <= 128:
            raise ValueError("producer must be a bounded string")
        self.transport = transport if transport is not None else httpx.AsyncHTTPTransport(**options)
        self.producer = producer
        self.capture = capture if capture is not None else HTTP_TRACE

    async def handle_async_request(self, request):
        exchange = self.capture.begin(request, self.producer)
        if exchange is None:
            return await self.transport.handle_async_request(request)
        original = request.stream
        request.stream = _AsyncStream(original, exchange, 'request')
        try:
            response = await self.transport.handle_async_request(request)
            exchange.response(response)
            if response.is_stream_consumed:
                exchange.entry['response_body']['representation'] = 'httpx_buffered_content'
                exchange.observe('response', response.content)
                exchange.finish_body('response', 'complete')
                exchange.terminal('complete')
            else:
                response.stream = _AsyncStream(response.stream, exchange, 'response')
            return response
        except BaseException as exc:
            exchange.terminal('transport_error', exc)
            raise
        finally:
            request.stream = original

    async def aclose(self):
        await self.transport.aclose()


class TraceHTTPClient(httpx.Client):
    """Explicit HTTPX client preserving native proxy/mount/SSL selection.

    HTTPX is pinned to 0.28.x. This one dispatch override is tested against
    that version; it wraps the transport selected by HTTPX, never rebuilds
    options, changes class globals or mutates a client's transport mappings.
    Client.close() remains the sole owner of the underlying transports.
    """
    def __init__(self, *, producer="core", capture=None, **options):
        if type(producer) is not str or not 0 < len(producer) <= 128:
            raise ValueError("producer must be a bounded string")
        self._trace_producer = producer
        self._trace_capture = capture if capture is not None else HTTP_TRACE
        options["headers"] = _browser_headers(options.get("headers"))
        super().__init__(**options)

    def _transport_for_url(self, url):
        transport = super()._transport_for_url(url)
        if isinstance(transport, TraceHTTPTransport):
            return transport
        return TraceHTTPTransport(transport, producer=self._trace_producer,
                                  capture=self._trace_capture)


class TraceAsyncHTTPClient(httpx.AsyncClient):
    """Async counterpart with native HTTPX proxy/mount/SSL selection."""
    def __init__(self, *, producer="core", capture=None, **options):
        if type(producer) is not str or not 0 < len(producer) <= 128:
            raise ValueError("producer must be a bounded string")
        self._trace_producer = producer
        self._trace_capture = capture if capture is not None else HTTP_TRACE
        options["headers"] = _browser_headers(options.get("headers"))
        super().__init__(**options)

    def _transport_for_url(self, url):
        transport = super()._transport_for_url(url)
        if isinstance(transport, TraceAsyncHTTPTransport):
            return transport
        return TraceAsyncHTTPTransport(transport, producer=self._trace_producer,
                                       capture=self._trace_capture)


class ContentTransferUnavailable(ValueError):
    """A transient error observed by the attested HTTP content transport.

    Only the caller knows whether the source operation can use a fresh
    offer. This exception does not, by itself, qualify another attempt.
    """


class _HTTPXTransportService:
    """Core-owned construction service; clients keep independent cookies/pools."""

    def client(self, *, producer: str, **options) -> httpx.Client:
        if 'capture' in options:
            raise TypeError("The Core HTTPX service owns the capture session")
        return TraceHTTPClient(producer=producer, **options)

    def public_client(self, *, producer: str, timeout: float = 15) -> httpx.Client:
        """Create an isolated, non-proxied client pinned to public IP peers.

        Each redirect remains an explicit caller decision. No transport,
        mount, proxy or SSL override can bypass public-peer validation.
        """
        import math
        from .public_httpx_transport import PublicOnlyHTTPTransport
        try:
            valid_timeout = type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ValueError("A finite positive public HTTP timeout is required")
        return TraceHTTPClient(producer=producer, timeout=timeout,
                               trust_env=False, follow_redirects=False,
                               transport=PublicOnlyHTTPTransport())

    def trace_enabled(self) -> bool:
        """Read-only, process-local opt-in gate for sensitive provider traces."""
        return _trace_active()

    def attested_https_download(
        self, *, producer: str, url: str, target: Path,
        admission_current, headers=None, timeout: float = 20,
        max_bytes: int = 512 * 1024 * 1024, max_redirects: int = 8,
        max_duration: float = 900, on_url_refusal=None,
        on_trace_url=None,
    ):
        """Transport only for an already-admitted, source-bound HTTPS GET.

        This service never authorizes acquisition or publishes a Placement.
        The Core admission callback MUST recheck source/account/consent; the
        selected-delivery caller MUST still verify content and publish via B.
        """
        from .core.attested_https_delivery import download_attested_https
        return download_attested_https(
            producer=producer, url=url, target=target,
            admission_current=admission_current, headers=headers,
            timeout=timeout, max_bytes=max_bytes,
            max_redirects=max_redirects, max_duration=max_duration,
            on_url_refusal=on_url_refusal, on_trace_url=on_trace_url,
        )

    def async_client(self, *, producer: str, **options) -> httpx.AsyncClient:
        if 'capture' in options:
            raise TypeError("The Core HTTPX service owns the capture session")
        return TraceAsyncHTTPClient(producer=producer, **options)


HTTPX_TRANSPORT = _HTTPXTransportService()
