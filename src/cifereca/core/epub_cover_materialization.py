"""N3 EPUB cover rehydration, separate from documentary N1/N2 (#6168).

Caller provides an authorized Core Artifact+Placement and its already chosen
cover member. This module never joins, selects or grants a book/loan; it only
restores missing physical image bytes, coalescing list and detail requests.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
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
    """Bounded worker-side N3 image recovery; never an N2 association cache.

    source_path MUST come from Core's exact identity and verified Placement.
    Consumers re-check SourceKey/revision/epoch before painting a result.
    No network, Gtk, catalogue or rights mutation is performed here.
    """

    def __init__(self, *, max_workers: int = 2, max_inflight: int = 128):
        if (type(max_workers) is not int or not 1 <= max_workers <= 8
                or type(max_inflight) is not int or not 1 <= max_inflight <= 4096):
            raise ValueError("invalid EPUB cover worker limits")
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="cifereca-epub-cover",
        )
        self._limit = max_inflight
        self._lock = Lock()
        self._closed = False
        self._pending: dict[
            tuple[ExactEpubCoverResource, Path], Future[str | None]
        ] = {}

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
                return _finished(target.resolve().as_uri())
            key = (source, source_path)
            future = self._pending.get(key)
            if future is not None:
                return future
            if len(self._pending) >= self._limit:
                return _finished(None)
            future = self._pool.submit(self._restore, source, source_path)
            self._pending[key] = future
        future.add_done_callback(lambda done: self._release(key, done))
        return future

    def _release(
        self, key: tuple[ExactEpubCoverResource, Path],
        finished: Future[str | None],
    ) -> None:
        with self._lock:
            if self._pending.get(key) is finished:
                self._pending.pop(key, None)

    @staticmethod
    def _restore(
        source: ExactEpubCoverResource, source_path: Path,
    ) -> str | None:
        """Recover the exact published member, without re-resolving the book."""
        try:
            fd = os.open(
                source_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode)
                        or not 0 < info.st_size <= _MAX_EPUB_BYTES):
                    return None
                digest = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                if digest.hexdigest() != source.artifact_sha256:
                    return None
                stream.seek(0)
                _bounded_directory(stream)
                with zipfile.ZipFile(stream) as archive:
                    if len(archive.infolist()) > MAX_ENTRIES:
                        return None
                    image = _read(archive, source.member, MAX_IMAGE)
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

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"inflight": len(self._pending)}

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)


__all__ = ["ExactEpubCoverResource", "EpubCoverPhysicalCache"]
