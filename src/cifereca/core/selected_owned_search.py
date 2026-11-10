"""Provider-neutral explicit Search action for an exact persisted owned right.

A remote book card and a provider hint are not a download grant. Select one
live, account-scoped persisted entitlement and delegate to the same fully
revalidated owned-Library target/executor used for an explicit click. No
provider-specific API, URL, legacy receipt or automatic Sync consent.
"""
from __future__ import annotations

from ..models import SearchResult
from ..providers import ProviderConfigurationError


def obtain_selected_owned(service, item: SearchResult):
    """Produce a canonical materialization result for one owned source."""
    try:
        if (type(item) is not SearchResult
                or type(item.source_provider) is not str
                or not item.source_provider
                or type(item.source_credential) is not str
                or not item.source_credential
                or type(item.record_id) is not str or not item.record_id
                or item.id != item.record_id
                or item.source_collection != "library"
                or item.owned is not True
                or item.downloadable is not True
                or type(item.metadata) is not dict
                or item.metadata.get("search_provenance") == "known_catalog_stale"):
            raise ValueError("explicit_source_unproven")
        identity = getattr(service.current_identity, "identity_id", None)
        if type(identity) is not str or not identity:
            raise ValueError("explicit_identity_missing")
        accounts = [
            account for account in service.accounts
            if account.provider_id == item.source_provider
            and account.credential_id == item.source_credential
            and account.identity_id == identity
        ]
        if len(accounts) != 1:
            raise ValueError("explicit_account_ambiguous")
        account = accounts[0]
        if not service.storage.provider_scope_account_bound(
            identity_id=identity, provider_id=item.source_provider,
            credential_id=item.source_credential, account_id=account.unique_id,
        ):
            raise ValueError("explicit_account_detached")
        rights = [
            right for right in service.storage.entitlements(
                provider_id=item.source_provider, state="active",
            )
            if right.get("identity_id") == identity
            and right.get("credential_id") == item.source_credential
            and right.get("account_id") == account.unique_id
            and right.get("item_id") == item.record_id
            and right.get("entitlement_type") == "owned"
            and right.get("loan_id") is None
            and right.get("expires_at") is None
        ]
        if len(rights) != 1:
            raise ValueError("explicit_owned_right_ambiguous")
        entitlement_id = rights[0].get("entitlement_id")
        if type(entitlement_id) is not str or not entitlement_id:
            raise ValueError("explicit_owned_right_unattested")
        entry = {
            "local": False, "local_path": None, "placement_id": None,
            "possessions": [{
                "identity_id": identity, "provider_id": item.source_provider,
                "kind": "owned", "state": "active", "item_id": item.record_id,
                "entitlement_id": entitlement_id,
            }],
        }
        # The existing owned action performs its own anti-ambiguity, local
        # receipt check, session and entitlement admission before and inside
        # the canonical acquisition lock. A recovery-only action does not
        # authorize new provider bytes.
        from .owned_library_download import (
            owned_library_download_target, obtain_owned_library_item,
        )
        target = owned_library_download_target(service, entry)
        if (target is None
                or getattr(target, "provider_id", None) != item.source_provider
                or getattr(target, "identity_id", None) != identity
                or getattr(target, "item_id", None) != item.record_id
                or getattr(target, "operation", None) == "resume_index"):
            raise ValueError("explicit_canonical_target_unavailable")
        return obtain_owned_library_item(service, target)
    except Exception as exc:
        from ..plugin_api import Cancelled
        if isinstance(exc, Cancelled):
            raise
        raise ProviderConfigurationError("canonical_owned_action_unavailable") from None
