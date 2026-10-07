from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Mapping

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .models import CatalogServiceFactory, DiagnosticsCheckProvider, DocumentTransformer, MaintenanceTool, TransportService, UiPageProvider, UiTitlebarProvider, WidgetCatalog

ContractStatus = Literal["experimental", "stable", "deprecated"]
_CAPABILITY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+$")


@dataclass(frozen=True, slots=True)
class ContractDefinition:
    category: str
    contract_id: str
    version: str
    status: ContractStatus = "stable"
    capabilities: tuple[str, ...] = ()
    protocol_name: str = ""


@dataclass(frozen=True, slots=True)
class ContractCheck:
    ok: bool
    reason_code: str = ""
    detail: Mapping[str, object] | None = None


# The public registry is intentionally data-only. Contract versions evolve
# independently from Plugin API and manifest versions.
CONTRACT_REGISTRY: tuple[ContractDefinition, ...] = (
    ContractDefinition("provider.library", "cifereca.provider", "1", capabilities=("catalog.search", "catalog.holdings", "acquisition.borrow", "acquisition.download")),
    ContractDefinition("catalog.service", "cifereca.catalog.service", "1", protocol_name="CatalogServiceFactory"),
    ContractDefinition("catalog.service", "cifereca.catalog.service", "1", protocol_name="CatalogServiceFactory"),
    ContractDefinition("transport", "cifereca.transport", "1", protocol_name="TransportService"),
    ContractDefinition("drm.handler", "cifereca.drm", "1"),
    ContractDefinition("runtime.adapter", "cifereca.runtime", "1"),
    ContractDefinition("storage.target", "cifereca.storage", "1"),
    ContractDefinition("ui.page", "cifereca.ui.page", "1", protocol_name="UiPageProvider"),
    ContractDefinition("ui.titlebar", "cifereca.ui.titlebar", "1", protocol_name="UiTitlebarProvider"),
    ContractDefinition("ui.widgets", "cifereca.ui.widgets", "1", protocol_name="WidgetCatalog"),
    ContractDefinition("sync", "cifereca.sync", "1", capabilities=("sync.loans", "sync.library")),
    ContractDefinition("document.transformer", "cifereca.document.transformer", "1", capabilities=("artifact.plan", "artifact.transform"), protocol_name="DocumentTransformer"),
    ContractDefinition("recommendation", "cifereca.recommendation", "1"),
    ContractDefinition("catalog.enrichment", "cifereca.catalog.enrichment", "1"),
    ContractDefinition("device.profile", "cifereca.device.profile", "1"),
    ContractDefinition("artifact.transform.preset", "cifereca.artifact.transform.preset", "1"),
    ContractDefinition("maintenance.tool", "cifereca.maintenance.tool", "1", protocol_name="MaintenanceTool"),
    ContractDefinition("diagnostics.check", "cifereca.diagnostics.check", "1", protocol_name="DiagnosticsCheckProvider"),
    ContractDefinition("meta.bundle", "cifereca.meta", "1"),
)

_BY_CATEGORY = {item.category: item for item in CONTRACT_REGISTRY}
_PROTOCOLS = {
    "catalog.service": CatalogServiceFactory,
    "transport": TransportService,
    "catalog.service": CatalogServiceFactory,
    "ui.page": UiPageProvider,
    "ui.titlebar": UiTitlebarProvider,
    "ui.widgets": WidgetCatalog,
    "document.transformer": DocumentTransformer,
    "maintenance.tool": MaintenanceTool,
    "diagnostics.check": DiagnosticsCheckProvider,
}


def contract_definition(category: str) -> ContractDefinition | None:
    return _BY_CATEGORY.get(str(category))


def split_contract(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if "/" not in text:
        return text, ""
    contract_id, version = text.rsplit("/", 1)
    return contract_id.strip(), version.strip()


def capability_name_valid(value: str) -> bool:
    return bool(_CAPABILITY_RE.fullmatch(str(value or "").strip()))


def contract_version_matches(actual: str, specifier: str) -> bool:
    if not specifier:
        return True
    try:
        return Version(str(actual)) in SpecifierSet(str(specifier))
    except (InvalidVersion, InvalidSpecifier):
        return False


def check_service_contract(category: str, service: object) -> ContractCheck:
    protocol = _PROTOCOLS.get(str(category))
    if protocol is None:
        return ContractCheck(True)
    if isinstance(service, protocol):
        return ContractCheck(True)
    definition = contract_definition(category)
    return ContractCheck(False, "contract_mismatch", {
        "category": str(category),
        "contract": definition.contract_id if definition else "",
        "version": definition.version if definition else "",
        "protocol": getattr(protocol, "__name__", str(protocol)),
    })


__all__ = [
    "ContractStatus", "ContractDefinition", "ContractCheck", "CONTRACT_REGISTRY",
    "contract_definition", "split_contract", "capability_name_valid",
    "contract_version_matches", "check_service_contract",
]
