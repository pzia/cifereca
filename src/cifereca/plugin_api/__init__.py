"""Stable public API for cifereca plugins.

External plugins should import from this package only.  Internal modules such as
``cifereca.services`` and ``cifereca.storage`` are not part of the plugin contract.
"""

from .context import PluginContext
from .errors import (AttentionRequired, PluginApiError, UserFacingError, UnsupportedOperation, AuthenticationRequired, ConfigurationRequired, TemporaryUnavailable, NotFound, Conflict, Cancelled)
from .models import (
    AcquisitionOffer,
    ArtifactInput,
    CategoryDefinition,
    Dependency,
    ContractRequirement, ContractRequirementMode,
    DrmMutationRequest,
    DrmMutationResult,
    DrmRuntime,
    PluginDescriptor,
    PluginDirectories,
    PluginLifecycle,
    InstantiationMode,
    PluginState,
    PluginStatus,
    RequirementStatus,
    ResolutionMode,
    StorageResult,
    StorageTarget,
    SystemRequirement,
    UiActionContribution, UiPageContribution, UiPageHost, UiIdentityHost, UiIdentityViewHost, UiMasterDetailHost, UiBookMasterDetailView, UiBookListEntry, UiBookDetailAction, UiBookDetailPresentation, UiPageActionHost, UiPageProvider, UiTitlebarProvider, WidgetCatalog,
    ResolvedPluginService, Provenance, BookRef, DetailField, DetailSection, FacetTag, ArtifactFormat, ArtifactRef, ArtifactVariant,
    ArtifactPlacementRef, ArtifactPossessionRef, LogicalBookArtifacts, LibraryMutationOperation, LibraryMutationRequest, LibraryMutationPlan, LibraryMutationResult,
    ArtifactRouteRequest, ArtifactRouteTarget, ArtifactRoutePlan, ArtifactTransferResult, CatalogSearchBatch, CatalogSearchSummary, CacheNamespaceInfo, CachePurgeResult, ActionStatistics, SyncRequest, SyncResult, AccountRef, SyncSourceRef,
    MaintenanceToolInfo, MaintenanceContext, MaintenancePlan, MaintenanceResult, MaintenanceTool,
    TransformCapability, TransformRequest, TransformPlan, TransformResult, ArtifactInputLease, ArtifactOutputLease, TaskControl, TaskHandle, DocumentTransformer,
    UiActionContext, UiBookListHost, UiBookDetailHost, UiFacetHost, UiActionHost, UiPluginManagementHost,
)
from .brokers import (CatalogBroker, AcquisitionBroker, SynchronizationBroker, MetadataBroker, ReconciliationBroker, IdentityBroker, DiagnosticsBroker, LibraryNamingBroker,
    ArtifactExecutionBroker, LibraryReadBroker, LibraryMaintenanceBroker, ArtifactRoutingBroker, CacheBroker, TaskBroker)
from .version import MANIFEST_VERSION, PLUGIN_API_VERSION
from .contracts import (ContractStatus, ContractDefinition, ContractCheck, CONTRACT_REGISTRY, contract_definition, split_contract, capability_name_valid, contract_version_matches, check_service_contract)
from cifereca.language import normalize_language_tag
from .bundles import (
    BUNDLE_MANIFEST_NAME, BUNDLE_MANIFEST_VERSION, BundleError, BundleManifest, BundleMember,
    bundle_search_values, load_bundle_manifest, write_bundle_manifest,
)

__all__ = [
    "PLUGIN_API_VERSION", "MANIFEST_VERSION", "PluginContext", "PluginApiError",
    "UserFacingError", "AttentionRequired", "UnsupportedOperation", "AuthenticationRequired", "ConfigurationRequired", "TemporaryUnavailable", "NotFound", "Conflict", "Cancelled", "AcquisitionOffer", "ArtifactInput",
    "DrmMutationRequest", "DrmMutationResult", "DrmRuntime",
    "StorageTarget", "StorageResult", "CategoryDefinition", "Dependency", "ContractRequirement", "ContractRequirementMode",
    "PluginDescriptor", "PluginDirectories", "PluginLifecycle", "PluginState", "InstantiationMode",
    "PluginStatus", "RequirementStatus", "ResolutionMode", "SystemRequirement",
    "ResolvedPluginService", "Provenance", "BookRef", "DetailField", "DetailSection", "FacetTag",
    "ArtifactFormat", "ArtifactRef", "ArtifactVariant", "ArtifactPlacementRef", "ArtifactPossessionRef", "LogicalBookArtifacts",
    "LibraryMutationOperation", "LibraryMutationRequest", "LibraryMutationPlan", "LibraryMutationResult",
    "ArtifactRouteRequest", "ArtifactRouteTarget", "ArtifactRoutePlan", "ArtifactTransferResult", "CatalogSearchBatch", "CatalogSearchSummary", "CacheNamespaceInfo", "CachePurgeResult", "ActionStatistics", "SyncRequest", "SyncResult", "AccountRef", "SyncSourceRef",
    "MaintenanceToolInfo", "MaintenanceContext", "MaintenancePlan", "MaintenanceResult", "MaintenanceTool",
    "TransformCapability", "TransformRequest", "TransformPlan", "TransformResult", "ArtifactInputLease", "ArtifactOutputLease", "TaskControl", "TaskHandle", "DocumentTransformer",
    "UiActionContext", "UiBookListHost", "UiBookDetailHost", "UiFacetHost", "UiActionHost", "UiPluginManagementHost",
    "CatalogBroker", "AcquisitionBroker", "SynchronizationBroker", "MetadataBroker", "ReconciliationBroker", "IdentityBroker", "DiagnosticsBroker", "LibraryNamingBroker",
    "ArtifactExecutionBroker", "LibraryReadBroker", "LibraryMaintenanceBroker", "ArtifactRoutingBroker", "CacheBroker", "TaskBroker",
    "normalize_language_tag",
    "ContractStatus", "ContractDefinition", "ContractCheck", "CONTRACT_REGISTRY", "contract_definition", "split_contract", "capability_name_valid", "contract_version_matches", "check_service_contract",
    "UiActionContribution", "UiPageContribution", "UiPageHost", "UiIdentityHost", "UiIdentityViewHost", "UiMasterDetailHost", "UiBookMasterDetailView", "UiBookListEntry", "UiBookDetailAction", "UiBookDetailPresentation", "UiPageActionHost", "UiPageProvider", "UiTitlebarProvider", "WidgetCatalog",
    "BUNDLE_MANIFEST_NAME", "BUNDLE_MANIFEST_VERSION", "BundleError", "BundleManifest", "BundleMember",
    "bundle_search_values", "load_bundle_manifest", "write_bundle_manifest",
    "Account", "Booking", "HistoryItem", "Holding", "ItemDetail", "Loan", "SearchResult",
    "CredentialField", "ProviderCapabilities", "ProviderCapabilityError",
    "ProviderConfigurationError", "ProviderDescriptor", "ProviderError",
    "ProviderPlugin", "ProviderSession",
]

# Provider compatibility contract v1.  These names are deliberately exported
# through plugin_api so external provider packages never import core internals.
from cifereca.models import Account, Booking, HistoryItem, Holding, ItemDetail, Loan, SearchResult
from cifereca.providers.base import (
    CredentialField, ProviderCapabilities, ProviderCapabilityError, ProviderConfigurationError,
    ProviderDescriptor, ProviderError, ProviderPlugin, ProviderSession,
)
