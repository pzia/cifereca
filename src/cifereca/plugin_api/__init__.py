"""Stable public API for cifereca plugins.

External plugins should import from this package only.  Internal modules such as
``cifereca.services`` and ``cifereca.storage`` are not part of the plugin contract.
"""

from cifereca.http_trace import HTTPX_TRANSPORT, TraceHTTPTransport, TraceAsyncHTTPTransport, TraceHTTPClient, TraceAsyncHTTPClient
from .context import PluginContext
from cifereca.install_paths import prefer_downloads_for_save
from .provider_collection import (ProviderCollection, ProviderCollectionSnapshot, VirtualProviderEntry, ProviderCollectionValidationError, validate_provider_collection_snapshot)
from .provider_search_stream import ProviderSearchEvent, ProviderSearchStream
from .capability_matrix import (
    CAPABILITY_MATRIX_CONTRACT, CapabilityEvidence, CapabilityMatrix,
    declared_provider_matrix,
)
from .capability_projection import project_existing_capabilities
from .provider_rights_observation import (RIGHTS_OBSERVATION_CONTRACT, ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot)
from .downloadable_resources import (
    DOWNLOADABLE_RESOURCE_CONTRACT, SourceReference, DownloadCapability,
    OptionalRightMetadata, ResourceObservation, ResourceSnapshot,
    DeliveryCandidate, from_legacy_rights_snapshot, as_legacy_rights_snapshot,
)
from .loan_inventory import (INVENTORY_CONTRACT, LoanInventoryScope, LoanBranchCoverage, LoanInventorySnapshot, legacy_loan_inventory, read_loan_inventory)
from .lending_borrower_eligibility import (
    BorrowerEligibilityStatus, BorrowerOperationEvidence,
    BorrowerEligibilityFact, assess_borrower_eligibility,
)
from .lending_actions import (
    LENDING_ACTION_CONTRACT, LendingActionOrigin, LendingActionOriginPolicy,
    LendingActionAdmission, assess_lending_action, LendingActionStatus,
    LendingActionOutcome,
)
from .errors import (AttentionRequired, PluginApiError, UserFacingError, UnsupportedOperation, AuthenticationRequired, ConfigurationRequired, TemporaryUnavailable, NotFound, Conflict, Cancelled)
from .models import (
    AcquisitionOffer, AcquisitionPreferences, AcquisitionOfferSet,
    normalize_acquisition_format, acquisition_offer_formats, acquisition_offer_preferred_format, acquisition_offers_metadata,
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
    UiActionContribution, UiPageContribution, UiPageHost, UiPerformanceHost, UiSearchNavigationHost, UiIdentityHost, UiIdentityViewHost, UiMasterDetailHost, UiBookMasterDetailView, UiBookListEntry, UiBookDetailAction, DocumentLink, UiBookDetailPresentation, UiPageActionHost, UiPageProvider, UiTitlebarProvider, WidgetCatalog,
    ResolvedPluginService, Provenance, YouthAgeKind, YouthAgeRange, BookRef, BookReferenceRecommendationService, DetailField, DetailSection, CachedBookDetail, BookDetailCacheEnricher, BookDetailCacheNotifier, FacetTag, ArtifactFormat, ArtifactRef, ArtifactVariant,
    ArtifactPlacementRef, ArtifactPossessionRef, LogicalBookArtifacts, LibraryMutationOperation, LibraryMutationRequest, LibraryMutationPlan, LibraryMutationResult,
    ArtifactRouteRequest, ArtifactRouteTarget, ArtifactRoutePlan, ArtifactTransferResult, CatalogSearchBatch, CatalogSearchSummary, CatalogueSourceCapability, ReleaseInstallationSnapshot, ReleaseUpdatePreview, CacheNamespaceInfo, CachePurgeResult, ActionStatistics, SyncRequest, SyncResult, AccountRef, SyncPhaseState, SyncSourceRef, AdeptProfileRef, RuntimeAcquisitionCapabilities,
    MaintenanceToolInfo, MaintenanceContext, MaintenancePlan, MaintenanceResult, MaintenanceTool,
    TransformCapability, TransformRequest, TransformPlan, TransformResult, ArtifactInputLease, ArtifactOutputLease, TaskControl, TaskHandle, TransportService, CatalogServiceFactory, HealthCheckResult, DiagnosticsCheckProvider, DocumentTransformer,
    UiActionContext, UiBookListHost, UiBookDetailHost, UiFacetHost, UiActionHost, UiBookNavigationHost, UiLoadedSearchNoticeHost, UiKnownSearchNoticeHost, UiPluginManagementHost,
)
from .brokers import (CatalogBroker, CatalogueSourceInventoryBroker, ReleaseUpdateReadBroker, AcquisitionBroker, SynchronizationBroker, SourceMaterializationScheduleBroker, SourceInventoryRefreshScheduleBroker, MetadataBroker, ReconciliationBroker, IdentityBroker, DiagnosticsBroker, LibraryNamingBroker, RuntimeCapabilitiesBroker, AdeptProfileBroker, AdeptLoanBroker,
    ArtifactExecutionBroker, LibraryReadBroker, LibraryReconciliationReadBroker, LibraryMaintenanceBroker, ArtifactRoutingBroker, CacheBroker, TaskBroker)
