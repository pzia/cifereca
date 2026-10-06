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
                        reusable.remove(plugin_id)
                        changed = True
                        break

        descriptors = {plugin_id: record.descriptor for plugin_id, record in self._records.items()}
        issues = validate_dependencies(descriptors)
        issues_by_plugin: dict[str, list[DependencyIssue]] = {}
        for issue in issues:
            issues_by_plugin.setdefault(issue.plugin_id, []).append(issue)

        enabled_ids = {plugin_id for plugin_id, record in self._records.items() if record.enabled}
        try:
            topological_order(descriptors, enabled_ids)
        except DependencyError as exc:
            cycle_text = str(exc)
            cyclic = {part.strip() for part in cycle_text.split(":", 1)[-1].split(",")}
            for plugin_id in cyclic:
                if plugin_id in self._records:
                    record = self._records[plugin_id]
                    record.state = "blocked"
                    record.reason = "dependency_cycle"
                    record.error = cycle_text

        for plugin_id, record in self._records.items():
            if not record.enabled:
                continue
            plugin_issues = issues_by_plugin.get(plugin_id, [])
            blocking = [issue for issue in plugin_issues if issue.code in {"dependency_missing", "dependency_version", "conflict"}]
            if blocking:
                record.state = "blocked"
                record.reason = blocking[0].code
                record.error = "; ".join(issue.message for issue in blocking)
                continue
            for conflict in record.descriptor.conflicts:
                target = self._records.get(conflict.plugin)
                if target is None or not target.enabled:
                    continue
                from packaging.specifiers import SpecifierSet
                from packaging.version import Version
                if not conflict.version or Version(target.descriptor.version) in SpecifierSet(conflict.version):
                    record.state = "blocked"
                    record.reason = "conflict"
                    record.error = f"conflicts with enabled plugin: {conflict.plugin}"
                    break
            if record.state == "blocked":
                continue
            # An enabled dependency can itself be disabled/blocked by policy; propagate later too.
            for dep in record.descriptor.requires:
                target = self._records.get(dep.plugin)
                if target is not None and not target.enabled:
                    record.state = "blocked"
                    record.reason = "dependency_disabled"
                    record.error = f"required plugin is disabled: {dep.plugin}"
                    break
            if record.state == "blocked":
                continue
            broker_ids = tuple(dict.fromkeys((*record.descriptor.requires_brokers, *record.descriptor.requires_services)))
            missing_brokers = [name for name in broker_ids if name not in self._brokers]
            if missing_brokers:
                record.state = "blocked"
                record.reason = "missing_broker"
                record.reason_detail = {"brokers": tuple(missing_brokers)}
                record.error = "required broker(s) unavailable: " + ", ".join(missing_brokers)
                continue
            degraded: list[dict[str, object]] = []
            contract_blocked = False
            for requirement in record.descriptor.requires_contracts:
                matches, detail = self._static_contract_matches(requirement)
                if matches:
                    continue
                if requirement.mode == "required":
                    record.state = "blocked"
                    record.reason = str(detail.get("reason_code") or "missing_service")
                    record.reason_detail = detail
                    record.error = str(detail.get("message") or "required contract unavailable")
                    contract_blocked = True
                    break
                if requirement.mode == "degraded":
                    degraded.append(detail)
            if contract_blocked:
                continue
            record.degraded_details = degraded
            if degraded:
                record.state = "degraded"
                record.reason = "missing_optional_capability"
                record.reason_detail = {"contracts": tuple(degraded)}
            old = old_records.get(plugin_id)
            if plugin_id in reusable and old is not None and old.requirements:
                # System requirement probes may spawn binaries (and can be
                # hundreds of milliseconds on desktop Python launchers). Keep
                # the already-validated result while the live plugin/package is
                # unchanged; blocked/not-yet-active plugins are still reprobed
                # on every refresh so installing a missing prerequisite is
                # detected without restarting cifereca.
                requirements = old.requirements
            else:
                requirements = check_system_requirements(record.descriptor.system_requires)
            record.requirements = requirements
            failed = [item for item in requirements if not item.ok]
            if failed:
                record.state = "blocked"
                record.reason = "system_requirement"
                record.error = "; ".join(item.message for item in failed)
                continue
            try:
                scope = schema_scope(record.descriptor.config_schema)
                if scope == "plugin":
                    self.config.get(record.descriptor.id, record.descriptor.config_schema)
                else:
                    # Scoped configuration is validated when an instance/Identity/target
                    # is configured; its absence must not block plugin activation.
                    defaults_for_schema(record.descriptor.config_schema)
            except PluginConfigError as exc:
                record.state = "blocked"
                record.reason = "configuration"
                record.error = str(exc)

        # Carry reusable services only after all policy/requirement/config checks
        # succeeded.  Everything else is deactivated narrowly rather than using
        # a global teardown.
        for plugin_id, old in old_records.items():
            new = self._records.get(plugin_id)
            if plugin_id in reusable and new is not None and new.enabled and new.state in {"ready", "degraded"}:
                new.instance = old.instance
                new.service = old.service
                continue
            if old.instance is not None:
                self._deactivate_record(old)
        return self.statuses()

    def _static_contract_matches(self, requirement: ContractRequirement) -> tuple[list[str], dict[str, object]]:
        category = self.categories.maybe_get(requirement.category)
        if category is None:
            return [], {
                "reason_code": "missing_service", "category": requirement.category,
                "contract": requirement.contract, "version": requirement.version,
                "message": f"required category is unavailable: {requirement.category}",
            }
        if category.contract_id != requirement.contract or not contract_version_matches(category.contract_version, requirement.version):
            return [], {
                "reason_code": "contr