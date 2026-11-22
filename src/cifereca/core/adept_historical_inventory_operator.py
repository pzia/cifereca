"""#976: explicit comparison of ONE recorded loan to the local ADEPT inventory.

Only a uniquely recorded historical stable profile/loan may select the
read-only runtime. No alias fallback, current binding, acquisition writer,
provider query, return or raw inventory crosses this boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from .adept_historical_inventory_join import _recorded_binding, review_legacy_adept_inventory
from .adept_historical_maintenance import HistoricalReviewRequest, _branches, _row, _text, _MAX_METADATA
from .library_sqlite_freshness import sqlite_source_stamp
from .adept_historical_snapshot import historical_read_snapshot
from .source_convergence import SourceKey
from ..plugin_api.provider_rights_observation import ProviderRightsScope


_FIELDS = ("acquisition_id", "provider_id", "credential_id", "account_id", "edition_id",
           "provider_loan_id", "recorded_adept_profile_id", "recorded_adept_loan_id", "canonical_branch")


def _record(request, acquisition_id):
    with historical_read_snapshot(request.storage) as db:
        rows = db.execute(
            "SELECT acquisition_id,provider_id,credential_id,account_id,item_id,status,"
            "CASE WHEN length(CAST(metadata_json AS BLOB))<=? THEN metadata_json ELSE NULL END "
            "FROM acquisition_ledger WHERE identity_id=? AND acquisition_id=? "
            "AND strategy='loan_auto' AND executor='kobo-adept' LIMIT 2",
            (_MAX_METADATA, request.identity, acquisition_id),
        ).fetchall()
    return rows[0] if len(rows) == 1 else None


def _producer(manager):
    candidates = manager.candidates("runtime.adapter", ready_only=False)
    # A runtime registry is a bounded inert discovery surface, not proof of
    # a historical activation. Refuse oversized snapshots before scanning
    # (including a unique eligible adapter hidden after hundreds of entries).
    if type(candidates) not in (tuple, list) or len(candidates) > 256:
        return None
    claimed = [
        row.descriptor.id for row in candidates
        if row.enabled is True and row.state == "ready"
        and row.descriptor.type == "runtime.adapter"
        and type(row.descriptor.capabilities) is tuple
        and "loan.inventory.readonly" in row.descriptor.capabilities
    ]
    if len(claimed) != 1 or not _text(claimed[0]):
        return None
    plugin_id = claimed[0]
    row = manager.status(plugin_id)
    if (row.enabled is not True or row.state != "ready" or row.descriptor.id != plugin_id
            or row.descriptor.type != "runtime.adapter"
            or type(row.descriptor.capabilities) is not tuple
            or "loan.inventory.readonly" not in row.descriptor.capabilities):
        return None
    runtime = manager.service(plugin_id)
    if not callable(getattr(runtime, "loan_inventory_readonly", None)):
        return None
    return plugin_id, runtime


@dataclass(frozen=True, slots=True, repr=False)
class _ReadControl:
    current: object

    def cancelled(self):
        return not self.current()


def admit_inventory_comparison(service, request, selected, *, explicitly_requested=False):
    """Cheap GTK admission. Worker revalidates all selected SQL coordinates.

    Private row values are frozen, not authority to read a runtime. The
    operation is one-shot and never falls back to an older runtime ABI.
    """
    if (explicitly_requested is not True or type(request) is not HistoricalReviewRequest
            or request.service is not service or not request.current()
            or type(selected) is not dict or selected.get("ledger_status") != "complete"
            or selected.get("canonical_status") != "unique_source_record"):
        return None
    coordinates = tuple(selected.get(key) for key in _FIELDS)
    if not all(_text(value, empty=(index == 3)) for index, value in enumerate(coordinates)):
        return None
    try:
        manager = service.plugin_manager
    except Exception:
        return None
    if manager is None:
        return None
    used = False
    guard = Lock()

    def operation():
        nonlocal used
        with guard:
            if used:
                return None
            used = True
        return _compare(service, request, coordinates, manager)
    return operation


def _compare(service, request, coordinates, manager):
    def current():
        try:
            return request.current() and service.plugin_manager is manager
        except Exception:
            return False
    try:
        if not current() or not request.bound():
            return None
        stamp = sqlite_source_stamp(request.database)
        original = _record(request, coordinates[0])
        if stamp is None or original is None:
            return None
        observed = _row(request, original, False)
        if (tuple(observed.get(key) for key in _FIELDS) != coordinates
                or observed["ledger_status"] != "complete"
                or observed["canonical_status"] != "unique_source_record"):
            return None
        _, provider, credential, account, edition, loan, profile, adept_loan, branch = coordinates
        source = SourceKey(ProviderRightsScope(request.identity, provider, credential, account),
                           edition, loan, "loan", branch)
        expected = ("recorded", (profile, adept_loan))
        if _recorded_binding(request.storage, source) != expected:
            return None
        producer = _producer(manager)
        if producer is None:
            return None
        plugin_id, runtime = producer

        def live():
            if not current():
                return False
            try:
                next_producer = _producer(manager)
                return (next_producer is not None and next_producer[0] == plugin_id
                        and next_producer[1] is runtime and current())
            except Exception:
                return False

        if (not live() or not request.bound() or _record(request, coordinates[0]) != original
                or sqlite_source_stamp(request.database) != stamp):
            return None
        inventory = runtime.loan_inventory_readonly(
            profile, consented=True, control=_ReadControl(live), stable_id_only=True,
        )
        if (not live() or not request.bound()
                or getattr(inventory, "status", None) != "complete"
                or getattr(inventory, "stable_profile_observed", None) is not True
                or getattr(inventory, "profile_reference", None) != profile
                or _record(request, coordinates[0]) != original
                or _branches(request.storage, request.identity, provider, credential, account, edition, loan)
                != ((branch,),)
                or _recorded_binding(request.storage, source) != expected
                or sqlite_source_stamp(request.database) != stamp):
            return None
        result = review_legacy_adept_inventory(
            request.storage, source=source, inventory_profile_id=profile,
            inventory_complete=True, inventory_loans=inventory.loans,
        )
        if (not live() or _record(request, coordinates[0]) != original
                or sqlite_source_stamp(request.database) != stamp):
            return None
        # Return only closed comparison categories and booleans. No profile
        # name, tokens, paths or facts about the other loans in this profile.
        return {
            "recorded_binding": result["recorded_binding"],
            "inventory_comparison": result["inventory_comparison"],
            "adept_license_state_reported": result["adept_license_state_reported"],
            "provider_loan_state": "not_checked",
            "activation_attested": False,
            "inventory_transport_attested": False,
            "may_rebind_or_return": False, "may_reacquire": False,
        }
    except Exception:
        return None
