from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from cifereca.paths import cache_dir, data_dir
from cifereca.plugin_api import (
    PluginContext, PluginDirectories, PluginDescriptor, PluginStatus, ResolvedPluginService,
    ContractRequirement, check_service_contract, contract_version_matches,
)

from .categories import CategoryRegistry
from .configuration import PluginConfigError, PluginConfigStore, defaults_for_schema, schema_scope
from .dependency import DependencyIssue, topological_order, validate_dependencies
from .errors import DependencyError, ManifestError
from .loader import load_entrypoint
from .packages import InstalledPackage, PackageInstaller, PackageStore
from .requirements import check_system_requirements
from .python_dependencies import PythonDependencyManager, PythonDependencyTransaction
from .state import PluginStateStore

_LOG = logging.getLogger("cifereca.plugins")


@dataclass(slots=True)
class _RuntimeRecord:
    descriptor: PluginDescriptor
    package: InstalledPackage
    enabled: bool
    effective_priority: int
    state: str = "disabled"
    reason: str = ""
    error: str = ""
    requirements: tuple[Any, ...] = ()
    reason_detail: dict[str, object] | None = None
    degraded_details: list[dict[str, object]] | None = None
    instance: object | None = None
    service: object | None = None


class PluginManager:
    """Manifest-first plugin runtime.

    `refresh()` performs discovery/validation only. `activate()` imports plugin code
    after categories, dependencies, API versions and system requirements have been
    evaluated. A broken third-party plugin therefore cannot prevent cifereca from
    discovering the rest of the installation.
    """

    def __init__(
        self,
        *,
        package_store: PackageStore | None = None,
        state_store: PluginStateStore | None = None,
        diagnostics: Any | None = None,
        config_store: PluginConfigStore | None = None,
        builtin_ui: bool = False,
        disable_plugins: bool = False,
        force_plugins: Iterable[str] = (),
        brokers: dict[str, object] | None = None,
        python_dependencies: PythonDependencyManager | None = None,
    ) -> None:
        self._builtin_ui = bool(builtin_ui)
        self._disable_plugins = bool(disable_plugins)
        self._force_plugins = {str(value).strip() for value in force_plugins if str(value).strip()}
        self._brokers = dict(brokers or {})
        self._service_subscribers: dict[str, list[Callable[[], None]]] = {}
        self._registry_generation = 0
        self._published_category_signatures: dict[str, tuple[object, ...]] = {}
        self.packages = package_store or PackageStore()
        self.state = state_store or PluginStateStore()
        self.installer = PackageInstaller(self.packages)
        self.python_dependencies = python_dependencies or PythonDependencyManager()
        self.categories = CategoryRegistry()
        self.diagnostics = diagnostics
        self.config = config_store or PluginConfigStore()
        self._records: dict[str, _RuntimeRecord] = {}
        self._load_errors: list[str] = []
        self._import_roots: set[str] = set()

    def _diag(self, message: str, status: str, detail: dict[str, Any]) -> None:
        if self.diagnostics is not None:
            try:
                self.diagnostics.add("PLUGIN", message, status, detail=detail, level="detailed")
            except Exception:
                pass

    def refresh(self) -> list[PluginStatus]:
        # Preserve already-active instances when their package, descriptor and
        # dependency graph are unchanged.  The previous implementation tore down
        # every plugin on each install/refresh, which made progressive plugin
        # insertion reprobe expensive runtimes such as Kobo-Adept repeatedly.
        old_records = self._records
        self.categories = CategoryRegistry()
        self._records = {}
        self._load_errors = []
        packages: list[InstalledPackage] = []
        for package_id in sorted(self.packages.all_packages()):
            try:
                packages.append(self.packages.load(package_id))
            except Exception as exc:
                self._load_errors.append(f"{package_id}: {exc}")
                self._diag("package discovery", "error", {"package": package_id, "error": str(exc)})
        # The shipped UI is a normal contribution package. Recovery remains in
        # the shell if a user disables/replaces the management page.
        from .manifest import load_manifest
        builtin_root = Path(__file__).resolve().parents[1] / "builtin_ui"
        if self._builtin_ui:
            packages.insert(0, InstalledPackage(load_manifest(builtin_root / "cifereca-plugin.json"), builtin_root, "builtin"))
        # Categories must exist before plugin type validation.
        for package in packages:
            for category in package.manifest.categories:
                try:
                    self.categories.register(category)
                except Exception as exc:
                    self._load_errors.append(f"{package.manifest.package.id}: {exc}")
        for package in packages:
            for descriptor in package.manifest.plugins:
                if descriptor.id in self._records:
                    self._load_errors.append(f"duplicate plugin id across packages: {descriptor.id}")
                    continue
                if self.categories.maybe_get(descriptor.type) is None:
                    self._load_errors.append(f"unknown plugin category {descriptor.type} for {descriptor.id}")
                    continue
                override = self.state.enabled_override(descriptor.id)
                configured_enabled = descriptor.enabled_by_default if override is None else override
                enabled = descriptor.id in self._force_plugins or (not self._disable_plugins and configured_enabled)
                priority = self.state.priority_override(descriptor.id)
                effective = descriptor.priority if priority is None else priority
                self._records[descriptor.id] = _RuntimeRecord(
                    descriptor=descriptor, package=package, enabled=enabled, effective_priority=effective,
                    state="disabled" if not enabled else "ready",
                )

        # Work out which live instances can safely survive this refresh.  A
        # dependent is reusable only when all of its previously-known required
        # plugins are themselves unchanged; upgrades therefore reactivate the
        # dependent while unrelated installs do not.
        reusable: set[str] = set()
        for plugin_id, record in self._records.items():
            old = old_records.get(plugin_id)
            if (
                old is not None
                and old.instance is not None
                and old.enabled == record.enabled
                and old.descriptor == record.descriptor
                and old.package.root == record.package.root
            ):
                reusable.add(plugin_id)
        changed = True
        while changed:
            changed = False
            for plugin_id in list(reusable):
                record = self._records[plugin_id]
                for dep in record.descriptor.requires:
                    if dep.plugin in old_records and dep.plugin not in reusable: