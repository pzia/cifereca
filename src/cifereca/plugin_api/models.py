from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol, runtime_checkable

PluginState = Literal["disabled", "ready", "blocked", "degraded", "incompatible", "error"]
ResolutionMode = Literal["priority", "all", "single"]
InstantiationMode = Literal["multiple", "per_identity", "singleton_public"]
ContractRequirementMode = Literal["required", "optional", "degraded"]


@dataclass(frozen=True, slots=True)
class CategoryDefinition:
    id: str
    display_name: str
    contract: str
    resolution: ResolutionMode = "priority"
    description: str = ""

    @property
    def contract_id(self) -> str:
        return self.contract.rsplit("/", 1)[0] if "/" in self.contract else self.contract

    @property
    def contract_version(self) -> str:
        return self.contract.rsplit("/", 1)[1] if "/" in self.contract else ""


@dataclass(frozen=True, slots=True)
class Dependency:
    plugin: str
    version: str = ""


@dataclass(frozen=True, slots=True)
class ContractRequirement:
    category: str
    contract: str
    version: str = ""
    mode: ContractRequirementMode = "required"
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SystemRequirement:
    type: str
    name: str = ""
    version: str = ""
    path: str = ""
    version_args: tuple[str, ...] = ("--version",)
    import_name: str = ""


@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    id: str
    type: str
    version: str
    package_id: str
    package_version: str
    entrypoint: str | None = None
    priority: int = 0
    requires: tuple[Dependency, ...] = ()
    recommends: tuple[Dependency, ...] = ()
    conflicts: tuple[Dependency, ...] = ()
    system_requires: tuple[SystemRequirement, ...] = ()
    enabled_by_default: bool = True
    display_name: str = ""
    description: str = ""
    config_schema: Mapping[str, Any] = field(default_factory=dict)
    instantiation: InstantiationMode = "multiple"
    help_text: str = ""
    capabilities: tuple[str, ...] = ()
    # Deprecated public v1 field. Interpreted as broker ids until Plugin API 2.
    requires_services: tuple[str, ...] = ()
    requires_brokers: tuple[str, ...] = ()
    requires_contracts: tuple[ContractRequirement, ...] = ()


@dataclass(frozen=True, slots=True)
class RequirementStatus:
    requirement: SystemRequirement
    ok: bool
    detected: str = ""
    message: str = ""


@dataclass(frozen=True, slots=True)
class PluginStatus:
    descriptor: PluginDescriptor
    state: PluginState
    enabled: bool
    effective_priority: int
    reason: str = ""
    error: str = ""
    requirements: tuple[RequirementStatus, ...] = ()
    reason_code: str = ""
    reason_detail: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PluginDirectories:
    data: Path
    cache: Path




@dataclass(frozen=True, slots=True)
class AcquisitionOffer:
    offer_id: str
    media_type: str = "ebook"
    format: str = ""
    drm: str = "none"
    source_kind: str = "http"
    source: Mapping[str, Any] = field(default_factory=dict)
    priority: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    formats: tuple[str, ...] = ()
    preferred_format: str = ""
    format_evidence: Mapping[str, Any] = field(default_factory=dict)


_ACQUISITION_FILE_FORMATS = {
    "epub", "kepub", "pdf", "mp3", "m4a", "ogg", "opus", "flac", "wav",
}
_ACQUISITION_MIME_FORMATS = {
    "application/epub+zip": "epub",
    "application/pdf": "pdf",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
}


def normalize_acquisition_format(value: object) -> str:
    """Normalize an explicitly attested acquisition file format."""
    raw = str(value or "").strip().casefold()
    if raw in _ACQUISITION_FILE_FORMATS:
        return raw
    return _ACQUISITION_MIME_FORMATS.get(raw, "")


def acquisition_offer_formats(offer: AcquisitionOffer) -> tuple[str, ...]:
    """Return deduplicated attested alternatives, preserving legacy format."""
    values = [offer.format, *tuple(offer.formats or ())]
    out: list[str] = []
    for value in values:
        normalized = normalize_acquisition_format(value)
        if normalized and normalized not in out:
            out.append(normalized)
    return tuple(out)


def acquisition_offer_preferred_format(offer: AcquisitionOffer) -> str:
    """Return the preference only when it is one of the attested alternatives."""
    preferred = normalize_acquisition_format(offer.preferred_format)
    return preferred if preferred and preferred in acquisition_offer_formats(offer) else ""


