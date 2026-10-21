from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from cifereca.paths import cache_dir, data_dir
from cifereca.plugin_release import expected_plugin_packages, package_freshness, installed_plugin_update_receipt
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

# Permanently retired providers. Do not reactivate stale user-installed
# packages even if an older release left them in the local PackageStore.
# Keep files/cache intact for user-controlled recovery; do not delete data.
_RETIRED_YOUTH_SOURCE_PACKAGES = frozenset({
    "org.cifereca.youth-source-usborne",
    "org.cifereca.youth-source-booktrust",
    "org.cifereca.youth-catalog",
    "org.cifereca.youth-bibliography-openlibrary",
    "org.cifereca.youth-detail-enrichment",
    "org.cifereca.youth-source-discovery",
    "org.cifereca.youth-source-ecole-loisirs",
    "org.cifereca.youth-source-milan",
})
from .requirements import check_system_requirements
from .python_dependencies import PythonDependencyManager, PythonDependencyTransaction
from .state import PluginStateStore

_LOG = logging.getLogger("cifereca.plugins")


def _load_builtin_package(root: Path) -> InstalledPackage:
    """Load builtin UI with the Core runtime version, never stale manifest metadata."""
    from cifereca import __version__
    from .manifest import load_manifest

    manifest = load_manifest(root / "cifereca-plugin.json")
    manifest = replace(
        manifest,
        package=replace(manifest.package, version=__version__),
        plugins=tuple(
            replace(plugin, version=__version__, package_version=__version__)
            for plugin in manifest.plugins
        ),
    )
    return InstalledPackage(manifest, root, "builtin")


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
        core_services: dict[str, object] | None = None,
        python_dependencies: PythonDependencyManager | None = None,
    ) -> None:
        self._builtin_ui = bool(builtin_ui)
        self._disable_plugins = bool(disable_plugins)
        self._force_plugins = {str(value).strip() for value in force_plugins if str(value).strip()}
        self._brokers = dict(brokers or {})
        # Reserved opt-in services are Core-owned, not plugin implementations.
        self._core_services = dict(core_services or {})
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
            if package_id in _RETIRED_YOUTH_SOURCE_PACKAGES:
                continue
            try:
                packages.append(self.packages.load(package_id))
            except Exception as exc:
                self._load_errors.append(f"{package_id}: {exc}")
                self._diag("package discovery", "error", {"package": package_id, "error": str(exc)})
        # The shipped UI is a normal contribution package. Recovery remains in
        # the shell if a user disables/replaces the management page.
        builtin_root = Path(__file__).resolve().parents[1] / "builtin_ui"
        if self._builtin_ui:
            packages.insert(0, _load_builtin_package(builtin_root))
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
                "reason_code": "contract_mismatch", "category": requirement.category,
                "expected_contract": requirement.contract, "expected_version": requirement.version,
                "actual_contract": category.contract_id, "actual_version": category.contract_version,
                "message": f"contract mismatch for category {requirement.category}",
            }
        required_caps = set(requirement.capabilities)
        matches = []
        for plugin_id, candidate in self._records.items():
            if not candidate.enabled or candidate.descriptor.type != requirement.category:
                continue
            if required_caps.issubset(set(candidate.descriptor.capabilities)):
                matches.append(plugin_id)
        if not matches:
            return [], {
                "reason_code": "missing_service", "category": requirement.category,
                "contract": requirement.contract, "version": requirement.version,
                "capabilities": tuple(requirement.capabilities),
                "message": f"no compatible provider for category {requirement.category}",
            }
        return sorted(matches), {}

    def _category_signatures(self) -> dict[str, tuple[object, ...]]:
        grouped: dict[str, list[object]] = {}
        for plugin_id, record in sorted(self._records.items()):
            if record.service is None or not record.enabled or record.state not in {"ready", "degraded"}:
                continue
            grouped.setdefault(record.descriptor.type, []).append((
                plugin_id, record.descriptor.version, tuple(record.descriptor.capabilities),
                record.state, id(record.service),
            ))
        return {key: tuple(value) for key, value in grouped.items()}

    def _publish_registry_change(self) -> None:
        signatures = self._category_signatures()
        keys = set(signatures) | set(self._published_category_signatures)
        changed = {key for key in keys if signatures.get(key, ()) != self._published_category_signatures.get(key, ())}
        if not changed:
            return
        self._published_category_signatures = signatures
        self._registry_generation += 1
        self._notify_service_subscribers(changed)

    @property
    def registry_generation(self) -> int:
        return self._registry_generation

    def _active_contract_match(self, requirement: ContractRequirement) -> bool:
        required_caps = set(requirement.capabilities)
        for row in self.services(requirement.category):
            if row.contract_id != requirement.contract:
                continue
            if not contract_version_matches(row.contract_version, requirement.version):
                continue
            if not required_caps.issubset(set(row.capabilities)):
                continue
            return True
        return False

    def activate(self) -> list[PluginStatus]:
        self.refresh()
        descriptors = {plugin_id: record.descriptor for plugin_id, record in self._records.items()}
        candidates = {
            plugin_id for plugin_id, record in self._records.items()
            if record.enabled and record.state in {"ready", "degraded"}
        }
        try:
            order = topological_order(descriptors, candidates)
        except DependencyError:
            order = sorted(candidates)

        pending = list(order)
        while pending:
            progressed = False
            next_pending: list[str] = []
            pending_set = set(pending)
            for plugin_id in pending:
                record = self._records[plugin_id]
                blocked_dependency = next(
                    (dep.plugin for dep in record.descriptor.requires
                     if dep.plugin in self._records and self._records[dep.plugin].state not in {"ready", "degraded"}),
                    None,
                )
                if blocked_dependency:
                    record.state = "blocked"
                    record.reason = "dependency_unavailable"
                    record.reason_detail = {"plugin": blocked_dependency}
                    record.error = f"required plugin is not ready: {blocked_dependency}"
                    progressed = True
                    continue

                wait_for_contract = False
                runtime_blocked = False
                for requirement in record.descriptor.requires_contracts:
                    if requirement.mode == "optional" or self._active_contract_match(requirement):
                        continue
                    static_matches, detail = self._static_contract_matches(requirement)
                    waiting = any(
                        provider_id != plugin_id and provider_id in pending_set
                        and self._records[provider_id].service is None
                        for provider_id in static_matches
                    )
                    if waiting:
                        wait_for_contract = True
                        break
                    if requirement.mode == "required":
                        record.state = "blocked"
                        record.reason = "missing_service"
                        record.reason_detail = detail or {
                            "category": requirement.category, "contract": requirement.contract,
                            "version": requirement.version, "capabilities": tuple(requirement.capabilities),
                        }
                        record.error = f"no ready service satisfies {requirement.contract} {requirement.version}"
                        runtime_blocked = True
                        break
                    if requirement.mode == "degraded":
                        details = record.degraded_details or []
                        runtime_detail = detail or {
                            "reason_code": "missing_service", "category": requirement.category,
                            "contract": requirement.contract, "version": requirement.version,
                            "capabilities": tuple(requirement.capabilities),
                        }
                        if runtime_detail not in details:
                            details.append(runtime_detail)
                        record.degraded_details = details
                if runtime_blocked:
                    progressed = True
                    continue
                if wait_for_contract:
                    next_pending.append(plugin_id)
                    continue

                if record.descriptor.type == "meta.bundle" and not record.descriptor.entrypoint:
                    record.state = "ready"
                    progressed = True
                    continue
                if record.instance is not None:
                    progressed = True
                    continue
                try:
                    self._activate_one(record)
                except Exception as exc:
                    record.state = "error"
                    record.reason = "activation_failed"
                    record.reason_detail = {"exception": type(exc).__name__}
                    record.error = str(exc)
                    self._diag("plugin activation", "error", {"plugin": plugin_id, "error": str(exc)})
                progressed = True

            if not next_pending:
                break
            if not progressed:
                for plugin_id in next_pending:
                    record = self._records[plugin_id]
                    record.state = "blocked"
                    record.reason = "missing_service"
                    record.reason_detail = {"contracts": tuple(
                        {"category": req.category, "contract": req.contract, "version": req.version}
                        for req in record.descriptor.requires_contracts if req.mode == "required"
                    )}
                    record.error = "required contract providers could not be activated"
                break
            pending = next_pending

        self._publish_registry_change()
        return self.statuses()

    def _activate_one(self, record: _RuntimeRecord) -> None:
        descriptor = record.descriptor
        if not descriptor.entrypoint:
            return
        root_text = str(record.package.root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
            self._import_roots.add(root_text)
        value = load_entrypoint(record.package.root, descriptor.entrypoint)
        instance = value
        if isinstance(value, type):
            instance = value()
        elif callable(value) and not hasattr(value, "activate"):
            instance = value()
        plugin_data = data_dir() / "plugins" / "data" / descriptor.id
        plugin_cache = cache_dir() / "plugins" / descriptor.id
        plugin_data.mkdir(parents=True, exist_ok=True)
        plugin_cache.mkdir(parents=True, exist_ok=True)
        scope = schema_scope(descriptor.config_schema)
        activation_config = (
            self.config.get(descriptor.id, descriptor.config_schema)
            if scope == "plugin"
            else defaults_for_schema(descriptor.config_schema)
        )
        context = PluginContext(
            plugin_id=descriptor.id,
            package_id=descriptor.package_id,
            directories=PluginDirectories(data=plugin_data, cache=plugin_cache),
            config=activation_config,
            logger=logging.getLogger(f"cifereca.plugin.{descriptor.id}"),
            _service_resolver=self.service,
            _services_resolver=self.services,
            _services_subscriber=self.subscribe_services,
            _broker_resolver=lambda broker_id, pid=descriptor.id, trusted=(record.package.sha256 == "builtin"): self._broker_for_plugin(pid, broker_id, trusted=trusted),
            _diagnostic_reporter=lambda message, status, detail, level, pid=descriptor.id: self._plugin_diag(pid, message, status, detail, level),
            _config_resolver=lambda scope_id, pid=descriptor.id, schema=descriptor.config_schema: self.config.get(pid, schema, scope_id=scope_id),
            _config_ids_resolver=lambda pid=descriptor.id, schema=descriptor.config_schema: self.config.scope_ids(pid, schema),
        )
        service = None
        activate = getattr(instance, "activate", None)
        if callable(activate):
            service = activate(context)
        published = service if service is not None else instance
        check = check_service_contract(descriptor.type, published)
        if not check.ok:
            deactivate = getattr(instance, "deactivate", None)
            if callable(deactivate):
                try:
                    deactivate()
                except Exception:
                    pass
            record.instance = None
            record.service = None
            record.state = "incompatible"
            record.reason = check.reason_code or "contract_mismatch"
            record.reason_detail = dict(check.detail or {})
            record.error = "activated service does not satisfy its public contract"
            self._diag("plugin contract validation", "error", {"plugin": descriptor.id, **record.reason_detail})
            return
        record.instance = instance
        record.service = published
        if record.degraded_details:
            record.state = "degraded"
            record.reason = "missing_optional_capability"
            record.reason_detail = {"contracts": tuple(record.degraded_details)}
        else:
            record.state = "ready"
            record.reason = ""
            record.reason_detail = {}
        record.error = ""
        self._diag("plugin activated", "ok", {"plugin": descriptor.id, "type": descriptor.type})

    def _deactivate_record(self, record: _RuntimeRecord) -> None:
        if record.instance is None:
            return
        deactivate = getattr(record.instance, "deactivate", None)
        if callable(deactivate):
            try:
                deactivate()
            except Exception as exc:
                self._diag("plugin deactivation", "error", {"plugin": record.descriptor.id, "error": str(exc)})
        record.instance = None
        record.service = None

    def deactivate_all(self) -> None:
        for record in reversed(list(self._records.values())):
            self._deactivate_record(record)
        roots = {Path(value).resolve() for value in self._import_roots}
        for name, module in list(sys.modules.items()):
            filename = getattr(module, "__file__", None)
            if not filename:
                continue
            try:
                resolved = Path(filename).resolve()
            except Exception:
                continue
            if any(root == resolved or root in resolved.parents for root in roots):
                sys.modules.pop(name, None)
        for value in list(self._import_roots):
            while value in sys.path:
                sys.path.remove(value)
        self._import_roots.clear()

    def service(self, plugin_id: str) -> object | None:
        if plugin_id in self._core_services:
            return self._core_services[plugin_id]
        record = self._records.get(plugin_id)
        return record.service if record is not None else None

    def services(self, category_id: str) -> tuple[ResolvedPluginService, ...]:
        rows: list[ResolvedPluginService] = []
        for status in self.candidates(category_id):
            service = self.service(status.descriptor.id)
            if service is None:
                continue
            d = status.descriptor
            category = self.categories.get(d.type)
            rows.append(ResolvedPluginService(
                d.id, d.version, d.package_id, d.package_version, d.type, service, status.effective_priority,
                category.contract_id, category.contract_version, tuple(d.capabilities), self._registry_generation,
            ))
        return tuple(rows)

    def subscribe_services(self, category_id: str, callback: Callable[[], None]) -> Callable[[], None]:
        category_id = str(category_id)
        callbacks = self._service_subscribers.setdefault(category_id, [])
        if callback not in callbacks:
            callbacks.append(callback)
        active = True
        def unsubscribe() -> None:
            nonlocal active
            if not active:
                return
            active = False
            try:
                callbacks.remove(callback)
            except ValueError:
                pass
        return unsubscribe

    def _notify_service_subscribers(self, category_ids: set[str] | None = None) -> None:
        keys = set(self._service_subscribers) if category_ids is None else set(category_ids)
        for category_id in sorted(keys):
            for callback in tuple(self._service_subscribers.get(category_id, ())):
                try:
                    callback()
                except Exception as exc:
                    self._diag("service subscription callback", "error", {"category": category_id, "error": str(exc)})

    def broker(self, broker_id: str) -> object | None:
        return self._brokers.get(str(broker_id))

    def _broker_for_plugin(self, plugin_id: str, broker_id: str, *, trusted: bool = False) -> object | None:
        broker = self.broker(broker_id)
        scoped = getattr(broker, "for_plugin", None)
        if callable(scoped):
            return scoped(plugin_id, trusted=trusted)
        return broker

    def _plugin_diag(self, plugin_id: str, message: str, status: str, detail: Any, level: str) -> None:
        if self.diagnostics is None:
            return
        try:
            from ..core.diagnostic_adept import PLUGIN_ID, PACKAGE_ID
            record = self._records.get(plugin_id)
            if (plugin_id == PLUGIN_ID and record is not None
                    and record.descriptor.id == PLUGIN_ID
                    and record.package.manifest.package.id == PACKAGE_ID):
                # The context callback binds this identity in Core. A field
                # named plugin_id in another plugin's payload proves nothing.
                self.diagnostics.add_core_kobo_adept(detail, status, level=level)
                return
            if plugin_id == PLUGIN_ID:
                # Do not fall back to free-form text for an unverified package
                # claiming this reserved producer identity.
                self.diagnostics.add("PLUGIN", "runtime diagnostic", "error",
                                     detail={"code": "unverified_runtime_producer"}, level=level)
                return
            payload = dict(detail or {}) if isinstance(detail, dict) else {"detail": detail}
            payload["plugin"] = plugin_id
            self.diagnostics.add("PLUGIN", message, status, detail=payload, level=level)
        except Exception:
            pass

    @staticmethod
    def _structured_reason(record: _RuntimeRecord) -> tuple[str, dict[str, object]]:
        code = str(record.reason or "")
        detail = dict(record.reason_detail or {})
        if code == "system_requirement":
            failed = [item for item in record.requirements if not item.ok]
            if failed:
                req = failed[0].requirement
                detail.setdefault("requirement_type", req.type)
                detail.setdefault("name", req.name or req.path)
                if req.type == "binary":
                    code = "binary_missing"
                elif req.type == "python-package":
                    code = "python_dependency_missing"
                elif req.type in {"file", "directory"}:
                    code = "system_resource_missing"
        elif code == "configuration":
            code = "configuration_missing"
        elif code == "service_unavailable":
            code = "missing_broker"
        return code, detail

    def status(self, plugin_id: str) -> PluginStatus:
        record = self._records[plugin_id]
        reason_code, reason_detail = self._structured_reason(record)
        return PluginStatus(
            descriptor=record.descriptor,
            state=record.state,  # type: ignore[arg-type]
            enabled=record.enabled,
            effective_priority=record.effective_priority,
            reason=record.reason,
            error=record.error,
            requirements=tuple(record.requirements),
            reason_code=reason_code,
            reason_detail=reason_detail,
        )

    def statuses(self) -> list[PluginStatus]:
        return [self.status(plugin_id) for plugin_id in sorted(self._records)]

    def errors(self) -> list[str]:
        return list(self._load_errors)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        if plugin_id not in self._records:
            self.refresh()
        if plugin_id not in self._records:
            raise KeyError(plugin_id)
        if not enabled:
            self._deactivate_plugin_and_dependents(plugin_id)
        self.state.set_enabled(plugin_id, enabled)
        self.activate()

    def _deactivate_dependents(self, plugin_id: str, _seen: set[str] | None = None) -> None:
        seen = _seen or set()
        if plugin_id in seen:
            return
        seen.add(plugin_id)
        for dependent_id, record in self._records.items():
            if any(dep.plugin == plugin_id for dep in record.descriptor.requires):
                self._deactivate_dependents(dependent_id, seen)
                self._deactivate_record(record)

    def _deactivate_plugin_and_dependents(self, plugin_id: str) -> None:
        self._deactivate_dependents(plugin_id)
        record = self._records.get(plugin_id)
        if record is not None:
            self._deactivate_record(record)

    def enabled_for_identity(self, plugin_id: str, identity_id: str) -> bool:
        """Return the effective per-Identity activation of a ready plugin.

        This is primarily used by singleton/public providers: one global plugin
        instance/configuration can be visible to one Identity and hidden from
        another without inventing credentials or duplicate provider instances.
        """
        if plugin_id not in self._records:
            self.refresh()
        record = self._records.get(plugin_id)
        if record is None or not record.enabled or record.state not in {"ready", "degraded"}:
            return False
        override = self.state.identity_enabled_override(plugin_id, str(identity_id))
        return True if override is None else bool(override)

    def set_identity_enabled(self, plugin_id: str, identity_id: str, enabled: bool | None) -> None:
        if plugin_id not in self._records:
            self.refresh()
        if plugin_id not in self._records:
            raise KeyError(plugin_id)
        self.state.set_identity_enabled(plugin_id, str(identity_id), enabled)

    def set_priority(self, plugin_id: str, priority: int | None) -> None:
        self.state.set_priority(plugin_id, priority)
        self.refresh()

    def candidates(self, category_id: str, *, ready_only: bool = True) -> list[PluginStatus]:
        rows = [status for status in self.statuses() if status.descriptor.type == category_id]
        if ready_only:
            rows = [status for status in rows if status.state in {"ready", "degraded"} and status.enabled]
        return sorted(rows, key=lambda status: (-status.effective_priority, status.descriptor.id))

    def select(self, category_id: str, predicate: Callable[[object], bool] | None = None) -> PluginStatus | None:
        category = self.categories.get(category_id)
        if category.resolution == "all":
            raise ValueError(f"category {category_id} uses resolution=all; use candidates()")
        for status in self.candidates(category_id):
            if predicate is None:
                return status
            service = self.service(status.descriptor.id)
            try:
                if predicate(service):
                    return status
            except Exception:
                continue
        return None

    def _active_manifests_with(
        self, package: InstalledPackage | None = None,
    ) -> tuple[list[object], list[Path]]:
        manifests: list[object] = []
        roots: list[Path] = []
        replacement_id = package.manifest.package.id if package is not None else None
        for active in self.packages.active_packages():
            if replacement_id and active.manifest.package.id == replacement_id:
                continue
            manifests.append(active.manifest)
            roots.append(active.root)
        if package is not None:
            manifests.append(package.manifest)
            roots.append(package.root)
        return manifests, roots

    def ensure_python_dependencies(
        self, *, activate: bool = True,
    ) -> list[dict[str, object]]:
        """Explicitly repair plugin Python dependencies in the Core-owned venv.

        An asynchronous CoreUI caller uses activate=False in a worker and
        reactivates plugins on the GTK main thread after the install finishes.
        Plugin code never invokes pip. CLI callers retain the default.
        """
        packages = self.packages.active_packages()
        manifests = [item.manifest for item in packages]
        transaction = self.python_dependencies.ensure(manifests, package_roots=[item.root for item in packages])
        transaction.commit()
        if activate:
            self.activate()
        return self.python_dependencies.diagnostic(manifests)

    def install(self, archive: Path, *, activate: bool = True, replace: bool = False) -> InstalledPackage:
        manifest = self.installer.inspect(Path(archive))
        package_id = manifest.package.id
        if package_id in _RETIRED_YOUTH_SOURCE_PACKAGES:
            raise ValueError("This retired youth provider can no longer be installed")
        version = manifest.package.version
        old_active = self.packages.active_version(package_id)
        final = self.packages.version_root(package_id, version)
        backup_root: Path | None = None
        backup_parent: Path | None = None
        if replace and final.exists():
            backup_parent = Path(tempfile.mkdtemp(prefix=f"cifereca-plugin-{package_id}-"))
            backup_root = backup_parent / "package"
            shutil.copytree(final, backup_root)
        if replace:
            # A dev snapshot may replace code while keeping the same package
            # version. Tear down live services first so the next activation is
            # guaranteed to import the replacement instead of reusing instances.
            self.deactivate_all()
        transaction: PythonDependencyTransaction | None = None
        installed: InstalledPackage | None = None
        try:
            # Do not make a new package active before its Python dependencies are
            # resolved. This keeps discovery manifest-first and entrypoint-free.
            installed = self.installer.install(archive, activate=False, replace=replace)
            if activate:
                manifests, roots = self._active_manifests_with(installed)
                transaction = self.python_dependencies.ensure(manifests, package_roots=roots)
                self.packages.set_active(package_id, version)
            if transaction is not None:
                # Dependency resolution itself is transactional. Plugin activation
                # errors remain isolated statuses, preserving the existing manager
                # contract for third-party plugins.
                transaction.commit()
            self.activate()
            return installed
        except Exception:
            if transaction is not None:
                try:
                    transaction.rollback()
                except Exception as exc:
                    self._diag("python dependency rollback", "error", {"package": package_id, "error": str(exc)})
            try:
                self.deactivate_all()
                if backup_root is not None and backup_root.exists():
                    if final.exists():
                        shutil.rmtree(final, ignore_errors=True)
                    shutil.copytree(backup_root, final)
                elif installed is not None and old_active != version:
                    self.installer.uninstall_version(package_id, version)
                pointer = self.packages._pointer_path(package_id)
                if old_active and self.packages.version_root(package_id, old_active).is_dir():
                    self.packages.set_active(package_id, old_active)
                elif not old_active:
                    pointer.unlink(missing_ok=True)
                self.activate()
            except Exception as exc:
                self._diag("plugin install rollback", "error", {"package": package_id, "error": str(exc)})
            raise
        finally:
            if backup_parent is not None:
                shutil.rmtree(backup_parent, ignore_errors=True)

    def rollback(self, package_id: str) -> str:
        pointer = self.packages.pointer(package_id)
        current = pointer.get("active")
        target = pointer.get("previous")
        if not target or not self.packages.version_root(package_id, target).is_dir():
            candidates = [version for version in self.packages.installed_versions(package_id) if version != current]
            target = candidates[0] if candidates else None
        if not target:
            from .errors import PackageInstallError
            raise PackageInstallError(f"no rollback version available for {package_id}")
        self.deactivate_all()
        package = self.packages.load(package_id, target)
        manifests, roots = self._active_manifests_with(package)
        transaction = self.python_dependencies.ensure(manifests, package_roots=roots)
        try:
            self.packages.set_active(package_id, target)
            self.activate()
            transaction.commit()
            return target
        except Exception:
            transaction.rollback()
            if current and self.packages.version_root(package_id, current).is_dir():
                self.packages.set_active(package_id, current)
                self.activate()
            raise

    def inspect_archive(self, archive: Path):
        return self.installer.inspect(Path(archive))

    def set_active_package_version(self, package_id: str, version: str) -> None:
        old_active = self.packages.active_version(package_id)
        self.deactivate_all()
        package = self.packages.load(package_id, version)
        manifests, roots = self._active_manifests_with(package)
        transaction = self.python_dependencies.ensure(manifests, package_roots=roots)
        try:
            self.packages.set_active(package_id, version)
            self.activate()
            transaction.commit()
        except Exception:
            transaction.rollback()
            if old_active and self.packages.version_root(package_id, old_active).is_dir():
                self.packages.set_active(package_id, old_active)
                self.activate()
            raise

    def remove_package(self, package_id: str, *, version: str | None = None) -> None:
        self.deactivate_all()
        if version is not None:
            self.installer.uninstall_version(package_id, version)
        else:
            root = self.packages.package_root(package_id)
            import shutil
            shutil.rmtree(root, ignore_errors=True)
        self.activate()

    def package_inventory(self, *, include_expected: bool = False) -> list[dict[str, Any]]:
        installed = self.packages.all_packages()
        expected = expected_plugin_packages()
        package_ids = set(installed)
        if include_expected:
            package_ids.update(expected)

        rows: list[dict[str, Any]] = []
        for package_id in sorted(package_ids):
            versions = list(installed.get(package_id, ()))
            pointer = self.packages.pointer(package_id) if versions else {"active": None, "previous": None}
            active = pointer.get("active")
            package = None
            if active:
                try:
                    package = self.packages.load(package_id, active)
                except Exception:
                    package = None
            release_info = expected.get(package_id, {})
            freshness = package_freshness(package_id, active, versions)
            # Do not report a stale receipt after a full release or manual
            # switch changed the active package bytes/version.
            receipt = installed_plugin_update_receipt(
                self.packages.package_root(package_id), package_id,
                [active] if active else versions,
            )
            if receipt and package and receipt["package_sha256"] != package.sha256:
                receipt = None
            rows.append({
                "package_id": package_id,
                "name": package.manifest.package.name if package else release_info.get("name", package_id),
                "publisher": package.manifest.package.publisher if package else ("cifereca" if release_info else ""),
                "active": active,
                "previous": pointer.get("previous"),
                "versions": versions,
                "sha256": package.sha256 if package else "",
                "last_plugin_update": receipt,
                **freshness,
            })
        if self._builtin_ui:
            from cifereca import __version__
            rows.insert(0, {
                "package_id": "core.ui", "name": "Interface cifereca", "publisher": "cifereca",
                "active": __version__, "previous": None, "versions": [__version__],
                "sha256": "builtin", "builtin": True,
                "expected_version": None, "freshness": "unknown", "freshness_reason": "builtin",
            })
        return rows

    def diagnostic(self, plugin_id: str | None = None) -> list[dict[str, Any]]:
        from cifereca.system_status import installation_date
        statuses = [self.status(plugin_id)] if plugin_id else self.statuses()
        package_rows = {row["package_id"]: row for row in self.package_inventory(include_expected=True)}
        out: list[dict[str, Any]] = []
        for status in statuses:
            d = status.descriptor
            package_row = package_rows.get(d.package_id, {})
            out.append({
                "plugin_id": d.id, "display_name": d.display_name or d.id, "type": d.type,
                "version": d.version, "package_id": d.package_id, "package_version": d.package_version,
                "expected_package_version": package_row.get("expected_version"),
                "package_freshness": package_row.get("freshness", "unknown"),
                "package_freshness_reason": package_row.get("freshness_reason", "unmanaged"),
                "last_plugin_update": package_row.get("last_plugin_update"),
                "description": d.description, "help_text": d.help_text,
                "enabled": status.enabled, "state": status.state,
                "installed_at": installation_date(self._records[d.id].package.root),
                "builtin": self._records[d.id].package.sha256 == "builtin",
                "priority": status.effective_priority, "reason": status.reason, "error": status.error,
                "reason_code": status.reason_code, "reason_detail": dict(status.reason_detail),
                "requires": [{"plugin": x.plugin, "version": x.version} for x in d.requires],
                "recommends": [{"plugin": x.plugin, "version": x.version} for x in d.recommends],
                "conflicts": [{"plugin": x.plugin, "version": x.version} for x in d.conflicts],
                "capabilities": list(d.capabilities),
                "requires_brokers": list(d.requires_brokers),
                "requires_contracts": [
                    {"category": x.category, "contract": x.contract, "version": x.version, "mode": x.mode, "capabilities": list(x.capabilities)}
                    for x in d.requires_contracts
                ],
                "requires_services": list(d.requires_services),
                "requirements": [
                    {"type": r.requirement.type, "name": r.requirement.name or r.requirement.path,
                     "version": r.requirement.version, "ok": r.ok, "detected": r.detected, "message": r.message}
                    for r in status.requirements
                ],
            })
        return out

    def page_contributions(self) -> list[Any]:
        from dataclasses import replace
        from cifereca.plugin_api import UiPageContribution
        pages: list[Any] = []
        for status in self.candidates("ui.page"):
            factory = getattr(self.service(status.descriptor.id), "pages", None)
            if not callable(factory):
                continue
            try:
                contributions = list(factory() or [])
            except Exception as exc:
                self._diag("ui.page contributions", "error", {"plugin": status.descriptor.id, "error": str(exc)})
                continue
            for page in contributions:
                try:
                    if not isinstance(page, UiPageContribution):
                        raise TypeError("pages() must return UiPageContribution objects")
                    if not isinstance(page.route, str) or not page.route.strip() or not callable(page.factory):
                        raise ValueError("page route and factory are required")
                    if not isinstance(page.title, str) or not isinstance(page.group, str):
                        raise ValueError("page title and group must be strings")
                    pages.append(replace(page, order=int(page.order), priority=status.effective_priority + int(page.priority)))
                except Exception as exc:
                    self._diag("ui.page contribution", "error", {"plugin": status.descriptor.id, "error": str(exc)})
        return sorted(pages, key=lambda page: (page.order, page.route))

    def widget_catalog(self) -> object | None:
        selected = self.select("ui.widgets")
        return self.service(selected.descriptor.id) if selected is not None else None

    def plugin_config(self, plugin_id: str, *, scope_id: str | None = None) -> dict[str, Any]:
        if plugin_id not in self._records:
            self.refresh()
        record = self._records.get(plugin_id)
        if record is None:
            raise KeyError(plugin_id)
        return self.config.get(plugin_id, record.descriptor.config_schema, scope_id=scope_id)

    def set_plugin_config(self, plugin_id: str, values: dict[str, Any], *, scope_id: str | None = None) -> dict[str, Any]:
        if plugin_id not in self._records:
            self.refresh()
        record = self._records.get(plugin_id)
        if record is None:
            raise KeyError(plugin_id)
        result = self.config.set(plugin_id, record.descriptor.config_schema, values, scope_id=scope_id)
        self._deactivate_plugin_and_dependents(plugin_id)
        self.activate()
        return result

    def close(self) -> None:
        self.deactivate_all()
