from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
from typing import Any

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
            except OSError as exc:
                warnings.append(f"{child.name}: {exc}")
    except OSError as exc:
        warnings.append(str(exc))
    after_entries, after_size = _tree_stats(root)
    return max(0, entries - after_entries), max(0, size - after_size), tuple(warnings)


@dataclass(slots=True)
class ScopedCacheBroker:
    service: Any
    plugin_id: str
    allow_global: bool = False

    @property
    def _plugin_root(self) -> Path:
        return cache_dir() / "plugins" / self.plugin_id

    def _core_db_namespaces(self) -> list[CacheNamespaceInfo]:
        return [
            CacheNamespaceInfo(
                namespace_id="db:" + str(row["namespace"]), owner="core", scope="core",
                entries=int(row["entries"]), size_bytes=int(row["size_bytes"]),
                expired_entries=int(row["expired_entries"]), purgeable=True,
                description="Cache SQLite géré par le Core",
            )
            for row in self.service.storage.cache_namespace_stats()
        ]

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
            return (self._plugin_namespace(self.plugin_id),)
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
        return namespace_id == "plugin:" + self.plugin_id

    def purge(self, namespace_id: str = "", *, expired_only: bool = False) -> CachePurgeResult:
        requested = [namespace_id] if namespace_id else [row.namespace_id for row in self.namespaces()]
        removed = freed = 0
        done: list[str] = []
        warnings: list[str] = []
        for ns in requested:
            if not self._allowed(ns):
                warnings.append(f"namespace interdit: {ns}")
                continue
            if ns.startswith("db:"):
                if not self.allow_global:
                    warnings.append(f"namespace interdit: {ns}")
                    continue
                count, size = self.service.storage.cache_delete_namespace_counted(ns[3:], expired_only=expired_only)
                removed += count; freed += size; done.append(ns)
                continue
            if ns == "core:epub-covers":
                if not self.allow_global:
                    warnings.append(f"namespace interdit: {ns}")
                    continue
                if expired_only:
                    warnings.append("core:epub-covers: pas de notion d'expiration")
                    continue
                count, size, extra = _purge_tree(cache_dir() / "epub-covers-v1")
                removed += count; freed += size; warnings.extend(extra); done.append(ns)
                continue
            if ns.startswith("plugin:"):
                owner = ns.split(":", 1)[1]
                if expired_only:
                    warnings.append(f"{ns}: pas de notion d'expiration")
                    continue
                count, size, extra = _purge_tree(cache_dir() / "plugins" / owner)
                removed += count; freed += size; warnings.extend(extra); done.append(ns)
                continue
            warnings.append(f"namespace inconnu: {ns}")
        return CachePurgeResult(not warnings, removed, freed, tuple(done), tuple(warnings))


@dataclass(slots=True)
class CacheBrokerImpl:
    service: Any

    def for_plugin(self, plugin_id: str, *, trusted: bool = False) -> ScopedCacheBroker:
        return ScopedCacheBroker(self.service, str(plugin_id), allow_global=bool(trusted))

    def namespaces(self) -> tuple[CacheNamespaceInfo, ...]:
        return ScopedCacheBroker(self.service, "core", allow_global=True).namespaces()

    def purge(self, namespace_id: str = "", *, expired_only: bool = False) -> CachePurgeResult:
        return ScopedCacheBroker(self.service, "core", allow_global=True).purge(namespace_id, expired_only=expired_only)
