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
    attested_source: ClassVar[str] = _ATTESTED_OWNED_RIGHT_SOURCE
    entitlement_id: str
    identity_id: str
    source_id: str
    credential_id: str
    account_id: str
    item_id: str
    configuration: tuple
    session_id: int
    session: object = field(compare=False, repr=False)


def _resolve(service, entitlement_id, identity_id, item_id, *, canonical_offer_id=None):
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
    # Before GET, absolutely no acquisition receipt may exist. Once the
    # canonical executor has received its *own* completed bytes, it records
    # a stage in the shared ledger before publishing OriginLink. Admit ONLY
    # that exact staged receipt; never ignore a competing or legacy entry.
    owned_stage = False
    if canonical_offer_id is not None:
        from ..plugin_api.provider_rights_observation import ProviderRightsScope
        from .source_convergence import SourceKey
        from .canonical_recovery import _stage_id, _SCHEMA, _STAGED
        from .canonical_delivery_abi import has_complete_delivery_abi
        _require(type(canonical_offer_id) is str and canonical_offer_id
                 and len(canonical_offer_id) <= 256)
        _require(has_complete_delivery_abi(
            service._sessions.get(service._session_key('numilog', account.credential_id))
        ))
        key = SourceKey(
            ProviderRightsScope(identity_id, account.provider_id,
                                account.credential_id, account.unique_id),
            item_id, item_id, 'purchase', NumilogOwnedDownloadTarget.branch,
        )
        receipt_id = _stage_id(key, canonical_offer_id)
        # The bounded preflight deliberately does not expose metadata_json.
        # Read ONLY this candidate receipt from the neutral ledger API,
        # after its exact ID and all public coordinates have matched.
        candidate = acquisitions[0] if (
            type(acquisitions) is list and len(acquisitions) == 1
            and type(acquisitions[0]) is dict
            and acquisitions[0].get('acquisition_id') == receipt_id
            and acquisitions[0].get('status') == _STAGED
            and acquisitions[0].get('identity_id') == identity_id
            and acquisitions[0].get('account_id') == account.unique_id
            and acquisitions[0].get('item_id') == item_id
        ) else None
        staged = service.storage.acquisition_get(receipt_id) if candidate else None
        owned_stage = (
            type(staged) is dict
            and staged.get('acquisition_id') == receipt_id
            and staged.get('status') == _STAGED
            and staged.get('identity_id') == identity_id
            and staged.get('provider_id') == account.provider_id
            and staged.get('credential_id') == account.credential_id
            and staged.get('account_id') == account.unique_id
            and staged.get('item_id') == item_id
            and type(staged.get('metadata')) is dict
            and staged['metadata'].get('schema') == _SCHEMA
            and staged['metadata'].get('offer_id') == canonical_offer_id
        )
    _require(type(rights) is list and len(rights) == 1
             and (acquisitions == [] or owned_stage))
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
    # The single Core-owned staged journal is not another local Placement.
    # Every other collision is still a veto, including another receipt for
    # the same edition with a different offer ID.
    _require(owned_stage or not service.storage.numilog_purchase_has_local_conflict(
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
    owner, session, account_id = require_source(service, item, canonical_only=True)
    from inspect import getattr_static
    from .canonical_delivery_abi import has_complete_delivery_abi
    _require(owner.identity_id == identity_id and account_id == account.unique_id)
    # A partial/legacy session cannot expose an actionable owned-library
    # target: there is no longer an authorized legacy GET writer to back it.
    _require(has_complete_delivery_abi(session)
             and callable(getattr_static(
                 session, "downloadable_selected_delivery_candidate_guarded", None
             )))
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


def _selected_owned_right_current(service, target, *, selected_offer_id=None) -> bool:
    """Live right and source check, permitting only Core's own staged receipt."""
    try:
        if type(target) is not NumilogOwnedDownloadTarget:
            return False
        fresh, _ = _resolve(
            service, target.entitlement_id, target.identity_id, target.item_id,
            canonical_offer_id=selected_offer_id,
        )
        return fresh == target and fresh.session is target.session
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
        # Every owned Library acquisition is a one-shot canonical A→B
        # operation. No historical direct GET or v1 writer fallback remains.
        from .explicit_owned_canonical import obtain_explicit_owned
        return obtain_explicit_owned(
            service, target, resolve=_resolve,
            right_current=_selected_owned_right_current,
        )
    except Cancelled:
        raise
    except Exception:
        raise OwnedDownloadRejected('owned_download_unavailable') from None
