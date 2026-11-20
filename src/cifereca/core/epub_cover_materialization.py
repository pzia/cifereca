"""N3 EPUB cover rehydration, separate from documentary N1/N2 (#6168).

Caller provides an authorized Core Artifact+Placement and its already chosen
cover member. This module never joins, selects or grants a book/loan; it only
restores missing physical image bytes, coalescing list and detail requests.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from collections.abc import Callable
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import time
from time import perf_counter
from threading import Lock
import zipfile

from .book_cover_projection import CoverDocumentScope
from ..epub_metadata import (
    MAX_ENTRIES, MAX_IMAGE, _bounded_directory, _image_kind, _member,
    _publish_cover, _read, cover_cache_dir, valid_cached_cover_path,
)

_CACHE_NAME = re.compile(r"[0-9a-f]{64}\.(png|jpg|gif|webp|svg)\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_EPUB_BYTES = 2 * 1024 * 1024 * 1024
_N3_EPUB_WINDOW = 1024
# N3 source identity: NEVER key physical work by the N2 book/borrower scope.
_PhysicalKey = tuple[str, str, str, Path]


def _physical_key(source: ExactEpubCoverResource, path: Path) -> _PhysicalKey:
    return (source.artifact_sha256, source.member, source.cache_name, path)


@dataclass(frozen=True, slots=True)
class ExactEpubCoverResource:
    """Physical source selected through a pre-existing attested Placement.

    The EPUB member is recorded when initially inspecting this very Artifact.
    Its image digest is distinct from the SHA-256 of the whole EPUB.
    No private path, credential or title is stored in this reference.
    """

    scope: CoverDocumentScope
    source_revision: int
    artifact_sha256: str
    member: str
    cache_name: str

    def __post_init__(self) -> None:
        if (type(self.scope) is not CoverDocumentScope
                or self.scope.kind != "placement"
                or not self.scope.artifact_id or not self.scope.placement_id):
            raise ValueError("expected attested Artifact and Placement scope")
        if type(self.source_revision) is not int or self.source_revision < 0:
            raise ValueError("invalid EPUB cover revision")
        if (type(self.artifact_sha256) is not str
                or _SHA.fullmatch(self.artifact_sha256) is None):
            raise ValueError("invalid EPUB artifact digest")
        if (self.scope.artifact_id.startswith("sha256:")
                and self.scope.artifact_id[7:] != self.artifact_sha256):
            raise ValueError("EPUB cover does not match scoped Artifact")
        if (type(self.member) is not str
                or not self.member or len(self.member) > 512
                or not self.member.isprintable()):
            raise ValueError("invalid EPUB cover member")
        try:
            if _member("", self.member) != self.member:
                raise ValueError("unnormalized EPUB cover member")
        except ValueError:
            raise ValueError("invalid EPUB cover member") from None
        if (type(self.cache_name) is not str
                or _CACHE_NAME.fullmatch(self.cache_name) is None):
            raise ValueError("invalid published EPUB cover name")


def _finished(value: str | None) -> Future[str | None]:
    result: Future[str | None] = Future()
    result.set_result(value)
    return result


class EpubCoverPhysicalCache:
    """Bounded N3 work keyed by physical source, never by document identity.

    source_path MUST come from Core's exact identity and verified Placement.
    Consumers re-check SourceKey/revision/epoch before painting a result.
    Cold failures get a short negative TTL; an independently restored image
    bypasses this cooldown. No network/GTK/catalogue/rights mutation here.
    """

    def __init__(
        self, *, max_workers: int = 2, max_inflight: int = 128,
        negative_ttl: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if (type(max_workers) is not int or not 1 <= max_workers <= 8
                or type(max_inflight) is not int or not 1 <= max_inflight <= 4096):
            raise ValueError("invalid EPUB cover worker limits")
        if (type(negative_ttl) not in (int, float)
                or not 0 < negative_ttl <= 300
                or not float("-inf") < negative_ttl < float("inf")
                or not callable(clock)):
            raise ValueError("invalid N3 failed-extraction cooldown")
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="cifereca-epub-cover",
        )
        self._limit = max_inflight
        self._lock = Lock()
        self._closed = False
        self._negative_ttl = float(negative_ttl)
        self._clock = clock
        # A failure is a TEMPORARY physical-cache miss, never N2 'missing'.
        # Bound negative memory independently from the in-flight worker cap.
        self._negative_until: OrderedDict[_PhysicalKey, float] = OrderedDict()
        self._max_negative = min(4096, max_inflight * 4)
        self._pending: dict[_PhysicalKey, Future[str | None]] = {}
        # Physical N3 counters: no DocumentRef/profile/credential in export.
        self._worker_recent = {outcome: deque(maxlen=_N3_EPUB_WINDOW)
                               for outcome in ("ok", "failed")}
        self._counts = {
            "n3_epub_hit": 0, "n3_epub_miss": 0,
            "n3_epub_singleflight": 0, "n3_epub_cooldown": 0,
            "n3_epub_overflow": 0, "n3_epub_failed": 0,
        }

    def request(
        self, source: ExactEpubCoverResource, *, source_path: Path,
    ) -> Future[str | None]:
        """A cold source returns the SAME Future to simultaneous consumers."""
        if type(source) is not ExactEpubCoverResource:
            raise TypeError("expected exact EPUB cover source")
        if (not isinstance(source_path, Path) or not source_path.is_absolute()
                or source_path.suffix.lower() not in (".epub", ".kepub")):
            raise ValueError("expected a verified EPUB Placement path")
        with self._lock:
            if self._closed:
                raise RuntimeError("EPUB cover cache closed")
            target = cover_cache_dir() / source.cache_name
            if valid_cached_cover_path(target):
                # Warm N3: no source file open, ZIP parse, hash or GET.
                self._counts["n3_epub_hit"] += 1
                return _finished(target.resolve().as_uri())
            key = _physical_key(source, source_path)
            future = self._pending.get(key)
            if future is not None:
                # Same bytes from two N2 document refs share ONE N3 read.
                self._counts["n3_epub_singleflight"] += 1
                return future
            cooldown = self._negative_until.get(key)
            if cooldown is not None:
                if self._clock() < cooldown:
                    self._negative_until.move_to_end(key)
                    self._counts["n3_epub_cooldown"] += 1
                    return _finished(None)
                self._negative_until.pop(key, None)
            if len(self._pending) >= self._limit:
                self._counts["n3_epub_overflow"] += 1
                return _finished(None)
            future = self._pool.submit(self._restore_timed, source, source_path)
            self._counts["n3_epub_miss"] += 1
            self._pending[key] = future
        future.add_done_callback(lambda done: self._release(key, done))
        return future

    def _release(
        self, key: _PhysicalKey,
        finished: Future[str | None],
    ) -> None:
        try:
            result = finished.result()
        except Exception:
            result = None
        with self._lock:
            if self._pending.get(key) is not finished:
                return
            self._pending.pop(key, None)
            if result is not None or self._closed:
                self._negative_until.pop(key, None)
            else:
                self._counts["n3_epub_failed"] += 1
                self._negative_until[key] = self._clock() + self._negative_ttl
                self._negative_until.move_to_end(key)
                while len(self._negative_until) > self._max_negative:
                    self._negative_until.popitem(last=False)

    def _restore_timed(self, source, source_path):
        # Measure actual worker execution, not queue delay, subscribers,
        # cooldown responses, or cancellations before a worker starts.
        started = perf_counter()
        outcome = "failed"
        try:
            result = self._restore(source, source_path)
            if result is not None:
                outcome = "ok"
            return result
        finally:
            self._record_worker_latency(outcome, (perf_counter() - started) * 1000)

    def _record_worker_latency(self, outcome, elapsed_ms):
        if not math.isfinite(elapsed_ms) or elapsed_ms < 0:
            return
        with self._lock:
            self._worker_recent[outcome].append(elapsed_ms)

    @staticmethod
    def _restore(
        source: ExactEpubCoverResource, source_path: Path,
    ) -> str | None:
        """Recover the exact published member, without re-resolving the book."""
        try:
            fd = os.open(
                source_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode)
                        or not 0 < info.st_size <= _MAX_EPUB_BYTES):
                    return None
                def stamp(value):
                    return (value.st_dev, value.st_ino, value.st_size,
                            value.st_mtime_ns, value.st_ctime_ns)

                def unchanged():
                    # A stable open inode alone does not attest that the
                    # authorized Placement still names this exact file.
                    return (stamp(os.fstat(stream.fileno())) == stamp(info)
                            and stamp(os.stat(source_path, follow_symlinks=False)) == stamp(info))

                # Never hash a growing file beyond its admitted size. Read
                # at most one extra byte to distinguish growth from EOF.
                digest = hashlib.sha256()
                remaining = info.st_size
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        return None
                    remaining -= len(chunk)
                    digest.update(chunk)
                if (stream.read(1) or not unchanged()
                        or digest.hexdigest() != source.artifact_sha256):
                    return None
                stream.seek(0)
                _bounded_directory(stream)
                with zipfile.ZipFile(stream) as archive:
                    if len(archive.infolist()) > MAX_ENTRIES:
                        return None
                    image = _read(archive, source.member, MAX_IMAGE)
                if not unchanged():
                    return None
            kind = _image_kind(image)
            if (kind is None or source.cache_name != (
                    hashlib.sha256(image).hexdigest() + "." + kind)):
                return None
            if _publish_cover(image, kind) != source.cache_name:
                return None
            target = cover_cache_dir() / source.cache_name
            return (target.resolve().as_uri()
                    if valid_cached_cover_path(target) else None)
        except Exception:
            # Never expose untrusted path/member or ZIP error text to callers.
            return None

    def stats(self) -> dict[str, int | float]:
        """Physical N3 counters and bounded worker-duration percentiles."""
        with self._lock:
            stats = {"inflight": len(self._pending), **self._counts}
            for outcome, window in self._worker_recent.items():
                prefix = f"n3_epub_{outcome}"
                recent = sorted(window)
                stats[f"{prefix}_recent_count"] = count = len(recent)
                if count:
                    stats[f"{prefix}_recent_p50_ms"] = round(recent[(count * 50 + 99) // 100 - 1], 2)
                    stats[f"{prefix}_recent_p95_ms"] = round(recent[(count * 95 + 99) // 100 - 1], 2)
            return stats

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._negative_until.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)


__all__ = ["ExactEpubCoverResource", "EpubCoverPhysicalCache"]
