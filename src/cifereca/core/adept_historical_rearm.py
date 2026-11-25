"""#7244: supervised, exact historical ADEPT v1 release (owner option A).

The operator's attestation is explicitly manual, not a runtime assertion of
activation generation. The selected-offer ABI only observes an existing
right. This module never invokes a candidate, borrows or executes ADEPT.
The ordinary canonical pipeline remains the only delivery writer.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import time
from inspect import getattr_static
from unicodedata import category

from ..locking import InterProcessLock
from ..models import Account, Loan
from ..plugin_api.loan_inventory import LoanInventoryScope
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from . import canonical_adept_attempt as marker, canonical_recovery as recovery
from .adept_acsm_plan import profile_is_explicitly_acsm_unready
from .adept_historical_final_receipt import observe_final_receipt
from .adept_historical_maintenance import HistoricalHoldSelection
from .adept_historical_snapshot import historical_read_snapshot
from .adept_runtime_preflight import require_fresh_acsm_capability
from .canonical_loan_borrower import pin_borrower
from .canonical_loan_retry_guard import historical_adept_ledger_hold
from .canonical_publication import _stable
from .canonical_runtime_attempt import read_runtime_attempt_in_db
from .library_sqlite_freshness import sqlite_source_stamp
from .loan_action_scope import capture_loan_action_scope
from .selected_loan_offer import select_exact_loan_offer
from .source_acquisition_consent import (
    consent_current_in_transaction, read_source_acquisition_consent,
)
from .source_context import source_context_mutation


def _digest(value):
    return (type(value) is str and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _profile_ref(value):
    return (type(value) is str and 0 < len(value) <= 256
            and value == value.strip()
            and all(category(c) not in ("Cc", "Cf", "Cs", "Zl", "Zp") for c in value))


def _audit_key(review):
    return marker.historical_rearm_audit_key(review.marker)


def _local_admissible(db, selection, inventory_scope):
    """SQL-only recheck of the exact negative hold; no nested Storage call."""
    source = selection.source
    coords = tuple(recovery._scope(source))
    ref = _stable("source", *coords)
    rows = db.execute(
        "SELECT identity_id,provider_id,credential_id,account_id,branch_id,"
        "edition_id,right_id,reference_kind,state,synchronizable,evidence_kind,evidence_ref "
        "FROM source_references WHERE source_reference_id=?", (ref,),
    ).fetchall()
    if (len(rows) != 1 or rows[0][:11] != (*coords, "active", 1, "provider_response")
            or type(rows[0][11]) is not str or not rows[0][11]
            or not recovery._exact_borrower_bound_in_db(db, source.scope)
            or not consent_current_in_transaction(db, source)
            or not marker.matches_reviewed_loan_adept_marker_in_transaction(
                db, source=source, review=selection.marker)
            or marker.review_loan_adept_unsubmitted_in_transaction(
                db, source=source) != selection.witness
            or read_runtime_attempt_in_db(db, source)[0]
            or historical_adept_ledger_hold(db, inventory_scope, source)):
        return False
    # Conservative: any recorded origin needs the existing local recovery
    # path, including unverified/missing placements. "Not proven" in the
    # historical report is not a proof of absence.
    return db.execute(
        "SELECT 1 FROM origin_links WHERE source_reference_id=? LIMIT 1", (ref,),
    ).fetchone() is None


def _fresh_active_offer(observed, right, source):
    if (type(observed) is not ProviderRightsSnapshot
            or observed.scope != source.scope or observed.branch != source.branch
            or observed.status not in ("success", "partial")
            or len(observed.rights) != 1 or observed.rights[0] is not right
            or right.state != "active" or not right.delivery_attested
            or (right.edition_id, right.right_id, right.kind) != (
                source.edition_id, source.right_id, source.kind)):
        return False
    try:
        stamp = datetime.fromisoformat(right.observed_at.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        return -5 <= age <= 300
    except (AttributeError, ValueError, TypeError, OverflowError):
        return False


def rearm_reviewed_historical_hold(service, selection, *, journal_sha256,
                                   historical_profile_id, confirmed, view_current=None):
    """Core-only operation after the operator attests THIS exact incident.

    The confirmation states that the identified journal documents refusal
    before launch for the selected marker, same loan and unchanged ADEPT
    activation. The journal digest is a reference to the operator's evidence,
    not a machine verification of its content. No generic reset is exposed.
    A successful return records only the exact local CAS and its audit.
    """
    def view_live():
        return view_current is None or (callable(view_current) and view_current() is True)

    if (not view_live() or confirmed is not True or type(selection) is not HistoricalHoldSelection
            or selection.request.service is not service
            or not _digest(journal_sha256) or not _profile_ref(historical_profile_id)
            or selection.marker.state != "held_phase_unknown"
            or selection.witness.state not in ("not_recorded", "non_submission_recorded")
            or selection.recorded_inactive or selection.copy_evidence != "not_proven"
            or selection.final_receipt_evidence != "not_observed"
            or not selection.current()):
        raise ValueError("historical_rearm_unqualified")
    request, source = selection.request, selection.source
    storage, scope = request.storage, source.scope
    # Reobserve the final checkpoint, rather than trusting a supplied
    # disposition. With no checkpoint or origin, the unchanged SQLite
    # stamp under the subsequent lease fences the absence of local receipts.
    if observe_final_receipt(request, source, selection.marker, selection.stamp) != "not_observed":
        raise ValueError("historical_rearm_local_recovery")
    with InterProcessLock(recovery.acquisition_lock_file(create=False),
                          existing_nofollow=True) as lock:
        if not view_live() or not selection.current() or not lock.strict_current():
            raise ValueError("historical_rearm_scope_changed")
        loan = Loan(id=source.right_id, record_id=source.edition_id,
                    provider="UNKNOWN", medium="digital", title="", state="active",
                    source_provider=scope.provider_id, source_credential=scope.credential_id,
                    source_account_id=scope.account_id, identity_id=scope.identity_id)
        account = service._account_for_loan(loan)
        session = service._session_for_source(scope.provider_id, scope.credential_id)
        if (type(account) is not Account
                or (account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id) != (
                    scope.identity_id, scope.provider_id, scope.credential_id, scope.account_id)):
            raise ValueError("historical_rearm_scope_changed")
        lease = capture_loan_action_scope(
            service, loan, account, session, expected_identity_id=scope.identity_id)
        if lease is None:
            raise ValueError("historical_rearm_scope_changed")
        inventory_scope = LoanInventoryScope.for_account(
            account, descriptor=getattr_static(session, "descriptor", None))
        with historical_read_snapshot(storage) as db:
            if not _local_admissible(db, selection, inventory_scope):
                raise ValueError("historical_rearm_local_recovery_or_consent")

        def current():
            return (view_live() and lock.strict_current() and selection.current()
                    and lease.current(service)
                    and source in read_source_acquisition_consent(service, account))

        if not current():
            raise ValueError("historical_rearm_scope_changed")
        borrower, borrower_current = pin_borrower(service, source)
        manager = service._plugin_manager
        generation = getattr(manager, "registry_generation", None)
        runtime = service._required_plugin_service("runtime.kobo-adept")

        def live():
            return (current() and borrower_current()
                    and service._plugin_manager is manager
                    and getattr(manager, "registry_generation", None) == generation
                    and service._required_plugin_service("runtime.kobo-adept") is runtime)

        def profile():
            if not live():
                raise ValueError("historical_rearm_scope_changed")
            name, _ = service.resolve_adept_profile_for_identity(borrower)
            row = service._adept_profile_row(
                name, service.adept_profiles_for_identity(borrower, profile_name=name))
            if (type(row) is not dict or (row.get("name") or row.get("profile")) != name
                    or row.get("profile_id") != historical_profile_id
                    or service._originating_adept_profile_id(borrower, name) != historical_profile_id
                    or profile_is_explicitly_acsm_unready(row) or not live()):
                raise ValueError("historical_rearm_profile_changed")
            return name, row.get("user_uuid")

        pinned_profile = profile()
        require_fresh_acsm_capability(service, pinned_profile[0], scope_current=live)
        if profile() != pinned_profile or not live():
            raise ValueError("historical_rearm_profile_changed")
        # Same existing selected-right observation as the canonical bridge.
        # Never call the returned candidate function in Maintenance.
        status, observed = select_exact_loan_offer(
            session, account, loan, branch=source.branch, current=live)
        if status != "selected" or observed is None:
            raise ValueError("historical_rearm_offer_unqualified")
        snapshot, right, _candidate = observed
        if not _fresh_active_offer(snapshot, right, source):
            raise ValueError("historical_rearm_offer_unqualified")
        if profile() != pinned_profile:
            raise ValueError("historical_rearm_profile_changed")
        require_fresh_acsm_capability(service, pinned_profile[0], scope_current=live)
        if profile() != pinned_profile or not live():
            raise ValueError("historical_rearm_profile_changed")
        payload = json.dumps({
            "schema": 1, "source": recovery._scope(source),
            "borrower_identity_id": borrower, "adept_profile_id": historical_profile_id,
            "marker_fingerprint": selection.marker.fingerprint,
            "user_uuid_digest": marker.historical_user_digest(pinned_profile[1]),
            "proof": "owner_attested_same_activation_and_pre_dispatch_refusal",
            "journal_sha256": journal_sha256,
            "non_submission_witness": selection.witness.reason,
            "fresh_offer_digest": sha256(right.delivery_offer_id.encode()).hexdigest(),
            "observed_at": right.observed_at, "decided_at": int(time.time()),
            "result": "exact_marker_released",
        }, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

        # No network/runtime callback under SQLite. The source generation
        # fence serializes ordinary Core context revocation with this short
        # commit; the durable stamp catches concurrent persisted changes.
        with source_context_mutation(service):
            with marker._durable_marker_transaction(storage) as db:
                if (not view_live() or not request.current() or not lock.strict_current()
                        or sqlite_source_stamp(request.database) != selection.stamp
                        or not _fresh_active_offer(snapshot, right, source)
                        or not _local_admissible(db, selection, inventory_scope)):
                    raise ValueError("historical_rearm_scope_changed")
                marker._release_reviewed_historical_attempt_in_transaction(
                    db, source=source, review=selection.marker,
                    witness=selection.witness, payload=payload)
                if (not view_live() or not request.current() or not lock.strict_current()
                        or service._plugin_manager is not manager
                        or getattr(manager, "registry_generation", None) != generation):
                    raise ValueError("historical_rearm_scope_changed")
        return "exact_marker_released"