def acquisition_offers_metadata(offers: object) -> dict[str, Any]:
    """Project offer alternatives into search metadata without inventing a choice."""
    if isinstance(offers, AcquisitionOfferSet):
        from .acquisition import acquisition_format_projection
        projection = acquisition_format_projection(offers)
        result = {"available_formats": list(projection["available_formats"]),
                  "format_source": "acquisition_offers" if projection["available_formats"] else "unknown"}
        preferred = projection["preferred_formats"]
        if preferred:
            result["preferred_format"] = preferred[0]
        return result
    rows = tuple(offers or ()) if isinstance(offers, (list, tuple)) else ()
    formats: list[str] = []
    preferences: list[str] = []
    for offer in rows:
        if not isinstance(offer, AcquisitionOffer):
            continue
        for value in acquisition_offer_formats(offer):
            if value not in formats:
                formats.append(value)
        preferred = acquisition_offer_preferred_format(offer)
        if preferred and preferred not in preferences:
            preferences.append(preferred)
    result: dict[str, Any] = {
        "available_formats": formats,
        "format_source": "acquisition_offers" if formats else "unknown",
    }
    if len(preferences) == 1:
        result["preferred_format"] = preferences[0]
    return result


@dataclass(frozen=True, slots=True)
class AcquisitionPreferences:
    """Ordered user/provider preferences applied only to attested offers."""
    format_order: tuple[str, ...] = ()
    offer_order: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AcquisitionOfferSet:
    """A complete acquisition choice set without implicit format inference."""
    offers: tuple[AcquisitionOffer, ...] = ()
    preferences: AcquisitionPreferences = field(default_factory=AcquisitionPreferences)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactInput:
    path: Path
    artifact_id: str = ""
    title: str = ""
    format: str = ""
    sha256: str = ""
    kind: Literal["file", "bundle"] = "file"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StorageTarget:
    target_id: str
    mount: Path
    label: str = ""
    kind: str = "filesystem"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StorageResult:
    target_id: str
    relative_path: str
    path: Path
    present: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)




@dataclass(frozen=True, slots=True)
class DrmMutationRequest:
    operation: Literal["fulfill", "renew", "return"]
    identity_id: str
    provider_id: str = ""
    entitlement_id: str = ""
    loan_id: str = ""
    source: Mapping[str, Any] = field(default_factory=dict)
    destination: Path | None = None
    media_format: str = ""
    profile_ref: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DrmMutationResult:
    ok: bool
    operation: str
    artifact: ArtifactInput | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class DrmRuntime(Protocol):
    def supports(self, request: DrmMutationRequest) -> bool: ...
    def execute(self, request: DrmMutationRequest) -> DrmMutationResult | Mapping[str, Any]: ...



@dataclass(frozen=True, slots=True)
class ResolvedPluginService:
    plugin_id: str
    plugin_version: str
    package_id: str
    package_version: str
    category_id: str
    service: object
    effective_priority: int = 0
    contract_id: str = ""
    contract_version: str = ""
    capabilities: tuple[str, ...] = ()
    registry_generation: int = 0


@dataclass(frozen=True, slots=True)
class Provenance:
    source: str
    provider_id: str = ""
    source_id: str = ""
    observed_at: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


YouthAgeKind = Literal["interest", "reading", "recommended", "publisher", "inferred"]


@dataclass(frozen=True, slots=True)
class YouthAgeRange:
    """Source-attested age range, without inventing a missing bound.

    `age_min=None` means no lower bound was asserted (e.g. "jusqu'à 6 ans").
    `age_max=None` means no upper bound was asserted (e.g. "8 ans et plus").
    Both bounds absent means unknown: never a positive age-filter match.
    """

    age_min: int | None = None
    age_max: int | None = None
    age_kind: YouthAgeKind = "recommended"
    source_label: str = ""
    provenance: Provenance | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.age_kind not in ("interest", "reading", "recommended", "publisher", "inferred"):
            raise ValueError("unknown youth age kind")
        for name, value in (("age_min", self.age_min), ("age_max", self.age_max)):
            if value is not None and (type(value) is not int or not 0 <= value <= 120):
                raise ValueError(f"{name} must be an integer between 0 and 120")
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError("age_min exceeds age_max")
        if self.confidence is not None:
            from math import isfinite
            if (not isinstance(self.confidence, (int, float))
                    or isinstance(self.confidence, bool)
                    or not isfinite(self.confidence)
                    or not 0 <= self.confidence <= 1):
                raise ValueError("confidence must be a finite number between 0 and 1")

    def contains(self, age: int) -> bool:
        """Match an age under 18; unknown ranges never imply permission."""
        if type(age) is not int or age < 0 or age >= 18:
            return False
        if self.age_min is None and self.age_max is None:
            return False
        return (self.age_min is None or age >= self.age_min) and (
            self.age_max is None or age <= self.age_max
        )


@dataclass(frozen=True, slots=True)
class BookRef:
    book_id: str
    title: str = ""
    authors: tuple[str, ...] = ()
    identifiers: Mapping[str, str] = field(default_factory=dict)
    provenance: Provenance | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class BookReferenceRecommendationService(Protocol):
    """Optional recommendation capability using a concrete book as seed."""

    def recommend_from_book(self, book: BookRef) -> str: ...


