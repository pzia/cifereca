"""Explicit Numilog Search click: only the canonical selected-offer executor.

The selected private book card is merely an action target. Core revalidates
one account, one attested owned entitlement and a complete guarded v2 ABI.
It never derives a download grant from public catalogue metadata or a
historical Numilog receipt, and never calls the legacy direct writer.
"""
from __future__ import annotations

from inspect import getattr_static

from ..models import SearchResult
from ..providers import ProviderConfigurationError
from .canonical_delivery_abi import has_complete_delivery_abi
from .numilog_manifest_rights import _scoped_record_id
from .numilog_manual_acquisition import require_source


_REFUSAL = "numilog_canonical_selected_delivery_required"


def obtain(service, item: SearchResult):
    """Explicit user action producing Core's already-published A→B result."""
    try:
        if (type(item) is not SearchResult
                or item.source_provider != "numilog"
                or item.source_collection != "library"
                or item.scenario != "NUMILOG_LIBRARY"
                or item.downloadable is not True
                or item.owned is not True
                or item.id != item.record_id
                or _scoped_record_id(item.record_id, item.source_credential) is None):
            raise ValueError("untrusted_item")
        identity, session, account_id = require_source(service, item, canonical_only=True)
        if (not has_complete_delivery_abi(session)
                or not callable(getattr_static(
                    session, "downloadable_selected_delivery_candidate_guarded", None
                ))):
            raise ValueError("canonical_guarded_abi_required")
        evidence = service.storage.numilog_purchase_preflight_evidence(
            identity_id=identity.identity_id,
            credential_id=item.source_credential,
            account_id=account_id, item_id=item.record_id,
        )
        rights = evidence.get("entitlements")
        if (type(rights) is not list or len(rights) != 1
                or evidence.get("acquisitions") != []
                or evidence.get("placement_conflict") is not False):
            raise ValueError("missing_or_ambiguous_right")
        right = rights[0]
        metadata = right.get("metadata")
        if (type(metadata) is not dict
                or metadata.get("source") != "numilog.authenticated_library"
                or right.get("entitlement_type") != "owned"
                or right.get("state") != "active"
                or right.get("identity_id") != identity.identity_id
                or right.get("credential_id") != item.source_credential
                or right.get("account_id") != account_id
                or right.get("item_id") != item.record_id
                or right.get("loan_id") is not None
                or right.get("expires_at") is not None):
            raise ValueError("right_not_attested")
        entitlement_id = right.get("entitlement_id")
        if type(entitlement_id) is not str or not entitlement_id:
            raise ValueError("right_id_unavailable")
        from .numilog_owned_download import _resolve, obtain as obtain_owned
        target, _selected = _resolve(
            service, entitlement_id, identity.identity_id, item.record_id,
        )
        if (target.session is not session
                or target.account_id != account_id
                or target.credential_id != item.source_credential
                or target.item_id != item.record_id):
            raise ValueError("selected_source_rebound")
        return obtain_owned(service, target)
    except Exception as exc:
        from ..plugin_api import Cancelled
        if isinstance(exc, Cancelled):
            raise
        raise ProviderConfigurationError(_REFUSAL) from None
