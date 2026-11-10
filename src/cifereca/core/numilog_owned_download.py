"""One exact owned Numilog purchase from local Library/Search, on explicit click.

A local right only proposes a target. The locked provider must still resolve
that exact credential/lid/variant in its fresh authenticated library before
following delivery links. Rendering does no HTTP or filesystem mutation.
"""
from dataclasses import dataclass, field
from typing import ClassVar

from ..models import SearchResult
from ..plugin_api import Cancelled
from .numilog_manifest_rights import _scoped_record_id
from .numilog_manual_acquisition import require_source
from .numilog_local_preview import _identity_root
from .owned_library_download import OwnedDownloadRejected, _require

_ATTESTED_OWNED_RIGHT_SOURCE = "numilog.authenticated_library"


@dataclass(frozen=True, repr=False)
class NumilogOwnedDownloadTarget:
    provider_id: ClassVar[str] = 'numilog'
    branch: ClassVar[str] = 'meslivres'
    entitlement_id: str
    identity_id: str
    source_id: str
    credential_id: str
    account_id: str
    item_id: str
    configuration: tuple
    session_id: int
    session: object = field(compare=False, repr=False)


def _resolve(service, entitlement_id, identity_id, item_id):
    from ..brokers import SynchronizationBrokerImpl
    _require(getattr(service.current_identity, 'identity_id', None) == identity_id)
    _require(type(entitlement_id) is str and bool(entitlement_id))
    accounts = [a for a in service.accounts if a.provider_id == 'numilog'
                and a.identity_id == identity_id
                and _scoped_record_id(item_id, a.credential_id) is not None]
    _require(len(accounts) == 1)
    account = accounts[0]
    roots = _identity_root(service, identity_id)
    _require(roots is not None and not service.storage.metadata_get('identity-directory-migration-v1'))
    evidence = service.storage.numilog_purchase_preflight_evidence(
        identity_id=identity_id, credential_id=account.credential_id,
        account_id=account.unique_id, item_id=item_id,
    )
    rights, acquisitions = evidence.get('entitlements'), evidence.get('acquisitions')
    _require(type(rights) is list and len(rights) == 1 and acquisitions == [])
    right = rights[0]
    metadata = right.get('metadata')
    _require(type(metadata) is dict and metadata.get('source') == _ATTESTED_OWNED_RIGHT_SOURCE
             and right.get('entitlement_id') == entitlement_id
             and right.get('identity_id') == identity_id
             and right.get('credential_id') == account.credential_id
             and right.get('account_id') == account.unique_id
             and right.get('item_id') == item_id
             and right.get('entitlement_type') == 'owned' and right.get('state') == 'active'
             and right.get('loan_id') is None and right.get('expires_at') is None
             and (metadata.get('downloadable') is None or metadata.get('downloadable') is True)
             and (metadata.get('owned') is None or metadata.get('owned') is True)
             and (metadata.get('archived') is None or metadata.get('archived') is False))
    _require(not service.storage.numilog_purchase_has_local_conflict(
        credential_id=account.credential_id, item_id=item_id,
    ))
    scalar = lambda key: metadata.get(key) if type(metadata.get(key)) is str else None
    item = SearchResult(
        id=item_id, record_id=item_id, docbase='NUMILOG', medium='digital',
        title=scalar('title') or 'Livre Numilog possédé', author=scalar('author'),
        type='Livre numérique possédé', format=None, publisher=None, published_at=None,
        description=None, thumbnail_url=None, friendly_url=None,
        scenario='NUMILOG_LIBRARY', source_provider='numilog',
        source_credential=account.credential_id, source_collection='library',
        owned=True, downloadable=True, content_type='ebook', metadata={},
    )
    owner, session, account_id = require_source(service, item)
    _require(owner.identity_id == identity_id and account_id == account.unique_id)
    sid = SynchronizationBrokerImpl(service)._source_id(account)
    target = NumilogOwnedDownloadTarget(
        entitlement_id, identity_id, sid, account.credential_id, account_id, item_id,
        (service.config.get('library.directory', '~/EBooks'), tuple(str(p) for p in roots)),
        id(session), session,
    )
    return target, item