@dataclass(frozen=True, slots=True)
class DetailField:
    id: str
    label: str
    value: object
    kind: str = "text"
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class DetailSection:
    id: str
    title: str
    fields: tuple[DetailField, ...] = ()
    order: int = 100


@dataclass(frozen=True, slots=True)
class UiBookListEntry:
    """Neutral book-row model rendered by the shell's standard list widget."""
    key: str
    title: str
    subtitle: str = ""
    meta: str = ""
    badge: str = ""
    thumbnail_url: str | None = None


@dataclass(frozen=True, slots=True)
class UiBookDetailAction:
    id: str
    title: str
    icon_name: str = "view-more-symbolic"
    uri: str = ""
    callback: Callable[[], object] | None = None


@dataclass(frozen=True, slots=True)
class DocumentLink:
    id: str
    label: str
    url: str
    relation: str = "notice"
    icon_name: str = "external-link-symbolic"


@dataclass(frozen=True, slots=True)
class UiBookDetailPresentation:
    title: str
    subtitle: str = ""
    thumbnail_url: str | None = None
    badges: tuple[str, ...] = ()
    sections: tuple[DetailSection, ...] = ()
    actions: tuple[UiBookDetailAction, ...] = ()
    links: tuple[DocumentLink, ...] = ()


@dataclass(frozen=True, slots=True)
class CachedBookDetail:
    """Bounded, cache-only book detail contribution from a plugin.

    The book and identity must match the request exactly. Freshness and source
    are explicit: the Core does not turn stale or inferred evidence into facts.
    Plugins must not perform network or disk scans in cached_detail().
    """

    book_id: str
    identity_id: str
    sections: tuple[DetailSection, ...] = ()
    actions: tuple[UiBookDetailAction, ...] = ()
    source: Provenance | None = None
    updated_at: str = ""
    freshness_token: str = ""
    stale: bool = False


@runtime_checkable
class BookDetailCacheEnricher(Protocol):
    """Optional service resolved via category book.detail.enrichment.

    Strictly read an already materialized local cache. No network access,
    subscription refresh, search, acquisition, or filesystem scan.
    """

    def cached_detail(self, book: BookRef, *, identity_id: str) -> CachedBookDetail | None: ...


@runtime_checkable
class BookDetailCacheNotifier(Protocol):
    """Optional invalidation notification when the plugin cache changes."""

    def subscribe_cache(self, callback: Callable[[], None]) -> Callable[[], None]: ...


@dataclass(frozen=True, slots=True)
class FacetTag:
    id: str
    label: str
    value: str
    count: int | None = None
    group: str = ""


@dataclass(frozen=True, slots=True)
class ArtifactFormat:
    media_type: str
    format: str
    drm: str = "none"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    book_id: str | None
    format: ArtifactFormat
    size_bytes: int
    sha256: str
    variant_kind: str = "original"
    filename: str = ""


@dataclass(frozen=True, slots=True)
class ArtifactVariant:
    artifact_id: str
    book_id: str
    source_artifact_id: str
    variant_kind: str
    format: ArtifactFormat
    size_bytes: int
    sha256: str
    filename: str
    transformer_plugin_id: str
    transformer_version: str
    profile_plugin_id: str | None = None
    profile_version: str | None = None
    options_fingerprint: str = ""
    source_sha256: str = ""
    created_at: str = ""
    stale: bool = False
    facts: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactPlacementRef:
    placement_id: str
    artifact_id: str
    identity_id: str | None = None
    provider_id: str = ""
    source_item_id: str = ""
    state: str = "present"
    kind: str = "library"
    filename: str = ""
    storage_target_id: str | None = None
    storage_plugin_id: str | None = None
    facts: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactPossessionRef:
    identity_id: str | None
    provider_id: str
    source_item_id: str
    kind: Literal["local", "owned", "loan", "license", "device", "storage"]
    state: str = "active"
    account_id: str | None = None
    entitlement_id: str | None = None
    expires_at: str | None = None
    facts: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LogicalBookArtifacts:
    book: BookRef
    artifacts: tuple[ArtifactRef, ...] = ()
    variants: tuple[ArtifactVariant, ...] = ()
    placements: tuple[ArtifactPlacementRef, ...] = ()
    possessions: tuple[ArtifactPossessionRef, ...] = ()


LibraryMutationOperation = Literal["normalize_filename", "move_placement", "delete_duplicate", "replace_cover", "delete_variant", "set_preferred_variant"]