from .acquisition import (
    normalize_acquisition_offers,
    attested_offer_formats,
    rank_acquisition_offers,
    acquisition_format_projection,
)
from .version import MANIFEST_VERSION, PLUGIN_API_VERSION
from .contracts import (ContractStatus, ContractDefinition, ContractCheck, CONTRACT_REGISTRY, contract_definition, split_contract, capability_name_valid, contract_version_matches, check_service_contract)
from cifereca.language import normalize_language_tag
from cifereca.filename_normalization import sanitize_filename_component
from cifereca.descriptions import description_text, description_element
from cifereca.diagnostics import redact_text
# GTK presentation-only helper; plugins access it solely through the public API.
from cifereca.gui.diagnostics_pseudonyms import DiagnosticsPseudonyms, DISPLAY_PSEUDONYMS
from cifereca.artifact_inspection import ArtifactInspection, detect_artifact_format, inspect_artifact, inspect_artifact_content
from cifereca.artifact_publication import publish_artifact_content, publish_artifact_file
from .bundles import (
    BUNDLE_MANIFEST_NAME, BUNDLE_MANIFEST_VERSION, BundleError, BundleManifest, BundleMember,
    bundle_search_values, load_bundle_manifest, write_bundle_manifest,
)
from .acquisition_recipes import (
    BundleRecipeError, BundleTrack, BundleAcquisitionRecipe,
    normalize_audio_format, validate_audio_bundle_manifest,
)
from .youth_ages import youth_age_from_label, youth_age_matches
from .book_references import book_ref_from_row, BookAccessFact, book_access_facts
from .book_detail_cache import CachedBookDetailContribution, collect_cached_book_details, subscribe_cached_book_detail_changes
from .llm import LLMRequest, LLMResult, LLMService
from .document_links import document_links

