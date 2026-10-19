from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
import os
import shutil
import threading
from typing import Any, Callable

from .paths import cache_dir
from .plugin_api import CacheNamespaceInfo, CachePurgeResult


def _tree_stats(root: Path) -> tuple[int, int]:
    """Count regular files without following symlinks."""
    if not root.exists() or not root.is_dir() or root.is_symlink():
        return 0, 0
    entries = size = 0
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(current)
        dirs[:] = [name for name in dirs if not (base / name).is_symlink()]
        for name in files:
            path = base / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                entries += 1
                size += int(path.stat().st_size)
            except OSError:
                continue
    return entries, size


def _purge_tree(root: Path) -> tuple[int, int, tuple[str, ...]]:
    entries, size = _tree_stats(root)
    warnings: list[str] = []
    if not root.exists():
        return 0, 0, ()
    try:
        # Delete children only; the stable cache root remains owned by Core.
        for child in tuple(root.iterdir()):
            try:
                if child.is_symlink() or child.is_file():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    shutil.rmtree(child)
            except OSError:
                # Never publish private filenames or raw filesystem errors.
                warnings.append("cache_entry_delete_failed")
    except OSError:
        warnings.append("cache_enumeration_failed")
    after_entries, after_size = _tree_stats(root)
    return max(0, entries - after_entries), max(0, size - after_size), tuple(warnings)


_CACHE_MISS = object()


class _SingleFlight:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._futures: dict[tuple[str, str], Future[Any]] = {}

    def run(self, namespace: str, key: str, producer: Callable[[], Any]) -> Any:
        token = (namespace, key)
        with self._lock:
            future = self._futures.get(token)
            if future is None:
                future = Future()
                self._futures[token] = future
                leader = True
            else:
                leader = False

        if not leader:
            return future.result()

        try:
            result = producer()
        except BaseException as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(result)
            return result
        finally:
            with self._lock:
                if self._futures.get(token) is future:
                    self._futures.pop(token, None)


