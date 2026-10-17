"""One-shot, user-triggered Numilog commercial detail enrichment (#1072).

No plugin imports, no automatic execution, no persistent rights or holdings
updates. This is the application's *identity boundary* before invoking the
already isolated Numilog provider method, not an edition matcher.
"""
from __future__ import annotations

from cifereca.models import SearchResult
from cifereca.plugin_api import ProviderCapabilityError


_ERROR = "Numilog : explicit_public_enrichment_unavailable"


def _scope(service, item: SearchResult, expected_identity_id: str) -> str:
    """Fail closed before HTTP when identity, account or source has changed."""
    if not isinstance(item, SearchResult):
        raise ProviderCapabilityError(_ERROR)
    if type(expected_identity_id) is not str:
        raise ProviderCapabilityError(_ERROR)
    identity = getattr(service, "current_identity", None)
    active_id = getattr(identity, "identity_id", "") if identity is not None else ""
    if active_id != expected_identity_id:
        raise ProviderCapabilityError(_ERROR)
    if item.source_provider == "numilog":
        if (not expected_identity_id or item.owned is not True
                or item.source_collection != "library"
                or type(item.source_credential) is not str
                or not item.source_credential
                or item.source_credential in {"__public__"}
                or item.download_url is not None):
            raise ProviderCapabilityError(_ERROR)
        # A private Numilog result is legal only if its exact source connection
        # belongs to the still-selected identity, never merely another account.
        accounts = service.accounts_for_identity(expected_identity_id)
        if not any(
            account.provider_id == "numilog"
            and account.credential_id == item.source_credential
            for account in accounts
        ):
            raise ProviderCapabilityError(_ERROR)
        return "private"
    if item.source_provider == "numilog-catalog":
        if (item.source_credential != "__public__"
                or item.source_collection != "public_catalog"
                or item.owned is not False
                or item.downloadable is not False
                or item.download_url is not None):
            raise ProviderCapabilityError(_ERROR)
        return "public"
    raise ProviderCapabilityError(_ERROR)


def can_enrich_numilog_result(item: object) -> bool:
    """UI visibility hint only; provider will re-attest all source evidence."""
    if not isinstance(item, SearchResult):
        return False
    if item.source_provider == "numilog":
        return (item.owned is True
                and item.source_collection == "library"
                and item.source_credential not in {"", "__public__"}
                and item.download_url is None)
    return (item.source_provider == "numilog-catalog"
            and item.source_credential == "__public__"
            and item.source_collection == "public_catalog"
            and item.owned is False and item.downloadable is False
            and item.download_url is None)


def enrich_numilog_result_once(service, item: SearchResult, *,
                               expected_identity_id: str) -> SearchResult:
    """Explicitly request public metadata for *this* book and identity.

    Provider is sole authority for purchased-link/edition evidence; Core may
    neither infer rights from the public listing nor re-enable acquisition.
    """
    kind = _scope(service, item, expected_identity_id)
    session = service._session_for_source(item.source_provider, item.source_credential)
    if kind == "private":
        action = getattr(session, "enrich_owned_public_detail", None)
    else:
        action = getattr(session, "enrich_public_detail", None)
    if not callable(action):
        raise ProviderCapabilityError(_ERROR)
    result = action(item)
    # A user can switch profile mid-flight: never surface those bytes under a
    # different identity. No automatic store/cache/holdings invalidation.
    if _scope(service, item, expected_identity_id) != kind:
        raise ProviderCapabilityError(_ERROR)
    if (not isinstance(result, SearchResult)
            or result.record_id != item.record_id
            or result.source_provider != item.source_provider
            or result.source_credential != item.source_credential
            or result.source_collection != item.source_collection
            or result.owned is not item.owned
            or result.downloadable is not item.downloadable
            or result.download_url != item.download_url
            or result.format != item.format):
        raise ProviderCapabilityError(_ERROR)
    return result