def target_for_entry(service, entry):
    """No HTTP, directory creation, artifact scan or acquisition at display."""
    try:
        _require(type(entry) is dict and not entry.get('local')
                 and not entry.get('local_path') and not entry.get('placement_id'))
        identity_id = getattr(service.current_identity, 'identity_id', None)
        _require(identity_id is not None)
        possessions = entry.get('possessions')
        _require(type(possessions) in (tuple, list))
        # A future provider's owned right can be a competing local action:
        # never silently discard it just because this legacy facade does not
        # recognize its brand. Ambiguity is not authorization to select Numilog.
        candidates = [p for p in possessions if type(p) is dict
                      and p.get('identity_id') == identity_id and p.get('kind') == 'owned'
                      and p.get('state') == 'active']
        _require(len(candidates) == 1 and candidates[0].get('provider_id') == 'numilog')
        p = candidates[0]
        target, _item = _resolve(service, p.get('entitlement_id'), identity_id, p.get('item_id'))
        return target
    except Exception:
        return None


def _selected_owned_right_current(service, target) -> bool:
    """Readmit a manual right *after* its own canonical receipt was journaled.

    Unlike _resolve(), this must not require acquisitions == []: the shared
    canonical executor writes its own pending recovery receipt between GET
    and OriginLink publication. This read checks ONLY the one still-active
    supplier right and exact selected owner/session/configuration. Core's
    canonical publisher separately rejects competing final publications.
    """
    try:
        if (type(target) is not NumilogOwnedDownloadTarget
                or getattr(service.current_identity, "identity_id", None)
                   != target.identity_id
                or service._sessions.get(
                    service._session_key(target.provider_id, target.credential_id)
                ) is not target.session
                or id(target.session) != target.session_id
                or service.storage.metadata_get("identity-directory-migration-v1")):
            return False
        roots = _identity_root(service, target.identity_id)
        if roots is None or (
            service.config.get("library.directory", "~/EBooks"),
            tuple(str(path) for path in roots),
        ) != target.configuration:
            return False
        evidence = service.storage.numilog_purchase_preflight_evidence(
            identity_id=target.identity_id, credential_id=target.credential_id,
            account_id=target.account_id, item_id=target.item_id,
        )
        rights = evidence.get("entitlements")
        if type(rights) is not list or len(rights) != 1:
            return False
        right = rights[0]
        metadata = right.get("metadata")
        return (
            type(metadata) is dict
            and metadata.get("source") == _ATTESTED_OWNED_RIGHT_SOURCE
            and right.get("entitlement_id") == target.entitlement_id
            and right.get("identity_id") == target.identity_id
            and right.get("provider_id") == target.provider_id
            and right.get("credential_id") == target.credential_id
            and right.get("account_id") == target.account_id
            and right.get("item_id") == target.item_id
            and right.get("entitlement_type") == "owned"
            and right.get("state") == "active"
            and right.get("loan_id") is None
            and right.get("expires_at") is None
            and (metadata.get("downloadable") is None
                 or metadata.get("downloadable") is True)
            and (metadata.get("owned") is None
                 or metadata.get("owned") is True)
            and (metadata.get("archived") is None
                 or metadata.get("archived") is False)
        )
    except Exception:
        return False


def obtain(service, target):
    """Revalidate the exact target throughout the locked manual executor."""
    try:
        _require(type(target) is NumilogOwnedDownloadTarget)
        def admission():
            fresh, item = _resolve(service, target.entitlement_id, target.identity_id, target.item_id)
            _require(fresh == target and fresh.session is target.session)
            return item
        item = admission()
        owner = service.identity_by_id(target.identity_id)
        _require(owner is not None)
        # Installed Numilog v2 sessions use the sole canonical A→B writer.
        # Never route a complete guarded producer back into the historical
        # download_direct + numilog_delivered_artifact_v1 receipt namespace.
        from .canonical_delivery_abi import has_complete_delivery_abi
        if has_complete_delivery_abi(target.session):
            from .explicit_owned_canonical import obtain_explicit_owned
            return obtain_explicit_owned(
                service, target, resolve=_resolve,
                right_current=_selected_owned_right_current,
            )
        from .numilog_materialization_checkpoint import index_delivered
        acquired = service.acquire_direct_item(item, _admission=admission)
        return index_delivered(service, acquired, title=item.title)
    except Cancelled:
        raise
    except Exception:
        raise OwnedDownloadRejected('owned_download_unavailable') from None