@dataclass(slots=True)
class ScopedCacheBroker:
    service: Any
    plugin_id: str
    allow_global: bool = False
    _singleflight: _SingleFlight = field(default_factory=_SingleFlight, repr=False)

    @property
    def _plugin_root(self) -> Path:
        return cache_dir() / "plugins" / self.plugin_id

    @property
    def _db_namespace(self) -> str:
        return "plugin:" + self.plugin_id

    @staticmethod
    def _key(value: str) -> str:
        key = str(value)
        if not key:
            raise ValueError("cache key must not be empty")
        return key

    def get(self, key: str, default: Any = None) -> Any:
        return self.service.storage.cache_get(self._db_namespace, self._key(key), default)

    def put(self, key: str, value: Any, *, ttl_seconds: int = 300) -> None:
        ttl = int(ttl_seconds)
        if ttl < 1:
            raise ValueError("ttl_seconds must be >= 1")
        self.service.storage.cache_put(self._db_namespace, self._key(key), value, ttl)

    def delete(self, key: str) -> bool:
        return bool(self.service.storage.cache_delete(self._db_namespace, self._key(key)))

    def get_or_compute(self, key: str, producer: Callable[[], Any], *, ttl_seconds: int = 300) -> Any:
        if not callable(producer):
            raise TypeError("producer must be callable")
        ttl = int(ttl_seconds)
        if ttl < 1:
            raise ValueError("ttl_seconds must be >= 1")
        cache_key = self._key(key)
        cached = self.service.storage.cache_get(self._db_namespace, cache_key, _CACHE_MISS)
        if cached is not _CACHE_MISS:
            return cached

        def load() -> Any:
            cached_again = self.service.storage.cache_get(self._db_namespace, cache_key, _CACHE_MISS)
            if cached_again is not _CACHE_MISS:
                return cached_again
            value = producer()
            self.service.storage.cache_put(self._db_namespace, cache_key, value, ttl)
            return value

        return self._singleflight.run(self._db_namespace, cache_key, load)

    def _core_db_namespaces(self) -> list[CacheNamespaceInfo]:
        rows: list[CacheNamespaceInfo] = []
        for row in self.service.storage.cache_namespace_stats():
            raw = str(row["namespace"])
            plugin_owned = raw.startswith("plugin:") and bool(raw.split(":", 1)[1])
            owner = raw.split(":", 1)[1] if plugin_owned else "core"
            rows.append(CacheNamespaceInfo(
                namespace_id="db:" + raw,
                owner=owner,
                scope="plugin" if plugin_owned else "core",
                entries=int(row["entries"]),
                size_bytes=int(row["size_bytes"]),
                expired_entries=int(row["expired_entries"]),
                purgeable=True,
                description="Cache clé/valeur TTL du plugin" if plugin_owned else "Cache SQLite géré par le Core",
            ))
        return rows

    def _plugin_db_namespace(self, plugin_id: str) -> CacheNamespaceInfo:
        raw = "plugin:" + plugin_id
        row = next(
            (item for item in self.service.storage.cache_namespace_stats() if str(item["namespace"]) == raw),
            None,
        )
        return CacheNamespaceInfo(
            namespace_id="db:" + raw,
            owner=plugin_id,
            scope="plugin",
            entries=int((row or {}).get("entries", 0)),
            size_bytes=int((row or {}).get("size_bytes", 0)),
            expired_entries=int((row or {}).get("expired_entries", 0)),
            purgeable=True,
            description="Cache clé/valeur TTL du plugin",
        )

    def _plugin_namespace(self, plugin_id: str) -> CacheNamespaceInfo:
        root = cache_dir() / "plugins" / plugin_id
        entries, size = _tree_stats(root)
        return CacheNamespaceInfo(
            namespace_id="plugin:" + plugin_id, owner=plugin_id, scope="plugin",
            entries=entries, size_bytes=size, expired_entries=0, purgeable=True,
            description="Répertoire de cache privé du plugin",
        )

    def namespaces(self) -> tuple[CacheNamespaceInfo, ...]:
        if not self.allow_global:
            rows = [self._plugin_db_namespace(self.plugin_id), self._plugin_namespace(self.plugin_id)]
            rows.sort(key=lambda item: item.namespace_id)
            return tuple(rows)
        rows = self._core_db_namespaces()
        cover_root = cache_dir() / "epub-covers-v1"
        count, size = _tree_stats(cover_root)
        rows.append(CacheNamespaceInfo(
            "core:epub-covers", "core", "core", count, size, 0, True,
            "Prévisualisations de couvertures EPUB",
        ))
        plugins_root = cache_dir() / "plugins"
        if plugins_root.is_dir():
            for child in sorted(plugins_root.iterdir(), key=lambda p: p.name):
                if child.is_dir() and not child.is_symlink():
                    rows.append(self._plugin_namespace(child.name))
        rows.sort(key=lambda item: (item.scope, item.owner, item.namespace_id))
        return tuple(rows)

    def _allowed(self, namespace_id: str) -> bool:
        if self.allow_global:
            return True
        return namespace_id in {
            "plugin:" + self.plugin_id,
            "db:plugin:" + self.plugin_id,
        }

    def purge(self, namespace_id: str = "", *, expired_only: bool = False) -> CachePurgeResult:
        requested = [namespace_id] if namespace_id else [row.namespace_id for row in self.namespaces()]
        removed = freed = 0
        done: list[str] = []
        warnings: list[str] = []
        for ns in requested:
            if not self._allowed(ns):
                warnings.append("cache_namespace_forbidden")
                continue
            if ns.startswith("db:"):
                count, size = self.service.storage.cache_delete_namespace_counted(ns[3:], expired_only=expired_only)
                removed += count; freed += size; done.append(ns)
                continue
            if ns == "core:epub-covers":
                if not self.allow_global:
                    warnings.append("cache_namespace_forbidden")
                    continue
                if expired_only:
                    warnings.append("cache_expiration_unsupported")
                    continue
                count, size, extra = _purge_tree(cache_dir() / "epub-covers-v1")
                removed += count; freed += size; warnings.extend(extra); done.append(ns)
                continue
            if ns.startswith("plugin:"):
                owner = ns.split(":", 1)[1]
                if expired_only:
                    warnings.append("cache_expiration_unsupported")
                    continue
                count, size, extra = _purge_tree(cache_dir() / "plugins" / owner)
                removed += count; freed += size; warnings.extend(extra); done.append(ns)
                continue
            warnings.append("cache_namespace_unknown")
        return CachePurgeResult(not warnings, removed, freed, tuple(done), tuple(warnings))


@dataclass(slots=True)
class CacheBrokerImpl:
    service: Any
    _singleflight: _SingleFlight = field(default_factory=_SingleFlight, init=False, repr=False)

    def _scope(self, plugin_id: str = "core", *, trusted: bool = True) -> ScopedCacheBroker:
        return ScopedCacheBroker(
            self.service,
            str(plugin_id),
            allow_global=bool(trusted),
            _singleflight=self._singleflight,
        )

    def for_plugin(self, plugin_id: str, *, trusted: bool = False) -> ScopedCacheBroker:
        return self._scope(plugin_id, trusted=trusted)

    def get(self, key: str, default: Any = None) -> Any:
        return self._scope().get(key, default)

    def put(self, key: str, value: Any, *, ttl_seconds: int = 300) -> None:
        self._scope().put(key, value, ttl_seconds=ttl_seconds)

    def delete(self, key: str) -> bool:
        return self._scope().delete(key)

    def get_or_compute(self, key: str, producer: Callable[[], Any], *, ttl_seconds: int = 300) -> Any:
        return self._scope().get_or_compute(key, producer, ttl_seconds=ttl_seconds)

    def namespaces(self) -> tuple[CacheNamespaceInfo, ...]:
        return self._scope().namespaces()

    def purge(self, namespace_id: str = "", *, expired_only: bool = False) -> CachePurgeResult:
        return self._scope().purge(namespace_id, expired_only=expired_only)
