from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from .models import PluginDirectories, ResolvedPluginService


@dataclass(slots=True)
class PluginContext:
    """Narrow public context passed to plugin code.

    The context intentionally does not expose LibraryService, Storage or GTK internals.
    More brokered capabilities can be added compatibly during later stages.
    """

    plugin_id: str
    package_id: str
    directories: PluginDirectories
    config: Mapping[str, Any] = field(default_factory=dict)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("cifereca.plugin"))
    _service_resolver: Callable[[str], object | None] = field(default=lambda _plugin_id: None, repr=False)
    _services_resolver: Callable[[str], tuple[ResolvedPluginService, ...]] = field(default=lambda _category_id: (), repr=False)
    _services_subscriber: Callable[[str, Callable[[], None]], Callable[[], None]] = field(default=lambda _category_id, _callback: (lambda: None), repr=False)
    _broker_resolver: Callable[[str], object | None] = field(default=lambda _broker_id: None, repr=False)
    _diagnostic_reporter: Callable[[str, str, Mapping[str, Any] | None, str], None] = field(default=lambda _message, _status, _detail, _level: None, repr=False)
    _config_resolver: Callable[[str | None], Mapping[str, Any]] = field(default=lambda _scope_id: {}, repr=False)
    _config_ids_resolver: Callable[[], list[str]] = field(default=lambda: [], repr=False)

    @property
    def data_directory(self) -> Path:
        return self.directories.data

    @property
    def cache_directory(self) -> Path:
        return self.directories.cache

    def service(self, plugin_id: str) -> object | None:
        return self._service_resolver(str(plugin_id))

    def services(
        self, category_id: str, *, contract: str = "", version: str = "", capabilities: tuple[str, ...] = (),
    ) -> tuple[ResolvedPluginService, ...]:
        rows = tuple(self._services_resolver(str(category_id)))
        required_caps = set(capabilities)
        out = []
        for row in rows:
            if contract and row.contract_id != contract:
                continue
            if version:
                try:
                    if Version(row.contract_version) not in SpecifierSet(version):
                        continue
                except Exception:
                    continue
            if required_caps and not required_caps.issubset(set(row.capabilities)):
                continue
            out.append(row)
        return tuple(out)

    def select_service(
        self, category_id: str, *, request: object | None = None, contract: str = "", version: str = "",
        capabilities: tuple[str, ...] = (),
    ) -> ResolvedPluginService | None:
        """Resolve by contract/capability first, then supports(), priority and plugin id.

        The registry already orders compatible rows by effective priority and stable
        plugin id. A provider exposing ``supports(request)`` can reject a request
        without becoming a hidden ordering protocol.
        """
        for row in self.services(category_id, contract=contract, version=version, capabilities=capabilities):
            if request is not None:
                supports = getattr(row.service, "supports", None)
                if callable(supports):
                    try:
                        if not bool(supports(request)):
                            continue
                    except Exception:
                        continue
            return row
        return None

    def subscribe_services(self, category_id: str, callback: Callable[[], None]) -> Callable[[], None]:
        return self._services_subscriber(str(category_id), callback)

    def broker(self, broker_id: str) -> object | None:
        return self._broker_resolver(str(broker_id))

    @property
    def logging_level(self) -> int:
        return int(self.logger.getEffectiveLevel())

    def report_diagnostic(self, message: str, status: str = "ok", *, detail: Mapping[str, Any] | None = None, level: str = "detailed") -> None:
        self._diagnostic_reporter(str(message), str(status), detail, str(level))

    def configuration_ids(self) -> list[str]:
        """Return configured stable ids for scoped configuration."""
        return list(self._config_ids_resolver())

    def configuration(self, scope_id: str | None = None) -> Mapping[str, Any]:
        """Return validated configuration for this plugin/scope.

        For plugin-scoped schemas this is equivalent to ``config``.  Instance,
        Identity and target scoped schemas require their stable scope id.
        """
        return self._config_resolver(scope_id)


__all__ = ["PluginContext"]
