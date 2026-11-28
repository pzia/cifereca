"""Provider-neutral, single-click public metadata enrichment boundary.

This never grants acquisition, learns ownership, persists source state or
performs an implicit provider scan. The provider owns its detail method;
Core pins the selected identity/source and permits descriptive fields only.
"""
from __future__ import annotations

from copy import deepcopy
from threading import Lock
from urllib.parse import urlsplit

from ..models import SearchResult
from ..plugin_api import ProviderCapabilityError
from .source_context import source_context_epoch

_ERROR = "explicit_public_enrichment_unavailable"


def _scope(service, item, expected_identity_id, selected_identity_id):
    if (type(item) is not SearchResult
            or type(expected_identity_id) is not str
            or not expected_identity_id
            or type(selected_identity_id) is not str
            or selected_identity_id != expected_identity_id
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


def admit_provider_detail_enrichment(service, item: SearchResult, *,
                                    identity_id: str, user_confirmed: bool = False):
    """Explicit, source-pinned read for trusted Core callers; no supplier I/O at admission.

    The old synchronous worker below remains a restrictive compatibility
    surface. Here only source lifetime and durable borrower authority, not
    the currently displayed reader, determine continuation.
    """
    from .private_inventory_binding import private_inventory_binding_current
    from . import provider_sessions

    try:
        if user_confirmed is not True:
            raise ValueError("confirmation_required")
        identity = service.identity_by_id(identity_id)
        if identity is None or identity.identity_id != identity_id:
            raise ValueError("identity_unknown")
        epoch = source_context_epoch(service)
        row = deepcopy(item)
        kind = _scope(service, row, identity_id, identity_id)
        if not can_enrich_provider_detail_result(row):
            raise ValueError("invalid_detail")
        provider, credential = row.source_provider, row.source_credential
        key = service._session_key(provider, credential)
        registry = service._sessions
        session = registry.get(key)
        storage = service.storage
        if session is None:
            raise ValueError("session_unavailable")
        account, scope = None, None
        if kind == "private":
            matches = [a for a in service.accounts_for_identity(identity_id)
                       if a.provider_id == provider and a.credential_id == credential]
            if len(matches) != 1:
                raise ValueError("ambiguous_account")
            account = matches[0]
            scope = (account.identity_id, account.provider_id,
                     account.credential_id, account.unique_id)
            observed = getattr(row, "source_account_id", None)
            if scope[0] != identity_id or (observed is not None and observed != scope[3]):
                raise ValueError("account_mismatch")

        def memory_current():
            return (
                service.storage is storage
                and service.identity_by_id(identity_id) is identity
                and source_context_epoch(service) == epoch
                and service._sessions is registry
                and registry.get(key) is session
                and getattr(session, "provider_id", None) == provider
                and getattr(session, "credential_id", None) == credential
                and getattr(session, "connected", None) is True
                and getattr(session, "_closed", False) is False
                and (account is None or (
                    (account.identity_id, account.provider_id, account.credential_id,
                     account.unique_id) == scope
                    and any(a is account for a in service.accounts_for_identity(identity_id))
                ))
            )

        if not memory_current():
            raise ValueError("scope_changed")
        method = ("enrich_public_detail" if kind == "public"
                  else "enrich_owned_public_detail")
        action = getattr(session, method, None)
        if not callable(action):
            raise ValueError("method_unavailable")
    except Exception:
        raise ProviderCapabilityError(_ERROR) from None

    lock, used = Lock(), False

    def current():
        if (not memory_current()
                or provider_sessions._session_for_source(
                    service, provider, credential, identity_id=identity_id,
                ) is not session
                or _scope(service, row, identity_id, identity_id) != kind
                or (account is not None and not private_inventory_binding_current(
                    service, account, session, scope=scope, epoch=epoch,
                    require_source_enabled=False,
                ))
                or not memory_current()):
            raise ValueError("scope_changed")

    def work():
        nonlocal used
        with lock:
            if used:
                raise ProviderCapabilityError(_ERROR)
            used = True
        try:
            current()
            # Pass the provider a separate DTO; comparison always uses the
            # admitted snapshot, even if the plugin mutates its argument.
            from .provider_health_search import observe_detail
            def accept(result):
                current()
                if not _preserves_detail_scope(row, result):
                    raise ValueError("scope_or_metadata_changed")
            return observe_detail(service, session, identity,
                                  lambda: action(deepcopy(row)), row, accept)
        except Exception:
            raise ProviderCapabilityError(_ERROR) from None

    return work


def _preserves_detail_scope(item, result):
    return type(result) is SearchResult and all(
        getattr(result, key) == getattr(item, key)
        for key in (
            "record_id", "id", "source_provider", "source_credential",
            "source_collection", "source_account_id",
            "owned", "downloadable", "download_url", "format",
        )
    )


def enrich_provider_detail_once(
    service, item: SearchResult, *, expected_identity_id: str,
    selected_identity_id: str | None = None,
    expected_context_epoch: int | None = None,
) -> SearchResult:
    """Invoke exactly one scoped provider detail method, fail-closed on rebind."""
    # A typed identity snapshot must come from the synchronous facade, not
    # from mutable GTK navigation inside this headless supplier worker.
    def context_current() -> bool:
        return (
            type(expected_context_epoch) is int and expected_context_epoch >= 0
            and getattr(service, "_account_context_epoch", None)
                == expected_context_epoch
        )

    try:
        if not context_current():
            raise ValueError("identity_context_unavailable")
        epoch = source_context_epoch(service)
        kind = _scope(service, item, expected_identity_id, selected_identity_id)
        session = service._session_for_source(
            item.source_provider, item.source_credential,
        )
        method = ("enrich_public_detail" if kind == "public"
                  else "enrich_owned_public_detail")
        action = getattr(session, method, None)
        if not callable(action):
            raise ValueError("method_unavailable")
        from .provider_health_search import observe_detail
        def accept(result):
            if (not context_current()
                    or source_context_epoch(service) != epoch
                    or _scope(
                        service, item, expected_identity_id, selected_identity_id,
                    ) != kind
                    or not _preserves_detail_scope(item, result)):
                raise ValueError("scope_or_metadata_changed")
        # Use the admitted identity, never mutable navigation in the worker.
        from types import SimpleNamespace
        identity = SimpleNamespace(identity_id=expected_identity_id)
        return observe_detail(service, session, identity, lambda: action(item), item, accept)
    except Exception:
        raise ProviderCapabilityError(_ERROR) from None


def can_enrich_provider_detail_result(item: object) -> bool:
    """Provider-neutral, passive UI hint; the session re-attests before GET.

    This checks only the source record shape. The GTK caller additionally
    requires an installed session exposing the matching enrichment ABI.
    """
    if not isinstance(item, SearchResult):
        return False
    url = getattr(item, "friendly_url", None)
    if (type(url) is not str or not url or len(url) > 16384
            or any(ord(char) < 33 or ord(char) in (92, 127) for char in url)):
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
    if (item.source_credential == "__public__"
            and item.source_collection == "public_catalog"):
        return (item.owned is False and item.downloadable is False
                and item.download_url is None)
    return (item.owned is True and item.source_collection == "library"
            and type(item.source_credential) is str
            and item.source_credential not in ("", "__public__")
            and item.download_url is None)