@dataclass(frozen=True, slots=True)
class LibraryMutationRequest:
    operation: LibraryMutationOperation
    placement_id: str = ""
    keep_placement_id: str = ""
    target_identity_id: str | None = None
    book_id: str = ""
    cover_artifact_id: str = ""
    artifact_id: str = ""
    purpose: str = "read"
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LibraryMutationPlan:
    operation: LibraryMutationOperation
    summary: str
    request: LibraryMutationRequest
    destructive: bool = False
    changes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    facts: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LibraryMutationResult:
    ok: bool
    changed: int = 0
    summary: str = ""
    placements: tuple[ArtifactPlacementRef, ...] = ()
    warnings: tuple[str, ...] = ()
    facts: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactRouteRequest:
    artifact: ArtifactRef
    target_id: str = ""
    filename: str = ""
    identity_id: str | None = None
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactRouteTarget:
    plugin_id: str
    target: StorageTarget
    effective_priority: int = 0
    registry_generation: int = 0
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactRoutePlan:
    request: ArtifactRouteRequest
    target: ArtifactRouteTarget
    filename: str
    summary: str = ""
    facts: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactTransferResult:
    ok: bool
    artifact: ArtifactRef
    target: ArtifactRouteTarget
    placement: ArtifactPlacementRef | None = None
    relative_path: str = ""
    warnings: tuple[str, ...] = ()
    facts: Mapping[str, object] = field(default_factory=dict)



@dataclass(frozen=True, slots=True)
class CatalogSearchBatch:
    provider_id: str
    provider_label: str
    results: tuple[object, ...] = ()
    warnings: tuple[str, ...] = ()
    error_code: str = ""
    error_message: str = ""
    cached: bool = False
    elapsed_ms: int = 0
    # Closed Core status, not a provider-supplied message. Optional trailing
    # field preserves old positional consumers of the public 0.4 contract.
    result_status: Literal[
        "live", "cached", "partial", "known_catalog_stale",
        "unavailable", "fallback_global",
    ] = "live"


@dataclass(frozen=True, slots=True)
class CatalogSearchSummary:
    query: str
    result_count: int
    providers_total: int
    providers_completed: int
    providers_failed: int
    cancelled: bool = False
    warnings: tuple[str, ...] = ()
    started_at: str = ""
    completed_at: str = ""


@dataclass(frozen=True, slots=True)
class CacheNamespaceInfo:
    namespace_id: str
    owner: str
    scope: Literal["plugin", "core"]
    entries: int = 0
    size_bytes: int = 0
    expired_entries: int = 0
    purgeable: bool = True
    description: str = ""


@dataclass(frozen=True, slots=True)
class CachePurgeResult:
    ok: bool
    entries_removed: int = 0
    bytes_freed: int = 0
    namespaces: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionStatistics:
    window_seconds: int
    from_timestamp: int
    to_timestamp: int
    acquisitions_succeeded: int = 0
    acquisitions_failed: int = 0
    imports_succeeded: int = 0
    imports_failed: int = 0
    synchronizations_succeeded: int = 0
    synchronizations_failed: int = 0
    transformations_succeeded: int = 0
    transformations_failed: int = 0
    transfers_succeeded: int = 0
    transfers_failed: int = 0

    @property
    def total(self) -> int:
        return sum((
            self.acquisitions_succeeded, self.acquisitions_failed,
            self.imports_succeeded, self.imports_failed,
            self.synchronizations_succeeded, self.synchronizations_failed,
            self.transformations_succeeded, self.transformations_failed,
            self.transfers_succeeded, self.transfers_failed,
        ))

@dataclass(frozen=True, slots=True)
class SyncRequest:
    identity_id: str | None = None
    deep: bool = False
    scopes: tuple[str, ...] = ()
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SyncResult:
    ok: bool
    changed: int = 0
    warnings: tuple[str, ...] = ()
    facts: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AccountRef:
    account_id: str
    identity_id: str
    provider_id: str
    label: str
    connected: bool = True
    capabilities: tuple[str, ...] = ()
    # Scope v2 is additive: old plugin fixtures retain the v1 defaults.
    # credential_id is the Core account binding ID, not an authentication
    # secret. Its presence is NOT authorization to download or alter rights.
    # Consumers must revalidate the same Core owner/session after provider I/O.
    credential_id: str = ""
    scope_version: int = 1


@dataclass(frozen=True, slots=True)
class SyncPhaseState:
    """Non-authorizing public snapshot of an independent source sync phase.

    Empty last_success means no *attested* success, not that no previous
    attempt ever succeeded. The snapshot never grants acquisition rights.
    """
    phase: str
    status: str = "idle"
    last_attempt: str = ""
    last_success: str = ""


