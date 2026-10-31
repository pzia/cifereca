"""One attested owned Kobo book from Library; never a source batch (#1347)."""
from __future__ import annotations

from dataclasses import dataclass

from ..models import SearchResult
from .materialization import MaterializationCoordinator


class OwnedDownloadRejected(ValueError):
    """Fixed public refusal; never reflect a provider or private identifier."""


@dataclass(frozen=True, repr=False)
class OwnedDownloadTarget:
    entitlement_id: str
    identity_id: str
    source_id: str
    scope: tuple
    configuration: str
    item_id: str
    revision_id: str


def _require(value):
    if not value:
        raise OwnedDownloadRejected("owned_download_unavailable")


def _resolve(service, entitlement_id, identity_id):
    from ..brokers import SynchronizationBrokerImpl
    from .source_materialization import _source

    _require(service.current_identity is not None and service.current_identity.identity_id == identity_id)
    rights = [row for row in service.storage.entitlements(provider_id="kobo", state="active")
              if row.get("entitlement_id") == entitlement_id and row.get("identity_id") == identity_id]
    _require(len(rights) == 1)
    right = rights[0]
    _require(right.get("entitlement_type") == "owned" and not right.get("expires_at") and not right.get("loan_id"))
    for key in ("credential_id", "account_id", "item_id", "revision_id"):
        _require(type(right.get(key)) is str and 0 < len(right[key]) <= 1024
                 and right[key] == right[key].strip() and not any(c in right[key] for c in "\0\r\n"))
    # Multiple active editions/accounts are not a choice this button can make.
    duplicates = [row for row in service.storage.entitlements(provider_id="kobo", state="active")
                  if row.get("identity_id") == identity_id and row.get("item_id") == right["item_id"]
                  and row.get("entitlement_type") == "owned"]
    # A 0.7 backup_put compatibility mirror produces an additional active
    # entitlement with a synthetic ID. It is NOT a second attested purchase
    # when a canonical provider right exists for this exact identity/book.
    # Prefer the independent provider-attested rows, but never auto-select a
    # different right from the requested Library possession.
    def projection_only(row):
        metadata = row.get("metadata")
        return (isinstance(metadata, dict)
                and (metadata.get("compatibility_projection") == "backup_items"
                     or metadata.get("imported_from") == "backup_items"))

    independent = [row for row in duplicates if not projection_only(row)]
    selected_pool = independent if independent else duplicates
    _require(len(selected_pool) == 1 and selected_pool[0]["entitlement_id"] == entitlement_id)
    accounts = [a for a in service.accounts if a.provider_id == "kobo" and a.identity_id == identity_id
                and a.credential_id == right["credential_id"] and a.unique_id == right["account_id"]]
    _require(len(accounts) == 1)
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(accounts[0])
    _, _, scope, configuration = _source(broker, sid)
    _require(service.local_placement_for_source(provider_id="kobo", credential_id=right["credential_id"],
                                               item_id=right["item_id"], identity_id=identity_id) is None)
    # A 0.7 backup receipt is historical evidence, not authority that this
    # exact current source has a physically usable Core Placement. The
    # identity/account/edition are already pinned by the entitlement and
    # selected broker account above; presence is checked by Core against a
    # verified exact local placement. Do NOT veto a new explicit GET because
    # backup_items still says complete, reconcile_required, acquiring, or
    # points to a stale/moved local_path.
    #
    # Likewise, a different unlinked Kobo/local file elsewhere in the same
    # identity is not proof about this owned resource. No global veto here;
    # the provider and acquisition executor retain their exact-source guards.
    metadata = right.get("metadata") or {}
    _require(isinstance(metadata, dict))
    _require(metadata.get("downloadable") is not False and metadata.get("owned") is not False
             and metadata.get("archived") is not True)
    _require(all(metadata.get(key) is None or type(metadata[key]) is bool
                 for key in ("owned", "downloadable", "archived")))
    target = OwnedDownloadTarget(entitlement_id, identity_id, sid, scope, configuration,
                                 right["item_id"], right["revision_id"])
    # Only local bibliographic scalars: no persisted acquisition URL or raw
    # provider metadata is copied into the request.
    text = lambda key, default=None: metadata.get(key) if type(metadata.get(key)) is str else default
    item = SearchResult(
        id="kobo:" + right["item_id"], record_id=right["item_id"], docbase="KOBO", medium="digital",
        title=text("title", right["item_id"]), author=text("author"), type="Livre numérique possédé",
        format=text("media_format", "epub"), publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="KOBO", source_provider="kobo",
        source_credential=right["credential_id"], content_type="ebook", source_collection="library",
        owned=True, downloadable=True, revision_id=right["revision_id"], metadata={},
    )
    return target, item


