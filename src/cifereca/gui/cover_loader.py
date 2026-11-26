from __future__ import annotations

import logging
from collections import OrderedDict, deque
import math
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
from .cover_preview import create_preview, preview_path
import hashlib
import itertools
import os
import stat
import tempfile
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit, unquote
import re
from ..epub_metadata import cover_cache_dir, MAX_IMAGE, valid_cached_cover_path
from ..core.epub_cover_materialization import (
    EpubCoverPhysicalCache, ExactEpubCoverResource,
)

import httpx
from ..http_trace import HTTPX_TRANSPORT

from ..diagnostics import Diagnostics
from ..paths import cache_dir


_TRACE_LOG = logging.getLogger("cifereca")

CACHE_SCHEMA = "v2"
_UI_DECODE_WINDOW = 1024
_N3_URL_WINDOW = 1024
# Fixed installation-wide aggregate buckets: no URL, host, response body,
# account, provider or per-request identity. Only actual response status.
_N3_HTTP_FAILURE_BUCKETS = ("401", "403", "404", "429", "other_4xx", "5xx", "other", "unknown")


def _n3_http_failure_bucket(exc: httpx.HTTPStatusError) -> str:
    status = getattr(exc.response, "status_code", None)
    if type(status) is not int or not 100 <= status <= 599:
        return "unknown"
    if status in (401, 403, 404, 429):
        return str(status)
    if 400 <= status <= 499:
        return "other_4xx"
    if 500 <= status <= 599:
        return "5xx"
    return "other"



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