@dataclass(frozen=True, slots=True)
class SyncSourceRef:
    source_id: str
    identity_id: str
    provider_id: str
    account_id: str
    label: str
    connected: bool = True
    sync_capable: bool = True
    enabled: bool = True
    last_attempt: str = ""
    last_success: str = ""
    status: str = "idle"
    error: str = ""
    # Source-level declarations, separate from the default metadata/rights
    # synchronization operation. Never imply an acquisition is automatic.
    sync_capabilities: tuple[str, ...] = ()
    phase_states: tuple[SyncPhaseState, ...] = ()


@dataclass(frozen=True, slots=True)
class AdeptProfileRef:
    """Stable public ADEPT profile reference.

    profile_id is the durable Core identity; name is the current CLI name.
    """
    profile_id: str
    name: str
    complete: bool = False
    fetch_capable: bool = False
    acsm_fetch_ready: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeAcquisitionCapabilities:
    """Bounded public snapshot of runtime acquisition capabilities."""
    provider_id: str
    available: bool = False
    compatible: bool = False
    operational: bool = False
    contract_version: int | None = None
    modes: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()
    software_version: str = ""


@dataclass(frozen=True, slots=True)
class MaintenanceToolInfo:
    id: str
    title: str
    description: str = ""
    icon_name: str = "applications-system-symbolic"
    scope: Literal["global", "identity", "library", "artifact", "storage.target"] = "global"
    danger_level: Literal["read-only", "repair", "destructive"] = "read-only"
    supports_dry_run: bool = True
    cancellable: bool = True


@dataclass(frozen=True, slots=True)
class MaintenanceContext:
    scope: str = "global"
    identity_id: str | None = None
    artifact: ArtifactRef | None = None
    storage_target_id: str | None = None
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MaintenancePlan:
    tool_id: str
    summary: str
    changes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    facts: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    ok: bool
    changed: int = 0
    summary: str = ""
    warnings: tuple[str, ...] = ()
    facts: Mapping[str, object] = field(default_factory=dict)
    # Explicit user-facing local detail; never render opaque facts/capabilities.
    details: tuple[str, ...] = ()


@runtime_checkable
class MaintenanceTool(Protocol):
    def describe(self) -> MaintenanceToolInfo: ...
    def plan(self, context: MaintenanceContext) -> MaintenancePlan: ...
    def run(self, plan: MaintenancePlan, control: TaskControl) -> MaintenanceResult: ...


