"""Bounded, opt-in HTTPX transport observations for the current process only.

No global HTTPX patch, eager stream reads, network replay or on-disk buffer.
Bodies are transport bytes, before HTTPX response decompression. Providers opt in
by constructing a public TraceHTTPTransport / TraceAsyncHTTPTransport.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
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
    if "user-agent" not in headers:
        headers["User-Agent"] = BROWSER_USER_AGENT
    return headers


def _trace_active() -> bool:
    from .logging_utils import diagnostics_level
    return diagnostics_level() == "trace"


class HTTPTraceCapture:
    """Thread-safe bounded RAM archive; it cannot survive a process restart.

    Capture failures never escape into the business request. Export is an
    explicit separate operation and reports its own failures to its caller.
    """

    def __init__(self, *, body_limit=2 * 1024 * 1024,
                 session_limit=32 * 1024 * 1024, exchange_limit=1000,
                 metadata_limit=64 * 1024):
        for value in (body_limit, session_limit, exchange_limit, metadata_limit):
            if type(value) is not int or value < 1:
                raise ValueError("Capture budgets must be positive integers")
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

    def status(self):
        """Small aggregate view, without copying bodies for a UI refresh."""
        with self._lock:
            return dict(active=_trace_active(), session_ref=self.session_ref,
                        exchanges=len(self._exchanges), used_bytes=self._used,
                        omitted_exchanges=self._omitted)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(dict(
                schema="cifereca.http-trace/1", session_ref=self.session_ref,
                representation="transport_bytes_before_response_decompression",
                omitted_exchanges=self._omitted, used_bytes=self._used,
                limits=dict(body_bytes=self.body_limit, session_bytes=self.session_limit,
                            exchanges=self.exchange_limit, metadata_bytes=self.metadata_limit),
                exchanges=self._exchanges))

    def export(self, destination: str | Path):
        """Publish a new ZIP atomically, never replace a file or follow its link.

        Only call following the application's explicit raw export confirmation.
        A private temporary ZIP is removed on failure. Existing destinations
        require a new filename; no partial export is published.
        """
        if not _trace_active():
            raise ValueError("HTTP export requires active Trace")
        snapshot = self.snapshot()
        destination = Path(destination)
        fd, temporary = tempfile.mkstemp(prefix=".cifereca-http-", dir=destination.parent)
        try:
            with os.fdopen(fd, "w+b") as output:
                with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
                    for entry in snapshot['exchanges']:
                        for side in ('request', 'response'):
                            body = entry[side + '_body']
                            content = bytes(body.pop('data'))
                            name = f"bodies/{entry['exchange_ref']}-{side}.bin"
                            body.update(blob=name, stored_bytes=len(content),
                                        sha256=hashlib.sha256(content).hexdigest())
                            archive.writestr(name, content)
                    archive.writestr("index.json", json.dumps(snapshot, ensure_ascii=True, indent=2))
                output.flush()
                os.fsync(output.fileno())
            # Hardlink publication is atomic, exclusive and same-filesystem.
            os.link(temporary, destination, follow_symlinks=False)
        finally:
            os.unlink(temporary)
        return destination


class _Exchange:
    def __init__(self, capture, entry):
        self.capture, self.entry = capture, entry
        self.started = time.monotonic()

    def observe(self, side, chunk):
        try:
            with self.capture._lock:
                body = self.entry[side + '_body']
                if not _trace_active():
                    if body['state'] not in ('complete', 'quota', 'error'):
                        body['state'] = 'trace_disabled'
                    return
                if body['state'] in ('quota', 'trace_disabled', 'error'):
                    body['observed_bytes'] += len(chunk)
                    return
                body['observed_bytes'] += len(chunk)
                available = min(self.capture.body_limit - len(body['data']),
                                self.capture.session_limit - self.capture._used)
                saved = chunk[:max(0, available)]
                body['data'].extend(saved)
                self.capture._used += len(saved)
                body['state'] = 'partial' if len(saved) == len(chunk) else 'quota'
        except Exception:
            self.finish_body(side, 'error')

    def finish_body(self, side, state):
        try:
            with self.capture._lock:
                body = self.entry[side + '_body']
                if body['state'] not in ('quota', 'trace_disabled', 'error'):
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

    def attested_https_download(
        self, *, producer: str, url: str, target: Path,
        admission_current, headers=None, timeout: float = 20,
        max_bytes: int = 512 * 1024 * 1024, max_redirects: int = 8,
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
            max_redirects=max_redirects,
        )

    def async_client(self, *, producer: str, **options) -> httpx.AsyncClient:
        if 'capture' in options:
            raise TypeError("The Core HTTPX service owns the capture session")
        return TraceAsyncHTTPClient(producer=producer, **options)


HTTPX_TRANSPORT = _HTTPXTransportService()
