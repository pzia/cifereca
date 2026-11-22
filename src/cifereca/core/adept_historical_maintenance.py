"""#976: bounded operator review of persisted historical loan transactions.

No runtime inventory fetch, automatic reconciliation or maintenance writer.
The recorded ADEPT binding is a historical ledger observation, not fresh
inventory evidence, and the canonical branch is never inferred from it.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .adept_historical_diagnostic import review_historical_loan_diagnostic
from .canonical_recovery import _unique_checkpoint_fields
from .local_origin_suggestion_operator import _root, _navigation_epoch
from .library_sqlite_freshness import sqlite_source_stamp
from .source_context import source_context_epoch
from .source_convergence import SourceKey

_PAGE = 4
_MAX_METADATA = 65536


def _text(value, *, empty=False):
    return (type(value) is str and (empty or bool(value)) and len(value) <= 1024
            and all(ord(c) >= 32 and ord(c) != 127 for c in value))


@dataclass(frozen=True, slots=True, repr=False)
class HistoricalReviewRequest:
    service: object
    owner: object
    identity: str
    directory: str
    storage: object
    database: Path
    config: object
    root: Path
    source_epoch: int
    navigation_epoch: int

    def current(self):
        # In-memory check suitable for GTK completion; DB binding separately
        # revalidated by the worker.
        try:
            s = self.service
            return (s.current_identity is self.owner
                    and self.owner.identity_id == self.identity
                    and self.owner.directory_name == self.directory
                    and s.storage is self.storage and Path(s.storage.path) == self.database
                    and s.config is self.config and _root(s.config) == self.root
                    and source_context_epoch(s) == self.source_epoch
                    and _navigation_epoch(s) == self.navigation_epoch)
        except Exception:
            return False

    def bound(self):
        return (self.current() and not self.storage._connect().in_transaction
                and self.storage.exact_profile_directory_binding(self.identity)
                == (self.directory, True))


def admit_historical_review(service):
    try:
        owner = service.current_identity
        if (not _text(owner.identity_id) or not _text(owner.directory_name)
                or owner.directory_name in (".", "..")
                or "/" in owner.directory_name or chr(92) in owner.directory_name):
            return None
        request = HistoricalReviewRequest(
            service, owner, owner.identity_id, owner.directory_name, service.storage,
            Path(service.storage.path), service.config, _root(service.config),
            source_context_epoch(service), _navigation_epoch(service),
        )
        return request if request.current() else None
    except Exception:
        return None


def _page(storage, identity, cursor):
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        return tuple(db.execute(
            "SELECT acquisition_id,provider_id,credential_id,account_id,item_id,status,"
            "CASE WHEN length(CAST(metadata_json AS BLOB))<=? THEN metadata_json ELSE NULL END "
            "FROM acquisition_ledger WHERE identity_id=? AND strategy='loan_auto' "
            "AND executor='kobo-adept' AND acquisition_id>? ORDER BY acquisition_id LIMIT ?",
            (_MAX_METADATA, identity, cursor, _PAGE + 1),
        ).fetchall())


def _branches(storage, identity, provider, credential, account, edition, loan):
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        return tuple(db.execute(
            "SELECT branch_id FROM source_references WHERE identity_id=? AND provider_id=? "
            "AND credential_id=? AND account_id=? AND edition_id=? AND right_id=? "
            "AND reference_kind='loan' ORDER BY branch_id LIMIT 2",
            (identity, provider, credential, account, edition, loan),
        ).fetchall())


def _row(request, row, verify):
    aid, provider, credential, account, edition, status, raw = row
    result = {
        "acquisition_id": aid if _text(aid) else None,
        "provider_id": provider if _text(provider) else None,
        "credential_id": credential if _text(credential) else None,
        "account_id": account if _text(account, empty=True) else None,
        "edition_id": edition if _text(edition) else None,
        "ledger_status": status if _text(status) else "unverified",
        "provider_loan_id": None, "recorded_adept_profile_id": None,
        "recorded_adept_loan_id": None, "historical_license_state": "unknown",
        "inventory_checked_at": None, "canonical_branch": None,
        "canonical_status": "scope_unverified", "publication": None, "local_bytes": None,
        "provider_loan_state": "not_checked",
        "inventory_transport_attested": False, "activation_attested": False,
        "ledger_branch_attested": False,
        "may_rebind_or_return": False, "may_reacquire": False,
    }
    if (not all(_text(x) for x in (aid, provider, credential, edition))
            or not _text(account, empty=True) or type(raw) is not str):
        return result
    try:
        metadata = json.loads(raw, object_pairs_hook=_unique_checkpoint_fields)
    except (ValueError, TypeError, RecursionError):
        return result
    if type(metadata) is not dict:
        return result
    loan = metadata.get("provider_loan_id")
    if not _text(loan):
        return result
    result["provider_loan_id"] = loan
    binding = metadata.get("adept_binding")
    if type(binding) is dict:
        for key in ("adept_profile_id", "adept_loan_id"):
            value = binding.get(key)
            if _text(value):
                result["recorded_" + key] = value
        stamp = binding.get("inventory_checked_at")
        if (status == "complete" and result["recorded_adept_profile_id"]
                and result["recorded_adept_loan_id"] and type(stamp) is int and stamp > 0
                and binding.get("inventory_status") == "resolved"):
            result["inventory_checked_at"] = stamp
            state = binding.get("loan_state")
            if state in ("active", "expired", "returned"):
                result["historical_license_state"] = state + "_recorded"
    coordinates = (request.storage, request.identity, provider, credential, account, edition, loan)
    branches = _branches(*coordinates)
    if len(branches) != 1:
        result["canonical_status"] = "ambiguous_branch" if branches else "source_not_recorded"
        return result
    branch = branches[0][0]
    if not _text(branch):
        return result
    try:
        source = SourceKey(ProviderRightsScope(request.identity, provider, credential, account),
                           edition, loan, "loan", branch)
    except ValueError:
        return result
    review = review_historical_loan_diagnostic(
        request.storage, source=source, library_root=request.root,
        inventory_profile_id=result["recorded_adept_profile_id"] or "",
        inventory_complete=False, inventory_loans=(),
        verify_local_bytes=verify,
    )
    if _branches(*coordinates) != branches:
        result["canonical_status"] = "source_changed"
        return result
    result.update(canonical_branch=branch, canonical_status="unique_source_record",
                  publication=review["publication"], local_bytes=review["local_bytes"])
    return result


def run_historical_review(service, request, *, cursor="", verify_local_bytes=False):
    """Return a bounded operator page, or None on revoked/uncertain context.

    Each row is an independent observation, not a shared SQL/FS snapshot.
    Explicit hashing checks at most four files using the existing per-file
    limit. The supplied cursor comes from the previous private page.
    """
    if (type(request) is not HistoricalReviewRequest or request.service is not service
            or not _text(cursor, empty=True) or type(verify_local_bytes) is not bool):
        return None
    try:
        if not request.bound():
            return None
        # A ledger page can remain byte-for-byte identical while its
        # independent source references/branch proof changes during hashing.
        # The same source-image fence already protects the v1 hold view.
        stamp = sqlite_source_stamp(request.database)
        if stamp is None:
            return None
        observed = _page(request.storage, request.identity, cursor)
        reports = []
        for row in observed[:_PAGE]:
            if not request.bound():
                return None
            reports.append(_row(request, row, verify_local_bytes))
        if (not request.bound() or sqlite_source_stamp(request.database) != stamp
                or _page(request.storage, request.identity, cursor) != observed
                or any(report["acquisition_id"] is None for report in reports)):
            return None
        next_cursor = observed[_PAGE - 1][0] if len(observed) > _PAGE else None
        return {"rows": tuple(reports), "next_cursor": next_cursor,
                "coverage": "persisted_loan_auto_adept_ledger",
                "complete_provider_inventory": False,
                "common_snapshot_attested": False, "mutation_authorized": False}
    except Exception:
        return None


@dataclass(frozen=True, slots=True, repr=False)
class _HoldPageAnchor:
    request: HistoricalReviewRequest
    offset: int
    stamp: tuple
    verified: bool


def run_historical_hold_review(service, request, *, offset=0,
                               verify_local_bytes=False):
    """Project the existing retry queue, not a second marker inventory.

    A v1 marker can exist before any acquisition ledger receipt. This
    explicit maintenance view includes those exact loan occurrences too.
    The current provider status, activation, fresh offer and replay proof
    remain unchecked. No marker value, operation ID or digest is exported.
    """
    from .loan_download_retry_review import review_loan_download_retries

    if (type(request) is not HistoricalReviewRequest or request.service is not service
            or type(offset) is not int or not 0 <= offset <= 2048
            or type(verify_local_bytes) is not bool):
        return None
    try:
        if not request.bound():
            return None
        # Hashing a selected historical copy can outlive the local marker
        # snapshot. A changed WAL invalidates the displayed hold evidence.
        stamp = sqlite_source_stamp(request.database)
        if stamp is None:
            return None
        blocked = tuple(row for row in review_loan_download_retries(
            service, identity_id=request.identity,
        ) if row.source.kind == "loan" and row.adept_hold)
        reports = []
        for row in blocked[offset:offset + _PAGE]:
            source = row.source
            copy_state = "not_requested"
            if verify_local_bytes:
                # Existing bounded no-follow verifier; a present SHA is not
                # replay proof, and an unavailable file is not a negative A.
                diagnostic = review_historical_loan_diagnostic(
                    request.storage, source=source, library_root=request.root,
                    inventory_profile_id="", inventory_complete=False,
                    inventory_loans=(), verify_local_bytes=True,
                )
                local = diagnostic.get("local_bytes")
                witness = local.get("evidence") if type(local) is dict else None
                copy_state = (
                    "verified_local_copy_observed"
                    if (type(local) is dict
                        and local.get("selection") == "unique_recorded_candidate"
                        and type(witness) is dict
                        and witness.get("placement_evidence") == "verified_artifact_bytes")
                    else "not_proven"
                )
            reports.append({
                "local_copy_evidence": copy_state,
                "provider_id": source.scope.provider_id,
                "credential_id": source.scope.credential_id,
                "account_id": source.scope.account_id,
                "edition_id": source.edition_id,
                "provider_loan_id": source.right_id,
                "canonical_branch": source.branch,
                "title": row.title,
                "hold_origin": row.adept_hold_origin,
                "marker_state": row.adept_marker_state,
                "non_submission_evidence": row.adept_unsubmitted_state,
                "non_submission_reason": row.adept_unsubmitted_reason,
                "recorded_inactive": row.inactive,
                "provider_loan_state": "not_checked",
                "activation_attested": False,
                "fresh_offer_attested": False,
                "replay_proof": "not_established",
                "unlock_authorized": False,
            })
        if not request.bound() or sqlite_source_stamp(request.database) != stamp:
            return None
        return {
            "rows": tuple(reports),
            "next_cursor": offset + _PAGE if offset + _PAGE < len(blocked) else None,
            "known_holds": len(blocked),
            "_review_anchor": _HoldPageAnchor(request, offset, stamp, verify_local_bytes),
            "coverage": "persisted_source_retry_queue",
            "complete_provider_inventory": False,
            "common_snapshot_attested": False,
            "mutation_authorized": False,
        }
    except Exception:
        return None


@dataclass(frozen=True, slots=True, repr=False)
class HistoricalHoldSelection:
    """Process-local selection, not a replay permit or a provider request."""
    request: HistoricalReviewRequest
    source: SourceKey
    marker: object
    witness: object
    stamp: tuple
    copy_evidence: str
    final_receipt_evidence: str = "not_checked"
    unlock_authorized: bool = False

    def current(self):
        from .canonical_adept_attempt import (
            review_loan_adept_marker_in_transaction,
            review_loan_adept_unsubmitted_in_transaction,
        )
        from .adept_historical_snapshot import historical_read_snapshot
        try:
            if (not self.request.bound()
                    or sqlite_source_stamp(self.request.database) != self.stamp):
                return False
            with historical_read_snapshot(self.request.storage) as db:
                if (review_loan_adept_marker_in_transaction(db, self.source) != self.marker
                        or review_loan_adept_unsubmitted_in_transaction(
                            db, source=self.source) != self.witness):
                    return False
            return (self.request.bound()
                    and sqlite_source_stamp(self.request.database) == self.stamp)
        except Exception:
            return False


def select_historical_hold(service, request, page, *, ordinal, confirmed=False):
    """Select one exact occurrence from the displayed private review page.

    No UUID, offer, marker value or path supplied by UI can substitute for
    the admitted page. Do not export the returned witness to plugin ports.
    Still requires the independently qualified activation, consent, fresh
    offer and a Core writer under acquisition lock before any rearm.
    """
    from .canonical_adept_attempt import (
        review_loan_adept_marker_in_transaction,
        review_loan_adept_unsubmitted_in_transaction,
    )
    from .adept_historical_snapshot import historical_read_snapshot

    if (confirmed is not True or type(request) is not HistoricalReviewRequest
            or request.service is not service or type(page) is not dict
            or type(ordinal) is not int or ordinal < 0):
        return None
    try:
        anchor = page.get("_review_anchor")
        if (type(anchor) is not _HoldPageAnchor or anchor.request is not request
                or not request.bound()
                or sqlite_source_stamp(request.database) != anchor.stamp):
            return None
        fresh = run_historical_hold_review(
            service, request, offset=anchor.offset, verify_local_bytes=anchor.verified)
        if fresh != page or ordinal >= len(fresh["rows"]):
            return None
        row = fresh["rows"][ordinal]
        source = SourceKey(
            ProviderRightsScope(request.identity, row["provider_id"],
                                row["credential_id"], row["account_id"]),
            row["edition_id"], row["provider_loan_id"], "loan", row["canonical_branch"],
        )
        with historical_read_snapshot(request.storage) as db:
            marker = review_loan_adept_marker_in_transaction(db, source)
            witness = review_loan_adept_unsubmitted_in_transaction(db, source=source)
        if (not request.bound()
                or sqlite_source_stamp(request.database) != anchor.stamp):
            return None
        # Explicit physical verification is opt-in; never hash final receipt
        # bytes during passive review or merely selecting the report by default.
        final_receipt = "not_checked"
        if anchor.verified:
            from .adept_historical_final_receipt import observe_final_receipt
            final_receipt = observe_final_receipt(request, source, marker, anchor.stamp)
        selection = HistoricalHoldSelection(
            request, source, marker, witness, anchor.stamp, row["local_copy_evidence"],
            final_receipt_evidence=final_receipt)
        return selection if selection.current() else None
    except Exception:
        return None