@dataclass(frozen=True, slots=True)
class TransformCapability:
    id: str
    input_formats: tuple[ArtifactFormat, ...]
    output_formats: tuple[ArtifactFormat, ...]
    preserves_original: bool = True
    supports_profiles: bool = False
    supports_plan: bool = False
    supports_batch: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class TransformRequest:
    source: ArtifactRef
    capability_id: str
    profile_plugin_id: str | None = None
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TransformPlan:
    output_format: ArtifactFormat
    proposed_filename: str
    warnings: tuple[str, ...] = ()
    facts: Mapping[str, object] = field(default_factory=dict)
    estimated_size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class TransformResult:
    output_format: ArtifactFormat
    proposed_filename: str
    sha256: str
    size_bytes: int
    facts: Mapping[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@runtime_checkable
class ArtifactInputLease(Protocol):
    @property
    def path(self) -> Path: ...
    @property
    def artifact(self) -> ArtifactRef: ...


@runtime_checkable
class ArtifactOutputLease(Protocol):
    @property
    def path(self) -> Path: ...
    def suggested_suffix(self) -> str: ...


@runtime_checkable
class TaskControl(Protocol):
    def cancelled(self) -> bool: ...
    def check_cancelled(self) -> None: ...
    def report(self, *, current: int | None = None, total: int | None = None, message: str = "") -> None: ...


@runtime_checkable
class TaskHandle(Protocol):
    @property
    def control(self) -> TaskControl: ...
    def cancel(self) -> None: ...
    def done(self) -> bool: ...
    def result(self, timeout: float | None = None) -> Any: ...
    def add_done_callback(self, callback: Callable[["TaskHandle"], None]) -> None: ...


@runtime_checkable
class TransportService(Protocol):
    def matches(self, source: Mapping[str, Any]) -> bool: ...
    def fetch(self, source: Mapping[str, Any], destination: Path) -> Path: ...


@runtime_checkable
class CatalogServiceFactory(Protocol):
    """Technical catalogue dependency, not a user-configurable library provider."""
    def create(self, **options: Any) -> object: ...


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    check_id: str
    component: str
    status: Literal["ok", "warning", "error", "unknown"] = "ok"
    message: str = ""
    detail: Mapping[str, object] = field(default_factory=dict)
    scope: str = "plugin"


@runtime_checkable
class DiagnosticsCheckProvider(Protocol):
    def checks(self) -> tuple[HealthCheckResult, ...]: ...


@runtime_checkable
class DocumentTransformer(Protocol):
    def capabilities(self) -> tuple[TransformCapability, ...]: ...
    def plan(self, request: TransformRequest, source: ArtifactInputLease, control: TaskControl | None = None) -> TransformPlan: ...
    def transform(self, request: TransformRequest, source: ArtifactInputLease, destination: ArtifactOutputLease, control: TaskControl) -> TransformResult: ...


@dataclass(frozen=True, slots=True)
class UiActionContext:
    target: str
    route: str = ""
    book: BookRef | None = None
    artifact: ArtifactRef | None = None
    selection: tuple[BookRef, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    # Origin profile captured by the host; empty means no active identity.
    # Trailing default preserves existing positional plugin constructors.
    identity_id: str = ""


@runtime_checkable
class UiBookListHost(Protocol):
    def selected_books(self) -> tuple[BookRef, ...]: ...
    def refresh_books(self) -> None: ...


@runtime_checkable
class UiBookDetailHost(Protocol):
    def current_book(self) -> BookRef | None: ...
    def detail_sections(self) -> tuple[DetailSection, ...]: ...
    def refresh_detail(self) -> None: ...


@runtime_checkable
class UiFacetHost(Protocol):
    def facets(self) -> tuple[FacetTag, ...]: ...
    def set_facet(self, facet_id: str, value: str | None) -> None: ...


@dataclass(frozen=True, slots=True)
class UiActionContribution:
    id: str
    title: str
    callback: Callable[["UiPageHost"], object]
    icon_name: str = "applications-system-symbolic"
    global_action: bool = False
    target: str = "page"
    visible_when: Callable[["UiActionContext"], bool] | None = None
    enabled_when: Callable[["UiActionContext"], bool] | None = None
    cancellable: bool = False
    progress: bool = False
    result_kind: str = "none"


@dataclass(frozen=True, slots=True)
class UiPageContribution:
    """Declarative navigation contribution returned by a ``ui.page`` plugin.

    ``factory`` receives a narrow UiPageHost supplied by the shell and must return
    a GTK widget at runtime.  Keeping GTK out of this dataclass lets manifests and
    dependency tests stay headless.
    """
    route: str
    title: str
    icon_name: str = "applications-system-symbolic"
    order: int = 100
    factory: Callable[["UiPageHost"], object] | None = None
    group: str = "usage"
    actions: tuple[UiActionContribution, ...] = ()
    priority: int = 0


@runtime_checkable
class UiPageHost(Protocol):
    def toast(self, message: str, timeout: int = 3) -> None: ...
    def navigate(self, route: str) -> None: ...
    def plugin_statuses(self) -> list[Mapping[str, Any]]: ...


@runtime_checkable
class UiPerformanceHost(UiPageHost, Protocol):
    """Optional local Core performance observer for technical UI pages.

    Reading never starts capture. Mutations require an exact boolean and an
    active host lifetime; navigation preserves an explicitly started capture.
    Reports use cifereca.performance.phases/1, already bounded/redacted by Core.
    Export remains an explicit local file action owned by the calling page.
    """
    def performance_report(self) -> Mapping[str, Any]: ...
    def set_performance_capture(self, enabled: bool) -> Mapping[str, Any]: ...


@runtime_checkable
class UiActionHost(UiPageHost, Protocol):
    """Host used while executing contextual UI actions."""
    def action_context(self) -> UiActionContext: ...
    def refresh_context(self) -> None: ...


@runtime_checkable
class UiSearchNavigationHost(UiPageHost, Protocol):
    """Optional book-value search route.

    Online requires explicit user activation. The host never silently switches
    identity or inherits stale provider/type filters from a previous query.
    Explicit providers must be currently searchable; no credential is passed.
    """
    def search_books(
        self, query: str, *, field: str = "title",
        online: bool = False, identity_id: str | None = None,
        providers: tuple[str, ...] = (),
    ) -> bool: ...


@runtime_checkable
class UiLoadedSearchNoticeHost(UiPageHost, Protocol):
    """Optional exact-match navigation to a notice ALREADY loaded by Search.

    Provider+credential+record+docbase+collection+active identity must
    identify one visible Core-owned Search result. No notice, capability,
    entitlement, borrow or download data is accepted from the plugin.
    Never triggers a fresh Search request or changes identity. A user click
    selecting the row uses the normal Search detail and holdings behavior.
    Returns False on stale, missing, disabled, hidden or ambiguous notices.
    An absent optional source_collection is passed as "" and matches only
    a row whose collection is absent or empty, never an arbitrary collection.
    """

    def can_open_loaded_search_notice(
        self, *, provider: str, credential: str, record_id: str,
        docbase: str, source_collection: str, identity_id: str,
    ) -> bool: ...

    def open_loaded_search_notice(
        self, *, provider: str, credential: str, record_id: str,
        docbase: str, source_collection: str, identity_id: str,
    ) -> bool: ...


@runtime_checkable
class UiKnownSearchNoticeHost(UiPageHost, Protocol):
    """Optional identity/edition-scoped navigation to a known SQLite notice.

    Recommandations passes only complete original supplier coordinates.
    The Core requires a fresh v2 mirror proof and a connected exact session;
    historical/ambiguous cache rows cannot be selected. A user click opens
    one local Search row; it NEVER performs network I/O, confers a right or
    uses any plugin-provided permission. An independent explicit Search click
    may fetch live holdings through the existing normal action controller.
    An absent source_collection is the exact empty string, not a wildcard.
    """

    def can_open_known_search_notice(
        self, *, provider: str, credential: str, record_id: str,
        docbase: str, source_collection: str, identity_id: str,
    ) -> bool: ...

    def open_known_search_notice(
        self, *, provider: str, credential: str, record_id: str,
        docbase: str, source_collection: str, identity_id: str,
    ) -> bool: ...


@runtime_checkable
class UiBookNavigationHost(UiPageHost, Protocol):
    """Optional public navigation to a book in the active identity's Library.

    Returns False when the book is absent, ambiguous or not in the currently
    selected identity; it never searches another user's library implicitly.
    """
    def open_book_detail(self, book: BookRef, *, identity_id: str | None = None) -> bool: ...


@runtime_checkable
class UiPluginManagementHost(UiPageHost, Protocol):
    def open_plugin_settings(self, plugin_id: str) -> None: ...



@runtime_checkable
class UiIdentityHost(UiPageHost, Protocol):
    """Optional identity brokers supported by Core 0.9.15 and later."""
    def identity_overview(self) -> list[dict[str, Any]]: ...
    def open_identity_library(self, identity_id: str) -> None: ...
    def synchronize(self, *, deep: bool = False, control: Any = None) -> None: ...
    def subscribe_identity_overview(self, callback: Callable[[], None]) -> Callable[[], None]: ...


@runtime_checkable
class UiIdentityViewHost(UiIdentityHost, Protocol):
    """Optional filtered navigation extension, Core 0.9.33 and later."""
    def supports_identity_view_filters(self) -> bool: ...
    def open_identity_view(self, identity_id: str, *, route: str, providers: tuple[str, ...] = ()) -> None: ...


@runtime_checkable
class UiBookMasterDetailView(Protocol):
    """Public façade over Cifereca's standard virtualized book master/detail UI."""
    @property
    def widget(self) -> object: ...
    def append_toolbar(self, widget: object) -> None: ...
    def show_rows(self, rows: tuple[UiBookListEntry, ...], total_count: int | None = None) -> None: ...
    def set_list_state(self, title: str, description: str = "", icon_name: str = "system-search-symbolic") -> None: ...
    def connect_selected(self, callback: Callable[[UiBookListEntry | None], None]) -> None: ...
    def show_detail(self, detail: UiBookDetailPresentation) -> None: ...
    def empty_detail(self, title: str = "Aucun document sélectionné", description: str = "Sélectionnez un document pour afficher ses détails.") -> None: ...


@runtime_checkable
class UiMasterDetailHost(UiPageHost, Protocol):
    """Optional standard book master/detail factory, Core 0.9.44+."""
    def build_book_master_detail(
        self, *, title: str, subtitle: str = "",
        empty_title: str = "Aucun document",
        empty_desc: str = "Aucun document à afficher.",
    ) -> UiBookMasterDetailView: ...


@runtime_checkable
class UiPageActionHost(UiPageHost, Protocol):
    """Optional standard action-bar factory, Core 0.9.33 and later."""
    def build_action_bar(self, actions: tuple[UiActionContribution, ...]) -> object: ...


@runtime_checkable
class UiTitlebarProvider(Protocol):
    def build(self, host: UiPageHost) -> object: ...
    def selected_actions(self) -> list[str]: ...


@runtime_checkable
class UiPageProvider(Protocol):
    def pages(self) -> list[UiPageContribution]: ...


@runtime_checkable
class WidgetCatalog(Protocol):
    def describe(self, widget_id: str) -> Mapping[str, Any] | None: ...


@runtime_checkable
class PluginLifecycle(Protocol):
    def activate(self, context: "PluginContext") -> object | None: ...
    def deactivate(self) -> None: ...


# Imported lazily/type-only in practice; keeping the name here documents the public contract.
class PluginContext(Protocol):
    plugin_id: str
    package_id: str
    directories: PluginDirectories
    config: Mapping[str, Any]

    def service(self, plugin_id: str) -> object | None: ...
    def services(self, category_id: str, *, contract: str = "", version: str = "", capabilities: tuple[str, ...] = ()) -> tuple[ResolvedPluginService, ...]: ...
    def select_service(self, category_id: str, *, request: object | None = None, contract: str = "", version: str = "", capabilities: tuple[str, ...] = ()) -> ResolvedPluginService | None: ...
    def subscribe_services(self, category_id: str, callback: Callable[[], None]) -> Callable[[], None]: ...
    def broker(self, broker_id: str) -> object | None: ...
    def report_diagnostic(self, message: str, status: str = "ok", *, detail: Mapping[str, Any] | None = None, level: str = "detailed") -> None: ...
    @property
    def logging_level(self) -> int: ...
    def configuration_ids(self) -> list[str]: ...
    def configuration(self, scope_id: str | None = None) -> Mapping[str, Any]: ...


__all__ = [
    "PluginState", "ResolutionMode", "InstantiationMode", "ContractRequirementMode", "CategoryDefinition", "Dependency", "ContractRequirement",
    "SystemRequirement", "PluginDescriptor", "RequirementStatus", "PluginStatus",
    "PluginDirectories", "AcquisitionOffer", "AcquisitionPreferences", "AcquisitionOfferSet", "normalize_acquisition_format", "acquisition_offer_formats", "acquisition_offer_preferred_format", "acquisition_offers_metadata", "ArtifactInput", "DrmMutationRequest", "DrmMutationResult", "DrmRuntime", "StorageTarget", "StorageResult",
    "ResolvedPluginService", "Provenance", "YouthAgeKind", "YouthAgeRange", "BookRef", "BookReferenceRecommendationService", "DetailField", "DetailSection", "CachedBookDetail", "BookDetailCacheEnricher", "BookDetailCacheNotifier", "FacetTag", "UiBookListEntry", "UiBookDetailAction", "DocumentLink", "UiBookDetailPresentation",
    "ArtifactFormat", "ArtifactRef", "ArtifactVariant", "ArtifactPlacementRef", "ArtifactPossessionRef", "LogicalBookArtifacts",
    "LibraryMutationOperation", "LibraryMutationRequest", "LibraryMutationPlan", "LibraryMutationResult",
    "ArtifactRouteRequest", "ArtifactRouteTarget", "ArtifactRoutePlan", "ArtifactTransferResult", "CatalogSearchBatch", "CatalogSearchSummary", "CacheNamespaceInfo", "CachePurgeResult", "ActionStatistics", "SyncRequest", "SyncResult", "AccountRef", "SyncSourceRef",
    "MaintenanceToolInfo", "MaintenanceContext", "MaintenancePlan", "MaintenanceResult", "MaintenanceTool",
    "TransformCapability", "TransformRequest", "TransformPlan", "TransformResult",
    "ArtifactInputLease", "ArtifactOutputLease", "TaskControl", "TaskHandle", "TransportService", "HealthCheckResult", "DiagnosticsCheckProvider", "DocumentTransformer",
    "UiActionContext", "UiBookListHost", "UiBookDetailHost", "UiFacetHost",
    "UiActionContribution", "UiPageContribution", "UiPageHost", "UiActionHost", "UiBookNavigationHost", "UiLoadedSearchNoticeHost", "UiPluginManagementHost", "UiIdentityHost", "UiIdentityViewHost", "UiMasterDetailHost", "UiBookMasterDetailView", "UiPageActionHost", "UiPageProvider", "UiTitlebarProvider", "WidgetCatalog",
    "PluginLifecycle", "PluginContext",
]



@dataclass(frozen=True, slots=True)
class CatalogueSourceCapability:
    """#2385: read-only catalog vs personal-inventory projection, contract v1.

    A search-enabled provider is not necessarily a synchronizable catalogue.
    This type contains only closed technical facts, never labels, accounts,
    credentials, query URLs, entitlements or user-owned material.
    """
    provider_id: str
    identity_scope: str  # public | authenticated
    catalog_search_supported: bool
    catalog_search_available: bool
    catalog_refresh: str  # unsupported until a separate verified contract exists
    personal_inventory_supported: bool
    personal_inventory_available: bool
    acquisition: str = "separate_explicit"
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class ReleaseInstallationSnapshot:
    """#1005: safe read-only view, no local paths or plugin credentials."""
    state: str  # installed | not_installed | unknown
    version: str = ""
    schema_version: int = 1
    can_apply: bool = False


@dataclass(frozen=True, slots=True)
class ReleaseUpdatePreview:
    """#1005: untrusted GitHub release announcement is never an apply ticket."""
    state: str  # blocked
    reason_code: str  # closed vocabulary
    version: str = ""
    schema_version: int = 1
    dry_run: bool = True
    provenance_verified: bool = False
    bytes_sha256_verified: bool = False
    can_apply: bool = False
