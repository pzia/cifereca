from __future__ import annotations

import logging
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
from .cover_preview import create_preview, preview_path
import hashlib
import itertools
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit, unquote
import re
from ..epub_metadata import cover_cache_dir, MAX_IMAGE

import httpx
from ..http_trace import HTTPX_TRANSPORT

from ..diagnostics import Diagnostics
from ..paths import cache_dir


_TRACE_LOG = logging.getLogger("cifereca")

CACHE_SCHEMA = "v2"


def _cover_failure_code(exc: BaseException) -> str:
    """Fixed diagnostic vocabulary; never inspect an exception's text or URL."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_status"
    if isinstance(exc, httpx.RequestError):
        return "request_failed"
    if isinstance(exc, OSError):
        return "storage_failed"
    if isinstance(exc, ValueError):
        return "invalid_image"
    return "unexpected"



@dataclass
class _Callback:
    fn: Callable[[Path | None], None]
    active: bool = True


class CoverRequest:
    """Poignée légère permettant à une cellule GTK recyclée d'ignorer un résultat ancien."""

    def __init__(self, callback: _Callback):
        self._callback = callback

    def cancel(self) -> None:
        self._callback.active = False
        child=getattr(self,"_source_request",None)
        if child is not None:child.cancel()


class CoverLoader:
    """Pipeline de couvertures asynchrone, borné, validé et mis en cache.

    0.2.2 introduit un nouveau namespace de cache afin de ne jamais réutiliser les
    fichiers ``.img`` potentiellement invalides créés par les premières versions.
    Un cache-hit n'est compté qu'après validation de la signature binaire.
    """

    def __init__(
        self,
        base_url: str = "",
        diagnostics: Diagnostics | None = None,
        max_workers: int = 6,
    ):
        self.base_url = base_url.rstrip("/")
        self.diagnostics = diagnostics
        self.root = cache_dir() / f"covers-{CACHE_SCHEMA}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_workers = max(2, int(max_workers))
        headers = {
            # Evite AVIF : certaines piles GTK Linux ne savent pas le décoder.
            "Accept": "image/jpeg,image/png,image/webp,image/gif,image/svg+xml,*/*;q=0.1",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        }
        if self.base_url:
            headers["Referer"] = self.base_url + "/"
        self.client = HTTPX_TRANSPORT.client(
            producer="core.cover_loader",
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=True,
            headers=headers,
            limits=httpx.Limits(
                max_connections=self.max_workers,
                max_keepalive_connections=self.max_workers,
                keepalive_expiry=20.0,
            ),
            http1=True,
            http2=False,
        )
        self._queue: queue.PriorityQueue[tuple[int, int, str]] = queue.PriorityQueue()
        self._sequence = itertools.count()
        self._callbacks: dict[str, list[_Callback]] = {}
        self._queued: set[str] = set()
        self._fail_until: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._stats = {
            "preview_created":0,"preview_hit":0,"preview_fallback":0,
            "requested": 0,
            "cache_hit_valid": 0,
            "cache_invalid": 0,
            "download_ok": 0,
            "download_failed": 0,
            "decode_failed": 0,
            "ui_applied": 0,
            "ui_stale": 0,
            "ui_failed": 0,
            "ui_decode_count": 0,
            "ui_decode_total_ms": 0.0,
            "ui_decode_max_ms": 0.0,
            "ui_decode_slow": 0,
            "deduplicated": 0,
            "cancelled_callbacks": 0,
            "data_url_ok": 0,
            "data_url_invalid": 0,
        }
        self.preview_root=self.root/"previews-v1";self.preview_root.mkdir(exist_ok=True)
        self._preview_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix="cifereca-preview")
        self._preview_jobs={}
        self._last_report_total = 0
        for idx in range(self.max_workers):
            thread = threading.Thread(target=self._worker, name=f"cifereca-cover-{idx + 1}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def close(self) -> None:
        self._stop.set()
        for _ in self._threads:
            self._queue.put((10_000, next(self._sequence), ""))
        for thread in self._threads:
            thread.join(timeout=0.35)
        self._preview_pool.shutdown(wait=False,cancel_futures=True)
        self.client.close()

    def stats(self) -> dict[str, int | float | str]:
        with self._lock:
            stats = dict(self._stats)
            count = int(stats.get("ui_decode_count") or 0)
            total = float(stats.pop("ui_decode_total_ms", 0.0) or 0.0)
            stats["ui_decode_avg_ms"] = round(total / count, 2) if count else 0.0
            stats["ui_decode_max_ms"] = round(float(stats.get("ui_decode_max_ms") or 0.0), 2)
            return {
                **stats,
                "queue": self._queue.qsize(),
                "inflight_urls": len(self._callbacks),
                "workers": self.max_workers,
                "cache_schema": CACHE_SCHEMA,
            }

    def _absolute(self, url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url
        return urljoin(self.base_url + "/", url) if self.base_url else url

    def _path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.root / f"{key}.img"

    @staticmethod
    def _supported_image_bytes(data: bytes) -> bool:
        """Validation légère avant qu'un fichier ne soit considéré comme du cache image.

        Le but n'est pas de décoder complètement l'image mais d'éliminer les pages
        HTML, JSON, AVIF/HEIC et anciens fichiers corrompus avant de les envoyer à GTK.
        """
        if len(data) < 8:
            return False
        if data.startswith(b"\xff\xd8\xff"):  # JPEG
            return True
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return True
        if data.startswith((b"GIF87a", b"GIF89a")):
            return True
        if data.startswith(b"BM"):
            return True
        if data[:4] in (b"II*\x00", b"MM\x00*"):
            return True
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return True
        # SVG public : assez rare, mais GTK sait généralement l'ouvrir via librsvg.
        prefix = data[:1024].lstrip().lower()
        if prefix.startswith(b"<svg") or (prefix.startswith(b"<?xml") and b"<svg" in prefix):
            return True
        # ISO-BMFF AVIF/HEIF : volontairement rejeté pour compatibilité GTK.
        if len(data) >= 16 and data[4:8] == b"ftyp":
            return False
        return False

    @staticmethod
    def _decode_data_url(url: str) -> bytes | None:
        """Decode a bounded, supported image data URI without network access."""
        try:
            header, payload = url.split(",", 1)
        except ValueError:
            return None
        metadata = header[5:].split(";") if header.startswith("data:") else []
        if not metadata:
            return None
        mime = metadata[0].strip().lower()
        supported = {
            "image/jpeg", "image/jpg", "image/png", "image/gif",
            "image/webp", "image/svg+xml",
        }
        flags = {part.strip().lower() for part in metadata[1:] if part.strip()}
        if mime not in supported or "base64" not in flags:
            return None
        max_encoded = ((MAX_IMAGE + 2) // 3) * 4 + 4
        if len(payload) > max_encoded:
            return None
        try:
            data = base64.b64decode(payload, validate=True)
        except (ValueError, binascii.Error):
            return None
        if not data or len(data) > MAX_IMAGE:
            return None
        return data if CoverLoader._supported_image_bytes(data[:1024]) else None

    def _valid_cache_file(self, path: Path) -> bool:
        try:
            if not path.exists() or path.stat().st_size <= 0:
                return False
            with path.open("rb") as fh:
                head = fh.read(1024)
            return self._supported_image_bytes(head)
        except OSError:
            return False

    def note_ui_applied(self) -> None:
        with self._lock:
            self._stats["ui_applied"] += 1

    def note_ui_stale(self) -> None:
        with self._lock:
            self._stats["ui_stale"] += 1

    def note_ui_failed(self) -> None:
        with self._lock:
            self._stats["ui_failed"] += 1

    def note_ui_decode(self, duration_ms: float) -> None:
        value = max(0.0, float(duration_ms))
        with self._lock:
            self._stats["ui_decode_count"] += 1
            self._stats["ui_decode_total_ms"] += value
            self._stats["ui_decode_max_ms"] = max(float(self._stats["ui_decode_max_ms"]), value)
            if value >= 30.0:
                self._stats["ui_decode_slow"] += 1
        if value >= 30.0:
            _TRACE_LOG.info("TRACE ui-perf slow operation=cover_decode duration_ms=%.1f", value)

    def invalidate(self, path: Path | None, *, decode_failure: bool = False) -> None:
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        with self._lock:
            if decode_failure:
                self._stats["decode_failed"] += 1
            else:
                self._stats["cache_invalid"] += 1

    def load(
        self,
        url: str | None,
        callback: Callable[[Path | None], None],
        *,
        priority: int = 10,
        force: bool = False,
    ) -> CoverRequest:
        holder = _Callback(callback)
        request = CoverRequest(holder)
        if not url:
            callback(None)
            return request

        if url.startswith("data:"):
            path = self._path(url)
            with self._lock:
                self._stats["requested"] += 1
            if not force and path.exists():
                if self._valid_cache_file(path):
                    with self._lock:
                        self._stats["cache_hit_valid"] += 1
                    callback(path)
                    self._maybe_report()
                    return request
                self.invalidate(path)

            data = self._decode_data_url(url)
            if data is None:
                with self._lock:
                    self._stats["data_url_invalid"] += 1
                callback(None)
                self._maybe_report()
                return request
            try:
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(path)
            except OSError:
                try:
                    path.with_suffix(".tmp").unlink(missing_ok=True)
                except OSError:
                    pass
                with self._lock:
                    self._stats["data_url_invalid"] += 1
                callback(None)
                self._maybe_report()
                return request
            with self._lock:
                self._stats["data_url_ok"] += 1
            callback(path)
            self._maybe_report()
            return request

        if url.startswith('file:'):
            # Only Core-published cover-cache files are accepted, never arbitrary local URIs.
            parsed=urlsplit(url);local=Path(unquote(parsed.path))
            root=cover_cache_dir().resolve()
            valid=False
            try:
                valid=bool(not parsed.netloc and not parsed.query and not parsed.fragment
                    and not local.is_symlink() and local.resolve().parent==root
                    and re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|gif|webp|svg)',local.name)
                    and local.stat().st_size<=MAX_IMAGE and self._valid_cache_file(local))
            except OSError:pass
            with self._lock:
                self._stats['requested']+=1
                self._stats['cache_hit_valid' if valid else 'cache_invalid']+=1
            callback(local if valid else None)
            return request

        absolute = self._absolute(url)
        path = self._path(absolute)
        with self._lock:
            self._stats["requested"] += 1

        if not force and path.exists():
            if self._valid_cache_file(path):
                with self._lock:
                    self._stats["cache_hit_valid"] += 1
                callback(path)
                self._maybe_report()
                return request
            self.invalidate(path)

        now = time.monotonic()
        with self._lock:
            failed_until = self._fail_until.get(absolute, 0.0)
            if not force and failed_until > now:
                callback(None)
                return request
            callbacks = self._callbacks.setdefault(absolute, [])
            callbacks.append(holder)
            if absolute in self._queued:
                self._stats["deduplicated"] += 1
                return request
            self._queued.add(absolute)

        self._queue.put((max(0, int(priority)), next(self._sequence), absolute))
        return request

    def load_preview(self,url,callback,*,width,height,priority=10,force=False):
        holder=_Callback(callback);request=CoverRequest(holder)
        def deliver(path):
            if holder.active and not self._stop.is_set():callback(path)
        def source_ready(source):
            if not holder.active or self._stop.is_set():return
            if source is None:deliver(None);return
            def work():
                try:
                    dest=preview_path(source,self.preview_root,width,height)
                    if not force and self._valid_cache_file(dest):
                        with self._lock:self._stats['preview_hit']+=1
                        return dest
                    create_preview(source,dest,width,height)
                    with self._lock:self._stats['preview_created']+=1
                    return dest
                except Exception as exc:
                    with self._lock:self._stats['preview_fallback']+=1
                    _TRACE_LOG.debug('TRACE cover preview fallback error=%s',type(exc).__name__)
                    return source
            if self._stop.is_set():return
            # Bound pending work by key, not one task per recycled cell.
            key=(str(source),width,height,force)
            with self._lock:
                future=self._preview_jobs.get(key)
                if future is None:
                    if len(self._preview_jobs)>=64:
                        self._stats['preview_fallback']+=1
                    else:
                        try:future=self._preview_pool.submit(work)
                        except RuntimeError:pass
                        if future is not None:self._preview_jobs[key]=future
            if future is None:deliver(None);return
            def done(f):
                with self._lock:
                    if self._preview_jobs.get(key) is f:self._preview_jobs.pop(key,None)
                if not f.cancelled():deliver(f.result())
            future.add_done_callback(done)
        request._source_request=self.load(url,source_ready,priority=priority,force=force)
        return request

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                _priority, _seq, absolute = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if not absolute or self._stop.is_set():
                    continue
                path = self._path(absolute)
                result: Path | None = None
                error: Exception | None = None
                started = time.perf_counter()
                try:
                    response = self.client.get(absolute)
                    response.raise_for_status()
                    data = response.content
                    content_type = response.headers.get("content-type", "").lower()
                    if not data:
                        raise ValueError("empty image body")
                    if "text/html" in content_type or "application/json" in content_type:
                        raise ValueError(f"unexpected content-type {content_type}")
                    if not self._supported_image_bytes(data[:1024]):
                        raise ValueError(f"unsupported or invalid image ({content_type or 'no content-type'})")
                    tmp = path.with_suffix(".tmp")
                    tmp.write_bytes(data)
                    tmp.replace(path)
                    result = path
                    # The URL, even its host/path, and MIME headers can be
                    # private or provider-controlled. Emit only a safe count.
                    _TRACE_LOG.debug("TRACE cover download ok bytes=%s", len(data))
                    with self._lock:
                        self._stats["download_ok"] += 1
                        self._fail_until.pop(absolute, None)
                except Exception as exc:  # le fallback visuel gère l'échec
                    error = exc
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    _TRACE_LOG.debug("TRACE cover download failed reason=%s", _cover_failure_code(exc))
                    with self._lock:
                        self._stats["download_failed"] += 1
                        self._fail_until[absolute] = time.monotonic() + 90.0

                elapsed = int((time.perf_counter() - started) * 1000)
                with self._lock:
                    callbacks = self._callbacks.pop(absolute, [])
                    self._queued.discard(absolute)
                for item in callbacks:
                    if not item.active:
                        with self._lock:
                            self._stats["cancelled_callbacks"] += 1
                        continue
                    try:
                        item.fn(result)
                    except Exception:
                        pass

                if error is not None and self.diagnostics is not None:
                    # Typed Core failure: no provider/loan/HTTP status inferred.
                    # Visual fallback and cache cooldown remain unchanged.
                    self.diagnostics.add_core_cover_failure(
                        _cover_failure_code(error),
                        duration_ms=min(600000, max(0, elapsed)),
                    )
                self._maybe_report()
            finally:
                self._queue.task_done()

    def _maybe_report(self) -> None:
        if self.diagnostics is None:
            return
        snapshot = self.stats()
        completed = (
            int(snapshot["cache_hit_valid"])
            + int(snapshot["cache_invalid"])
            + int(snapshot["download_ok"])
            + int(snapshot["download_failed"])
            + int(snapshot["decode_failed"])
            + int(snapshot["data_url_ok"])
            + int(snapshot["data_url_invalid"])
        )
        if completed - self._last_report_total < 20:
            return
        self._last_report_total = completed
        self.diagnostics.add(
            "COVER",
            "pipeline",
            "ok",
            detail=snapshot,
            level="detailed",
        )