@dataclass(frozen=True, slots=True)
class CoverMediaState:
    """N3 physical evidence only; never a book, source or entitlement join.

    `source_cached` and `preview_cached` mean validated signatures on disk,
    not a GTK decode success. No URL, path, profile or cache key is exported.
    """

    source_kind: str
    source_cached: bool
    preview_cached: bool
    width: int | None = None
    height: int | None = None


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
        # Exact direct HTTP absence, not a generic failure/cooldown. Keep
        # only a bounded in-memory receipt for optional N2 requalification.
        self._http_missing_until: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._ui_decode_recent: deque[float] = deque(maxlen=_UI_DECODE_WINDOW)
        self._n3_url_recent = {outcome: deque(maxlen=_N3_URL_WINDOW)
                               for outcome in ("ok", "failed")}
        self._preview_recent = {outcome: deque(maxlen=1024)
                                for outcome in ("hit", "created", "fallback")}
        self._stats = {
            "preview_created":0,"preview_hit":0,"preview_fallback":0,
            "requested": 0,
            "cache_hit_valid": 0,
            "cache_invalid": 0,
            "download_ok": 0,
            "download_failed": 0,
            **{f"n3_url_http_failure_{bucket}": 0 for bucket in _N3_HTTP_FAILURE_BUCKETS},
            "n3_url_published_bytes": 0,
            "n3_url_ok_total_ms": 0.0,
            "n3_url_ok_max_ms": 0.0,
            "n3_url_failed_total_ms": 0.0,
            "n3_url_failed_max_ms": 0.0,
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
        self._epub_physical = EpubCoverPhysicalCache(max_workers=2)
        self._preview_jobs={}
        # Bounded, key-scoped subscribers let queued preview work skip
        # expensive image decoding after rapid GTK cell recycling.
        self._preview_waiters={}
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
        self._epub_physical.close()
        self.client.close()

    def stats(self) -> dict[str, int | float | str]:
        # N3 EPUB counters are installation-wide media observations, never
        # document ownership or provider-account statistics.
        physical = self._epub_physical.stats()
        with self._lock:
            stats = dict(self._stats)
            count = int(stats.get("ui_decode_count") or 0)
            total = float(stats.pop("ui_decode_total_ms", 0.0) or 0.0)
            stats["ui_decode_avg_ms"] = round(total / count, 2) if count else 0.0
            stats["ui_decode_max_ms"] = round(float(stats.get("ui_decode_max_ms") or 0.0), 2)
            # Nearest-rank percentiles of the bounded recent N4 sample, not
            # lifetime percentiles or N2/N3 latency. Zero samples is explicit.
            recent = sorted(self._ui_decode_recent)
            stats["ui_decode_recent_count"] = len(recent)
            if recent:
                stats["ui_decode_recent_p50_ms"] = round(recent[(len(recent) * 50 + 99) // 100 - 1], 2)
                stats["ui_decode_recent_p95_ms"] = round(recent[(len(recent) * 95 + 99) // 100 - 1], 2)
            # One sample per actual worker attempt, not per subscriber or
            # cache lookup. Includes transfer, validation and disk publication
            # (or failure cleanup); excludes queue time, preview and GTK decode.
            for outcome in ("ok", "failed"):
                prefix = f"n3_url_{outcome}"
                attempts = int(stats[f"download_{outcome}"])
                total_ms = float(stats.pop(f"{prefix}_total_ms"))
                stats[f"{prefix}_avg_ms"] = round(total_ms / attempts, 2) if attempts else 0.0
                stats[f"{prefix}_max_ms"] = round(float(stats[f"{prefix}_max_ms"]), 2)
                recent = sorted(self._n3_url_recent[outcome])
                stats[f"{prefix}_recent_count"] = len(recent)
                if recent:
                    stats[f"{prefix}_recent_p50_ms"] = round(recent[(len(recent) * 50 + 99) // 100 - 1], 2)
                    stats[f"{prefix}_recent_p95_ms"] = round(recent[(len(recent) * 95 + 99) // 100 - 1], 2)
            # Preview worker samples exclude queue delay, URL/EPUB retrieval
            # and GTK decode. Admission overflow is not a worker fallback.
            for outcome, window in self._preview_recent.items():
                prefix = f"preview_worker_{outcome}"
                recent = sorted(window)
                stats[f"{prefix}_recent_count"] = count = len(recent)
                if count:
                    stats[f"{prefix}_recent_p50_ms"] = round(recent[(count * 50 + 99) // 100 - 1], 2)
                    stats[f"{prefix}_recent_p95_ms"] = round(recent[(count * 95 + 99) // 100 - 1], 2)
            return {
                **stats,
                "queue": self._queue.qsize(),
                "inflight_urls": len(self._callbacks),
                "workers": self.max_workers,
                "cache_schema": CACHE_SCHEMA,
                **{key: value for key, value in physical.items()
                   if key.startswith("n3_epub_")},
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

    @staticmethod
    def _publish_image(path: Path, data: bytes | bytearray) -> None:
        """Publish N3 bytes atomically without following a predictable temp link.

        A fixed <digest>.tmp can be pre-created as a symlink (or a FIFO).
        mkstemp creates an exclusive regular file in the same cache directory;
        os.replace then atomically replaces the leaf, not its symlink target.
        """
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent,
        )
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _valid_cache_file(self, path: Path) -> bool:
        """N3 media validity, not authority linking a DocumentRef to an URL.

        Refuse leaf symlinks and verify the SAME opened descriptor: a cached
        thumbnail must not jump to another file between stat and read, even
        when all profiles share cifereca's physical media store.
        """
        try:
            fd = os.open(
                path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(fd, "rb") as fh:
                info = os.fstat(fh.fileno())
                if (not stat.S_ISREG(info.st_mode)
                        or not 0 < info.st_size <= MAX_IMAGE):
                    return False
                head = fh.read(1024)
            return self._supported_image_bytes(head)
        except OSError:
            return False

    def probe_media(
        self, url: str | None, *, width: int | None = None,
        height: int | None = None,
    ) -> CoverMediaState:
        """Read-only N3 cache snapshot for an already-resolved cover URI.

        Checks the same binary cache namespace and HiDPI preview key used
        by load()/load_preview(). The probe never downloads, scans an EPUB,
        caches a decision or associates any book with the image. A source
        file can disappear between this snapshot and its GTK consumption.
        """
        if ((width is None) != (height is None)
                or (width is not None and (
                    type(width) is not int or not 1 <= width <= 2048
                    or type(height) is not int or not 1 <= height <= 2048))):
            raise ValueError("invalid cover preview dimensions")
        if type(url) is not str or not url:
            return CoverMediaState("none", False, False, width, height)

        path: Path | None = None
        kind = "unsupported"
        try:
            if url.startswith("data:"):
                kind = "data"
                # A data URI can occupy substantial memory; probing only
                # tests the already-built content-hash path, never decodes it.
                path = self._path(url)
            elif url.startswith("file:"):
                kind = "embedded"
                parsed = urlsplit(url)
                local = Path(unquote(parsed.path))
                root = cover_cache_dir(create=False).resolve()
                if (not parsed.netloc and not parsed.query
                        and not parsed.fragment and not local.is_symlink()
                        and local.resolve().parent == root
                        and re.fullmatch(
                            r"[a-f0-9]{64}\.(png|jpg|gif|webp|svg)", local.name,
                        )):
                    path = local
            else:
                absolute = self._absolute(url)
                parsed = urlsplit(absolute)
                if (parsed.scheme in ("http", "https") and parsed.hostname
                        and parsed.username is None and parsed.password is None):
                    kind = "remote"
                    path = self._path(absolute)
        except (OSError, ValueError, TypeError):
            path = None

        source_cached = bool(
            path is not None
            and (valid_cached_cover_path(path) if kind == "embedded"
                 else self._valid_cache_file(path))
        )
        preview_cached = False
        if source_cached and width is not None and height is not None:
            try:
                preview = preview_path(path, self.preview_root, width, height)
                preview_cached = self._valid_cache_file(preview)
            except (OSError, ValueError):
                pass
        return CoverMediaState(
            kind, source_cached, preview_cached, width, height,
        )

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
        value = float(duration_ms)
        if not math.isfinite(value) or value < 0:
            return
        with self._lock:
            self._ui_decode_recent.append(value)
            self._stats["ui_decode_count"] += 1
            self._stats["ui_decode_total_ms"] += value
            self._stats["ui_decode_max_ms"] = max(float(self._stats["ui_decode_max_ms"]), value)
            if value >= 30.0:
                self._stats["ui_decode_slow"] += 1
        if value >= 30.0:
            _TRACE_LOG.info("TRACE ui-perf slow operation=cover_decode duration_ms=%.1f", value)

    def _owned_n3_path(self, path: Path) -> bool:
        """Reject arbitrary filesystem deletion from an image decode failure.

        A returned N3 path is a physical file *inside* the application's
        URL, preview or content-addressed EPUB cache. Path existence and
        image validity are irrelevant here: a corrupt image must still be
        removable, but another user file must never be unlinked.
        """
        if not isinstance(path, Path) or not path.is_absolute():
            return False
        try:
            parent = path.parent
            # A symlinked cache root must not redirect deletions elsewhere.
            if parent.is_symlink():
                return False
            root = parent.resolve(strict=True)
            if (root == self.root.resolve()
                    and not self.root.is_symlink()
                    and re.fullmatch(r"[a-f0-9]{64}\.img", path.name)):
                return True
            if (root == self.preview_root.resolve()
                    and not self.preview_root.is_symlink()
                    and re.fullmatch(r"[a-f0-9]{64}\.png", path.name)):
                return True
            epub_root = cover_cache_dir()
            if (root == epub_root.resolve()
                    and not epub_root.is_symlink()
                    and re.fullmatch(
                        r"[a-f0-9]{64}\.(?:png|jpg|gif|webp|svg)", path.name,
                    )):
                return True
        except (OSError, ValueError):
            return False
        return False

    def invalidate(self, path: Path | None, *, decode_failure: bool = False) -> None:
        if not self._owned_n3_path(path):
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

    def definitive_http_missing_ttl(self, url: str) -> float:
        """Remaining lifetime of a direct exact GET 404/410; no media IO."""
        if type(url) is not str or not url.startswith("https://"):
            return 0.0
        with self._lock:
            if self._stop.is_set():
                return 0.0
            return max(0.0, self._http_missing_until.get(url, 0.0) - time.monotonic())

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
                self._publish_image(path, data)
            except OSError:
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
            # Only Core-published EPUB cache images; verify the full digest
            # instead of merely accepting valid-looking replacement bytes.
            parsed=urlsplit(url)
            local=Path(unquote(parsed.path))
            valid=bool(
                not parsed.netloc and not parsed.query and not parsed.fragment
                and valid_cached_cover_path(local)
            )
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
                    self._http_missing_until.pop(absolute, None)
                callback(path)
                self._maybe_report()
                return request
            self.invalidate(path)

        now = time.monotonic()
        with self._lock:
            failed_until = self._fail_until.get(absolute, 0.0)
            cooling_down = not force and failed_until > now
            if not cooling_down:
                callbacks = self._callbacks.setdefault(absolute, [])
                callbacks.append(holder)
                if absolute in self._queued:
                    self._stats["deduplicated"] += 1
                    return request
                self._queued.add(absolute)
                # A newer real attempt supersedes the old absence receipt.
                self._http_missing_until.pop(absolute, None)

        if cooling_down:
            # A consumer may synchronously consult stats(), invalidate, or
            # request a different cover in its fallback callback. Invoking
            # it under _lock deadlocks the shared CoverLoader for all views.
            callback(None)
            return request
        self._queue.put((max(0, int(priority)), next(self._sequence), absolute))
        return request

    def load_exact_epub(
        self,
        source: ExactEpubCoverResource,
        *,
        source_path: Path,
        callback: Callable[[Path | None], None],
        width: int | None = None,
        height: int | None = None,
        priority: int = 10,
    ) -> CoverRequest:
        """N3 only: restore one already selected exact EPUB cover for GTK.

        Core MUST have verified the Artifact+Placement/rights before supplying
        this source and path. This method never chooses a book/cover, contacts
        suppliers, publishes N2 or falls back to a catalogue URL. The caller
        must recheck its N2 selected token and GTK bind epoch when applying a
        callback; a physical cache hit is not a documentary association.
        """
        if type(source) is not ExactEpubCoverResource:
            raise TypeError("expected exact EPUB cover resource")
        if not callable(callback):
            raise TypeError("expected EPUB cover callback")
        if ((width is None) != (height is None)
                or width is not None and (
                    type(width) is not int or type(height) is not int
                    or not 1 <= width <= 2048 or not 1 <= height <= 2048)):
            raise ValueError("invalid EPUB preview dimensions")
        holder = _Callback(callback)
        request = CoverRequest(holder)
        if self._stop.is_set():
            callback(None)
            return request

        # Same Core-owned N3 cache and single-flight used by all list/detail
        # requests. SourcePath is authorized by Core, not discovered by GTK.
        future = self._epub_physical.request(source, source_path=source_path)

        def physical_ready(done):
            if not holder.active or self._stop.is_set():
                return
            try:
                uri = done.result()
            except Exception:
                uri = None
            if uri is None:
                if holder.active and not self._stop.is_set():
                    callback(None)
                return

            def deliver(path):
                if holder.active and not self._stop.is_set():
                    callback(path)

            if width is None:
                child = self.load(uri, deliver, priority=priority)
            else:
                child = self.load_preview(
                    uri, deliver, width=width, height=height,
                    priority=priority,
                )
            request._source_request = child
            if not holder.active:
                child.cancel()

        future.add_done_callback(physical_ready)
        return request

    def _record_preview_worker(self, outcome, started):
        elapsed = (time.perf_counter() - started) * 1000
        with self._lock:
            self._stats["preview_" + outcome] += 1
            if math.isfinite(elapsed) and elapsed >= 0:
                self._preview_recent[outcome].append(elapsed)

    def load_preview(self, url, callback, *, width, height, priority=10, force=False):
        holder = _Callback(callback)
        request = CoverRequest(holder)

        def deliver(path):
            if holder.active and not self._stop.is_set():
                callback(path)

        def source_ready(source):
            if not holder.active or self._stop.is_set():
                return
            if source is None:
                deliver(None)
                return

            # One preview conversion per source/size, shared by all callers.
            key = (str(source), width, height, force)

            def work():
                # An already-queued conversion need not decode bytes if
                # every subscriber was canceled before its pool slot opened.
                # Read the shared subscribers under the same lock that admits
                # a new live request for this key.
                with self._lock:
                    needed = any(
                        subscriber.active
                        for subscriber in self._preview_waiters.get(key, ())
                    )
                if not needed or self._stop.is_set():
                    return None
                started = time.perf_counter()
                try:
                    dest = preview_path(source, self.preview_root, width, height)
                    if not force and self._valid_cache_file(dest):
                        self._record_preview_worker("hit", started)
                        return dest
                    create_preview(source, dest, width, height)
                    self._record_preview_worker("created", started)
                    return dest
                except Exception as exc:
                    self._record_preview_worker("fallback", started)
                    _TRACE_LOG.debug(
                        'TRACE cover preview fallback reason=%s',
                        _cover_failure_code(exc),
                    )
                    return source

            if self._stop.is_set():
                return
            with self._lock:
                future = self._preview_jobs.get(key)
                if future is None:
                    if len(self._preview_jobs) >= 64:
                        self._stats['preview_fallback'] += 1
                    else:
                        # Register the initial consumer BEFORE the worker can
                        # start; otherwise an eager pool would skip live work.
                        self._preview_waiters[key] = [holder]
                        try:
                            future = self._preview_pool.submit(work)
                        except RuntimeError:
                            self._preview_waiters.pop(key, None)
                        if future is not None:
                            self._preview_jobs[key] = future
                else:
                    self._preview_waiters.setdefault(key, []).append(holder)
            if future is None:
                deliver(None)
                return

            def done(f):
                with self._lock:
                    if self._preview_jobs.get(key) is f:
                        self._preview_jobs.pop(key, None)
                        self._preview_waiters.pop(key, None)
                if not f.cancelled():
                    deliver(f.result())

            future.add_done_callback(done)

        request._source_request = self.load(
            url, source_ready, priority=priority, force=force,
        )
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
                # A recycled GTK cell may cancel its request while the URL
                # waits behind other downloads. Claim and discard an entirely
                # cancelled batch atomically so a later live request for the
                # same URL can queue normally; never issue an unused GET.
                with self._lock:
                    waiting = self._callbacks.get(absolute, ())
                    if not any(callback.active for callback in waiting):
                        self._callbacks.pop(absolute, None)
                        self._queued.discard(absolute)
                        self._stats["cancelled_callbacks"] += len(waiting)
                        continue
                path = self._path(absolute)
                result: Path | None = None
                error: Exception | None = None
                started = time.perf_counter()
                try:
                    # Stream through the shared Core loader: a remote cover
                    # may advertise a false Content-Length or expand when
                    # decompressed. Never buffer/write more than MAX_IMAGE.
                    with self.client.stream("GET", absolute) as response:
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").lower()
                        if ("text/html" in content_type
                                or "application/json" in content_type):
                            raise ValueError("unexpected image content type")
                        announced = response.headers.get("content-length", "")
                        if (announced.isdecimal() and len(announced) <= 15
                                and int(announced) > MAX_IMAGE):
                            raise ValueError("image exceeds size budget")
                        data = bytearray()
                        for chunk in response.iter_bytes(chunk_size=65536):
                            if len(data) + len(chunk) > MAX_IMAGE:
                                raise ValueError("image exceeds size budget")
                            data.extend(chunk)
                    if not data:
                        raise ValueError("empty image body")
                    if not self._supported_image_bytes(data[:1024]):
                        raise ValueError("unsupported image bytes")
                    self._publish_image(path, data)
                    result = path
                    # The URL, even its host/path, and MIME headers can be
                    # private or provider-controlled. Emit only a safe count.
                    _TRACE_LOG.debug("TRACE cover download ok bytes=%s", len(data))
                    with self._lock:
                        elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000)
                        self._stats["download_ok"] += 1
                        self._stats["n3_url_published_bytes"] += len(data)
                        self._stats["n3_url_ok_total_ms"] += elapsed_ms
                        if math.isfinite(elapsed_ms):
                            self._n3_url_recent["ok"].append(elapsed_ms)
                        self._stats["n3_url_ok_max_ms"] = max(
                            self._stats["n3_url_ok_max_ms"], elapsed_ms,
                        )
                        self._fail_until.pop(absolute, None)
                        self._http_missing_until.pop(absolute, None)
                except Exception as exc:  # le fallback visuel gère l'échec
                    error = exc
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    _TRACE_LOG.debug("TRACE cover download failed reason=%s", _cover_failure_code(exc))
                    with self._lock:
                        elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000)
                        self._stats["download_failed"] += 1
                        if isinstance(exc, httpx.HTTPStatusError):
                            bucket = _n3_http_failure_bucket(exc)
                            self._stats[f"n3_url_http_failure_{bucket}"] += 1
                        self._stats["n3_url_failed_total_ms"] += elapsed_ms
                        if math.isfinite(elapsed_ms):
                            self._n3_url_recent["failed"].append(elapsed_ms)
                        self._stats["n3_url_failed_max_ms"] = max(
                            self._stats["n3_url_failed_max_ms"], elapsed_ms,
                        )
                        expires = time.monotonic() + 90.0
                        self._fail_until[absolute] = expires
                        self._http_missing_until.pop(absolute, None)
                        # Do not qualify redirects, auth or transient errors,
                        # broken preview, bad bytes or filesystem failures.
                        if (isinstance(exc, httpx.HTTPStatusError)
                                and exc.response.status_code in (404, 410)
                                and not exc.response.history
                                and exc.request.method == "GET"
                                and exc.request.url == httpx.URL(absolute)
                                and absolute.startswith("https://")):
                            self._http_missing_until[absolute] = expires
                            while len(self._http_missing_until) > 1024:
                                self._http_missing_until.popitem(last=False)

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