def owned_library_download_target(service, entry):
    """Passive admission for a single unambiguous possession in the current row."""
    # Never choose implicitly between two actionable source possessions.
    if isinstance(entry, dict) and isinstance(entry.get("possessions"), (list, tuple)):
        current = getattr(service.current_identity, "identity_id", None)
        candidates = [p for p in entry["possessions"] if isinstance(p, dict)
                      and p.get("identity_id") == current and p.get("state") == "active"
                      and p.get("kind") == "owned" and p.get("provider_id") in {"kobo", "numilog"}]
        if len(candidates) > 1:
            return None
    from .numilog_materialization_checkpoint import target_for_entry as resume_target
    pending = resume_target(service, entry)
    if pending is not None:
        return pending
    from .numilog_owned_download import target_for_entry
    numilog = target_for_entry(service, entry)
    if numilog is not None:
        return numilog
    try:
        _require(isinstance(entry, dict) and not entry.get("local") and not entry.get("placement_id")
                 and not entry.get("local_path"))
        current = service.current_identity
        _require(current is not None)
        possessions = entry.get("possessions")
        _require(isinstance(possessions, (list, tuple)))
        matches = [p for p in possessions if isinstance(p, dict) and p.get("provider_id") == "kobo"
                   and p.get("identity_id") == current.identity_id and p.get("kind") == "owned"
                   and p.get("state") == "active"]
        _require(len(matches) == 1 and type(matches[0].get("entitlement_id")) is str)
        target, _item = _resolve(service, matches[0]["entitlement_id"], current.identity_id)
        _require(target.item_id == matches[0].get("item_id"))
        return target
    except Exception:
        return None


def obtain_owned_library_item(service, target):
    """Recheck at dispatch and again *inside* the direct-acquisition lock."""
    from .numilog_materialization_checkpoint import NumilogIndexResumeTarget, resume
    if type(target) is NumilogIndexResumeTarget:
        return resume(service, target.acquisition_id, expected_receipt=target)
    from .numilog_owned_download import NumilogOwnedDownloadTarget, obtain
    if type(target) is NumilogOwnedDownloadTarget:
        return obtain(service, target)
    try:
        _require(type(target) is OwnedDownloadTarget)
        current, item = _resolve(service, target.entitlement_id, target.identity_id)
        _require(current == target)
        owner = service.identity_by_id(target.identity_id)
        _require(owner is not None)

        def admission():
            fresh, refreshed_item = _resolve(service, target.entitlement_id, target.identity_id)
            _require(fresh == target)
            return refreshed_item

        return MaterializationCoordinator(
            acquire=lambda selected: service.acquire_direct_item(selected, _admission=admission),
            index_path=service.catalog_index_path,
            # The acquisition belongs to the admitted owner even if selection
            # changes while a provider finishes its in-flight file.
            resolve_identity=lambda *_args: owner,
        ).obtain(item)
    except OwnedDownloadRejected:
        raise
    except Exception:
        raise OwnedDownloadRejected("owned_download_unavailable") from None
