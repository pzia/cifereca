"""Provider-neutral, single-click public metadata enrichment boundary.

This never grants acquisition, learns ownership, persists source state or
performs an implicit provider scan. The provider owns its detail method;
Core pins the selected identity/source and permits descriptive fields only.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from ..models import SearchResult
from ..plugin_api import ProviderCapabilityError
from .source_context import source_context_epoch

_ERROR = "explicit_public_enrichment_unavailable"


def _scope(service, item, expected_identity_id):
    if (type(item) is not SearchResult
            or type(expected_identity_id) is not str
            or not expected_identity_id
            or getattr(getattr(service, "current_identity", None),
                       "identity_id", None) != expected_identity_id
            or type(item.source_provider) is not str or not item.source_provider
            or type(item.source_credential) is not str or not item.source_credential
            or item.download_url is not None):
        raise ProviderCapabilityError(_ERROR)
    if (item.source_credential == "__public__"
            and item.source_collection == "public_catalog"
            and item.owned is False and item.downloadable is False):
        return "public"
    if (item.source_credential == "__public__"
            or item.source_collection != "library" or item.owned is not True):
        raise ProviderCapabilityError(_ERROR)
    accounts = service.accounts_for_identity(expected_identity_id)
    matches = [
        account for account in accounts
        if account.provider_id == item.source_provider
        and account.credential_id == item.source_credential
    ]
    if len(matches) != 1:
        raise ProviderCapabilityError(_ERROR)
    return "private"


def enrich_provider_detail_once(service, item: SearchResult, *,
                                expected_identity_id: str) -> SearchResult:
    """Invoke exactly one scoped provider detail method, fail-closed on rebind."""
    try:
        epoch = source_context_epoch(service)
        kind = _scope(service, item, expected_identity_id)
        session = service._session_for_source(
            item.source_provider, item.source_credential,
        )
        method = ("enrich_public_detail" if kind == "public"
                  else "enrich_owned_public_detail")
        action = getattr(session, method, None)
        if not callable(action):
            raise ValueError("method_unavailable")
        result = action(item)
        if (source_context_epoch(service) != epoch
                or _scope(service, item, expected_identity_id) != kind
                or type(result) is not SearchResult
                or any(getattr(result, key) != getattr(item, key)
                       for key in (
                           "record_id", "id", "source_provider",
                           "source_credential", "source_collection",
                           "owned", "downloadable", "download_url", "format",
                       ))):
            raise ValueError("scope_or_metadata_changed")
        return result
    except Exception:
        raise ProviderCapabilityError(_ERROR) from None


def can_enrich_provider_detail_result(item: object, *, private_provider_id: str | None = None, public_provider_id: str | None = None) -> bool:
    """UI visibility hint only; provider will re-attest all source evidence."""
    if not isinstance(item, SearchResult):
        return False
    url = getattr(item, "friendly_url", None)
    # This is only a UI hint. Core must not encode a supplier hostname:
    # the provider independently attests the product URL before any GET.
    # Keep a generic syntactic HTTPS gate to avoid advertising malformed links.
    if (type(url) is not str or not url or len(url) > 16384
            or any(ord(char) < 33 or ord(char) == 127 or ord(char) == 92 for char in url)):
        return False
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443)
                or not parsed.path or parsed.path == "/"
                or parsed.query or parsed.fragment):
            return False
    except ValueError:
        return False
    private_shape = (item.owned is True
                     and item.source_collection == "library"
                     and item.source_credential not in {"", "__public__"}
                     and item.download_url is None)
    public_shape = (item.source_credential == "__public__"
                    and item.source_collection == "public_catalog"
                    and item.owned is False and item.downloadable is False
                    and item.download_url is None)
    return ((private_provider_id is None or item.source_provider == private_provider_id)
            and private_shape) or (
            (public_provider_id is None or item.source_provider == public_provider_id)
            and public_shape)