__all__ = [
    "HTTPX_TRANSPORT", "TraceHTTPTransport", "TraceAsyncHTTPTransport", "TraceHTTPClient", "TraceAsyncHTTPClient",
    "DiagnosticsPseudonyms", "DISPLAY_PSEUDONYMS",
    "LLMRequest", "LLMResult", "LLMService",
    "ProviderCollection", "ProviderCollectionSnapshot", "VirtualProviderEntry", "ProviderCollectionValidationError", "validate_provider_collection_snapshot",
    "INVENTORY_CONTRACT", "LoanInventoryScope", "LoanBranchCoverage", "LoanInventorySnapshot", "legacy_loan_inventory", "read_loan_inventory",
    "BorrowerEligibilityStatus", "BorrowerOperationEvidence",
    "BorrowerEligibilityFact", "assess_borrower_eligibility",
    "LENDING_ACTION_CONTRACT", "LendingActionOrigin", "LendingActionOriginPolicy",
    "LendingActionAdmission", "assess_lending_action", "LendingActionStatus",
    "LendingActionOutcome",
    "ProviderSearchEvent", "ProviderSearchStream",
    "CAPABILITY_MATRIX_CONTRACT", "CapabilityEvidence", "CapabilityMatrix", "declared_provider_matrix", "project_existing_capabilities",
    "RIGHTS_OBSERVATION_CONTRACT", "ProviderRightsScope", "ProviderRightObservation", "ProviderRightsSnapshot",
    "PLUGIN_API_VERSION", "MANIFEST_VERSION", "PluginContext", "PluginApiError",
    "UserFacingError", "AttentionRequired", "UnsupportedOperation", "AuthenticationRequired", "ConfigurationRequired", "TemporaryUnavailable", "NotFound", "Conflict", "Cancelled", "AcquisitionOffer", "AcquisitionPreferences", "AcquisitionOfferSet", "normalize_acquisition_format", "acquisition_offer_formats", "acquisition_offer_preferred_format", "acquisition_offers_metadata", "ArtifactInput",
    "DrmMutationRequest", "DrmMutationResult", "DrmRuntime",
    "StorageTarget", "StorageResult", "CategoryDefinition", "Dependency", "ContractRequirement", "ContractRequirementMode",
    "PluginDescriptor", "PluginDirectories", "PluginLifecycle", "PluginState", "InstantiationMode",
    "PluginStatus", "RequirementStatus", "ResolutionMode", "SystemRequirement",
    "ResolvedPluginService", "Provenance", "YouthAgeKind", "YouthAgeRange", "BookRef", "BookReferenceRecommendationService", "DetailField", "DetailSection", "CachedBookDetail", "BookDetailCacheEnricher", "BookDetailCacheNotifier", "FacetTag",
    "ArtifactFormat", "ArtifactRef", "ArtifactVariant", "ArtifactPlacementRef", "ArtifactPossessionRef", "LogicalBookArtifacts",
    "LibraryMutationOperation", "LibraryMutationRequest", "LibraryMutationPlan", "LibraryMutationResult",
    "ArtifactRouteRequest", "ArtifactRouteTarget", "ArtifactRoutePlan", "ArtifactTransferResult", "CatalogSearchBatch", "CatalogSearchSummary", "CatalogueSourceCapability", "ReleaseInstallationSnapshot", "ReleaseUpdatePreview", "CacheNamespaceInfo", "CachePurgeResult", "ActionStatistics", "SyncRequest", "SyncResult", "AccountRef", "SyncPhaseState", "SyncSourceRef", "AdeptProfileRef", "RuntimeAcquisitionCapabilities",
    "MaintenanceToolInfo", "MaintenanceContext", "MaintenancePlan", "MaintenanceResult", "MaintenanceTool",
    "TransformCapability", "TransformRequest", "TransformPlan", "TransformResult", "ArtifactInputLease", "ArtifactOutputLease", "TaskControl", "TaskHandle", "TransportService", "CatalogServiceFactory", "HealthCheckResult", "DiagnosticsCheckProvider", "DocumentTransformer",
    "UiActionContext", "UiBookListHost", "UiBookDetailHost", "UiFacetHost", "UiActionHost", "UiBookNavigationHost", "UiLoadedSearchNoticeHost", "UiKnownSearchNoticeHost", "UiPluginManagementHost",
    "CatalogBroker", "CatalogueSourceInventoryBroker", "ReleaseUpdateReadBroker", "AcquisitionBroker", "SynchronizationBroker", "SourceMaterializationScheduleBroker", "SourceInventoryRefreshScheduleBroker", "MetadataBroker", "ReconciliationBroker", "IdentityBroker", "DiagnosticsBroker", "LibraryNamingBroker", "RuntimeCapabilitiesBroker", "AdeptProfileBroker", "AdeptLoanBroker",
    "ArtifactExecutionBroker", "LibraryReadBroker", "LibraryReconciliationReadBroker", "LibraryMaintenanceBroker", "ArtifactRoutingBroker", "CacheBroker", "TaskBroker",
    "normalize_language_tag", "sanitize_filename_component", "prefer_downloads_for_save", "description_text", "description_element", "redact_text",
    "normalize_acquisition_offers", "attested_offer_formats", "rank_acquisition_offers", "acquisition_format_projection", "ArtifactInspection", "detect_artifact_format", "inspect_artifact", "inspect_artifact_content", "publish_artifact_content", "publish_artifact_file",
    "ContractStatus", "ContractDefinition", "ContractCheck", "CONTRACT_REGISTRY", "contract_definition", "split_contract", "capability_name_valid", "contract_version_matches", "check_service_contract",
    "UiActionContribution", "UiPageContribution", "UiPageHost", "UiPerformanceHost", "UiSearchNavigationHost", "UiIdentityHost", "UiIdentityViewHost", "UiMasterDetailHost", "UiBookMasterDetailView", "UiBookListEntry", "UiBookDetailAction", "DocumentLink", "UiBookDetailPresentation", "UiPageActionHost", "UiPageProvider", "UiTitlebarProvider", "WidgetCatalog",
    "BUNDLE_MANIFEST_NAME", "BUNDLE_MANIFEST_VERSION", "BundleError", "BundleManifest", "BundleMember",
    "bundle_search_values", "load_bundle_manifest", "write_bundle_manifest",
    "BundleRecipeError", "BundleTrack", "BundleAcquisitionRecipe", "normalize_audio_format", "validate_audio_bundle_manifest", "document_links", "book_ref_from_row", "BookAccessFact", "book_access_facts", "CachedBookDetailContribution", "collect_cached_book_details", "subscribe_cached_book_detail_changes", "youth_age_from_label", "youth_age_matches",
    "Account", "Booking", "HistoryItem", "Holding", "ItemDetail", "Loan", "Medium", "SearchResult",
    "CredentialField", "ProviderCapabilities", "ProviderSearchCapabilities", "ProviderCapabilityError",
    "ProviderConfigurationError", "ProviderDescriptor", "ProviderError",
    "ProviderPlugin", "ProviderSession",
]

# Provider compatibility contract v1.  These names are deliberately exported
# through plugin_api so external provider packages never import core internals.
from cifereca.models import Account, Booking, HistoryItem, Holding, ItemDetail, Loan, Medium, SearchResult
from cifereca.providers.base import (
    CredentialField, ProviderCapabilities, ProviderSearchCapabilities, ProviderCapabilityError, ProviderConfigurationError,
    ProviderDescriptor, ProviderError, ProviderPlugin, ProviderSession,
)

