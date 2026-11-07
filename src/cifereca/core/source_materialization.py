"""Explicit source confirmation, isolated from default metadata synchronization.

The temporary Kobo adapter reuses the existing preflight/executor policy. Plans
are bounded, memory-only and one-use; losing a process requires a new preview.
Neither advisory previews nor persisted counters are acquisition permissions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re
import secrets
import stat
import threading
import time

from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file
from ..plugin_api import SyncResult
from . import kobo_backup
from .canonical_delivery_abi import has_complete_delivery_abi
from .kobo_local_audit import _read_hash_within
from .kobo_local_reconciliation import _roots, _within

_LOG = logging.getLogger("cifereca.sync")
_TTL = 600
_PROCESS_NONCE = secrets.token_hex(16)
_BUDGET = 8 * 1024 * 1024
_COUNTS = ("already_local_linked", "already_local_verified", "needs_reconciliation",
           "attention_required", "skipped_archived", "blocked_scope", "blocked_unlinked", "not_downloadable",
           "candidate_unverified", "potentially_missing", "invalid_manifest_items")


class _Rejected(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise _Rejected(reason)


def _digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    _require(len(raw) <= _BUDGET, "evidence_budget_exhausted")
    return hashlib.sha256(raw.encode()).hexdigest()


def _result(status, *, ok=False, changed=0, **facts):
    return SyncResult(ok=ok, changed=changed, warnings=() if ok else (status,), facts={
        "phase": "materialize", "status": status, **facts,
    })


def process_owner():
    return f"{os.getpid()}:{_PROCESS_NONCE}"


@dataclass(repr=False)
class MaterializationPlans:
    entries: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, scope, manifest, evidence):
        with self.lock:
            now = time.monotonic()
            self.entries = {key: row for key, row in self.entries.items() if row[0] > now}
            while len(self.entries) >= 16:
                self.entries.pop(next(iter(self.entries)))
            token = secrets.token_hex(32)
            self.entries[token] = (now + _TTL, scope, manifest, evidence)
            return token

    def take(self, token, scope):
        _require(type(token) is str and re.fullmatch("[0-9a-f]{64}", token), "invalid_confirmation")
        with self.lock:
            row = self.entries.get(token)
            _require(row is not None and row[1] == scope, "confirmation_unavailable")
            self.entries.pop(token)
        _require(row[0] > time.monotonic(), "confirmation_expired")
        return row


def _source(broker, source_id, *, allow_canonical=False):
    # No fallback to the selected account, and no implicit connection.
    try:
        rows = [row for row in broker.service.accounts if broker._source_id(row) == source_id]
    except (TypeError, AttributeError):
        raise _Rejected("unknown_source") from None
    _require(len(rows) == 1, "ambiguous_source" if rows else "unknown_source")
    account = rows[0]
    _require(account.provider_id == "kobo", "not_supported")
    scope = (source_id, account.identity_id, account.provider_id, account.credential_id, account.unique_id)
    _require(all(type(value) is str and 0 < len(value) <= 1024 and not any(c in value for c in "\0\n\r")
                 for value in scope), "invalid_source")
    _require(broker._enabled(source_id), "source_disabled")
    service = broker.service
    _require(service.storage.identity_for_connection("kobo", account.credential_id) == account.identity_id, "invalid_source")
    sessions = [s for s in service._sessions.values()
                if s.provider_id == "kobo" and s.credential_id == account.credential_id]
    _require(len(sessions) == 1, "ambiguous_source")
    session = sessions[0]
    _require(session.connected, "source_disconnected")
    _require(bool(getattr(session.capabilities, "backup", False)), "not_supported")
    # #3196: a fully typed Core provider must never dispatch a second Kobo
    # acquisition through the legacy, backup_items-authoritative worker.
    # Check on BOTH preview and execution, including an ABI added after a
    # confirmation token was issued.
    _require(allow_canonical or not has_complete_delivery_abi(session),
             "canonical_source_delivery_active")
    session_accounts = session.accounts
    _require(len(session_accounts) == 1 and session_accounts[0].unique_id == account.unique_id, "ambiguous_account")
    _require(sum(a.provider_id == "kobo" and a.credential_id == account.credential_id
                 for a in service.accounts) == 1, "ambiguous_account")
    _require(not service.storage.metadata_get("identity-directory-migration-v1"), "directory_migration_pending")
    roots = _roots(service, account.identity_id)
    for root in roots:
        _require(root.is_absolute() and ".." not in root.parts, "invalid_directory")
        for component in (root, *root.parents):
            try:
                mode = component.lstat().st_mode
            except FileNotFoundError:
                continue
            _require(stat.S_ISDIR(mode) and not stat.S_ISLNK(mode), "unsafe_directory")
    config = {key: service.config.get(key) for key in (
        "library.directory", "kobo_backup.directory", "kobo_backup.format",
        "kobo_backup.allow_unlinked_acquisition",
    )}
    return account, session, scope, _digest({"scope": scope, "roots": list(map(str, roots)), "config": config})


def _manifest(session):
    value = session.backup_manifest()
    _require(type(value) is list and len(value) <= 10000, "invalid_manifest")
    _require(kobo_backup._kobo_manifest_rejection_count(session) == 0, "invalid_manifest")
    # Copy plain JSON: provider mutations after preview cannot alter the plan.
    copied = json.loads(json.dumps(value, allow_nan=False))
    fingerprint = _digest(copied)
    for row in copied:
        _require(isinstance(row, dict) and type(row.get("product_id", row.get("id"))) is str
                 and bool(row.get("product_id", row.get("id")).strip())
                 and row.get("content_type", "ebook") in {"ebook", "book", "epub"}, "invalid_manifest")
        _require(isinstance(row, dict) and type(row.get("revision_id")) is str
                 and bool(row["revision_id"].strip()), "revision_unproven")
    return copied, fingerprint


def _evidence(service, account, manifest):
    # #3196 D2: a confirmation covering an edition already represented by
    # Core must not advertise the backup_items worker as an executor, even
    # while the provider ABI remains partial. The v0.7 source scope lacks
    # borrower/branch, so one typed edition blocks the whole legacy batch.
    # Match raw Kobo IDs exactly as the W2 manifest admission does. This
    # is read-only and rechecked against the live DB at confirmation.
    item_ids = {
        str(row.get("product_id") or row.get("id") or "").strip()
        for row in manifest
    }
    with service.storage._lock, service.storage._connect() as db:
        typed = db.execute(
            "SELECT edition_id FROM source_references WHERE "
            "provider_id='kobo' AND credential_id=? LIMIT 10001",
            (account.credential_id,),
        ).fetchall()
    if len(typed) > 10000:
        raise _Rejected("evidence_budget_exhausted")
    _require(not any(edition in item_ids for (edition,) in typed),
             "canonical_source_delivery_active")
    reasons = {}
    preview = kobo_backup.kobo_backup_preview(
        service, account.credential_id, refresh_manifest=True,
        _manifest_evidence=(manifest, 0), _item_reasons=reasons,
    )
    _require(preview["manifest_valid"], "invalid_manifest")
    counts = {key: preview["counts"][key] for key in _COUNTS}
    # Unrelated local books are diagnostic only; exact per-item candidates
    # and unsafe placements remain vetoes in the refreshed preview.
    _require(preview["blocking_items"] == 0, "reconciliation_required")
    roots = _roots(service, account.identity_id)
    backup = service.storage.backup_list("kobo", account.credential_id)
    _require(len(backup) <= 10000, "evidence_budget_exhausted")
    files = []
    for row in backup:
        _require(row.get("identity_id") == account.identity_id
                 and row.get("account_id") == account.unique_id, "source_scope_mismatch")
        _require(row.get("status") not in {"acquiring", "downloading", "interrupted"}, "acquisition_unresolved")
        path = row.get("local_path")
        if path:
            _require(_within(path, roots), "file_outside_identity")
            observed = _read_hash_within(path, roots)
            _require(observed is not None and observed[:2] == (str(row.get("sha256") or "").lower(), row.get("size")), "local_evidence_unverified")
            files.append((path, observed))
    tables = {}
    # A bounded snapshot of all local evidence for this source/identity. The
    # fingerprint stays private; no title, path, provider ID or token is logged.
    with service.storage._lock:
        db = service.storage._connect()
        # The Python lock protects only this process. A separate writer may
        # update the v0.7 projection, receipts or source binding between table
        # reads. One SQLite read snapshot prevents accepting a fingerprint
        # assembled from mutually incompatible generations. A SAVEPOINT also
        # works if the reusable thread-local connection is already in a
        # transaction; always release it even on an evidence-budget failure.
        db.execute("SAVEPOINT kobo_materialization_evidence")
        try:
            for table in ("backup_items", "entitlements", "acquisition_ledger", "provider_connections", "placements", "catalog_entries"):
                if table in {"placements", "catalog_entries"}:
                    where, args = "identity_id=?", (account.identity_id,)
                else:
                    where, args = "provider_id='kobo' AND credential_id=?", (account.credential_id,)
                cursor = db.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY 1", args)
                rows = cursor.fetchmany(10001)
                _require(len(rows) <= 10000, "evidence_budget_exhausted")
                tables[table] = rows
        finally:
            db.execute("RELEASE SAVEPOINT kobo_materialization_evidence")
    return _digest({"tables": tables, "reasons": reasons, "files": files}), counts




def _canonical_preview(service, account):
    """Provider A + exact Core B presence, with zero legacy writers/readers.

    This fingerprint is only a one-use user confirmation constraint. It is
    never an admission to GET or a substitute for SourceReference/OriginLink.
    The final Sync re-observes the resource, offer, consent and account under
    its own acquisition lock.
    """
    from .rights_snapshot_read import read_account_rights_snapshot
    from .convergence_verified_presence import plan_existing_verified_presence
    snapshot = read_account_rights_snapshot(service, account)
    _require(snapshot.status in {"success", "partial"}
             and snapshot.branch
             and snapshot.scope.identity_id == account.identity_id
             and snapshot.scope.provider_id == account.provider_id
             and snapshot.scope.credential_id == account.credential_id
             and snapshot.scope.account_id == account.unique_id,
             "canonical_observation_unavailable")
    # Only supplier-authenticated positives enter a canonical plan. A failed,
    # ambiguous or partially paginated source is never interpreted negatively.
    _require(all(row.provider_attested for row in snapshot.rights),
             "canonical_observation_unavailable")
    plan = plan_existing_verified_presence(
        service.storage, snapshot, library_root=service.library_dir(),
    )
    rows = sorted((
        row.edition_id, row.right_id, row.kind, row.state,
        row.provider_revision_id, row.evidence_kind,
        row.delivery, row.delivery_unavailable_reason,
    ) for row in snapshot.rights)
    fingerprint = _digest({
        "scope": [account.identity_id, account.provider_id,
                  account.credential_id, account.unique_id],
        "branch": snapshot.branch, "status": snapshot.status,
        "coverage": snapshot.coverage, "items": rows,
    })
    counts = {name: 0 for name in _COUNTS}
    counts["already_local_verified"] = sum(
        d.provider_satisfied for d in plan.decisions
    )
    counts["potentially_missing"] = sum(
        not d.provider_satisfied and d.state not in {
            "inactive", "skipped_archived",
        } for d in plan.decisions
    )
    counts["skipped_archived"] = sum(
        d.state == "skipped_archived" for d in plan.decisions
    )
    return fingerprint, counts


def _execute_canonical(broker, source_id, token, *, control=None):
    """Consume one typed preview, then invoke exactly ONE Core source Sync.

    A Core Sync owns its own acquisition lock. Releasing our preview lock
    before that call avoids recursive locks; its session/consent/offer/B
    publication admission is independent of this non-authoritative preview.
    """
    from .source_sync_consent import read_policy, scope_for_account
    from .source_context import source_context_epoch
    try:
        with InterProcessLock(acquisition_lock_file()):
            account, session, scope, configuration = _source(
                broker, source_id, allow_canonical=True,
            )
            _require(has_complete_delivery_abi(session),
                     "canonical_source_delivery_active")
            stored = broker._materialization_plans.take(token, scope)
            _require(type(stored[2]) is str
                     and stored[2].startswith("canonical:")
                     and type(stored[3]) is tuple and len(stored[3]) == 6
                     and stored[3][1] == "canonical",
                     "confirmation_unavailable")
            _require(
                stored[3][3] is session
                and stored[3][4] == source_context_epoch(broker.service)
                and stored[3][5] == getattr(session, "_epoch", None),
                "source_changed",
            )
            policy = read_policy(
                broker.service.storage, scope_for_account(account),
            )
            _require(policy.mode == "on"
                     and policy.revision == stored[3][2],
                     "canonical_consent_changed")
            _require(configuration == stored[3][0], "source_changed")
            fresh, _ = _canonical_preview(broker.service, account)
            _require(stored[2] == "canonical:" + fresh, "preview_changed")
            current = _source(broker, source_id, allow_canonical=True)
            _require(current[1] is session
                     and current[2:] == (scope, configuration),
                     "source_changed")
            _require(read_policy(
                broker.service.storage, scope_for_account(account)
            ).revision == policy.revision, "canonical_consent_changed")
    except LockBusyError:
        return _result("acquisition_busy")
    except _Rejected as exc:
        return _result(str(exc))
    except Exception:
        return _result("canonical_preflight_unavailable")

    try:
        # This broker method dispatches Core's canonical selected-offer
        # executor under a fresh lock; NEVER call the legacy W2 worker.
        report = broker.synchronize_source(source_id, control=control)
        execution = getattr(report, "facts", {}).get("canonical_execution")
        if (type(execution) is not dict
                or set(execution) != {
                    "downloaded", "recovered", "present_attested", "pending",
                    "blocked", "failed", "cancelled", "skipped_archived",
                    "inactive",
                }
                or any(type(n) is not int or n < 0 or n > 1_000_000
                       for n in execution.values())):
            # Successful inventory alone is not a completed delivery.
            return _result("canonical_execution_unavailable")
        downloaded = execution["downloaded"]
        status = ("success" if report.ok
                  and not any(execution[x] for x in (
                      "pending", "blocked", "failed", "cancelled",
                  )) else "partial")
        return _result(
            status, ok=status == "success", changed=downloaded,
            counts=execution, execution_path="canonical",
        )
    except Exception:
        return _result("canonical_execution_unavailable")


def prepare(broker, source_id):
    try:
        with InterProcessLock(acquisition_lock_file()):
            account, session, scope, configuration = _source(
                broker, source_id, allow_canonical=True,
            )
            if has_complete_delivery_abi(session):
                # Manual confirmation for a modern Kobo source must reuse the
                # same Core A->B executor as Sync, never the 0.7 backup writer.
                from .source_sync_consent import read_policy, scope_for_account
                from .source_context import source_context_epoch
                policy = read_policy(broker.service.storage, scope_for_account(account))
                _require(policy.mode == "on" and type(policy.revision) is str,
                         "canonical_source_delivery_active")
                core_epoch = source_context_epoch(broker.service)
                provider_epoch = getattr(session, "_epoch", None)
                fingerprint, counts = _canonical_preview(broker.service, account)
                current = _source(broker, source_id, allow_canonical=True)
                _require(current[1] is session and current[2:] == (scope, configuration)
                         and source_context_epoch(broker.service) == core_epoch
                         and getattr(session, "_epoch", None) == provider_epoch,
                         "source_changed")
                _require(read_policy(
                    broker.service.storage, scope_for_account(account)
                ).revision == policy.revision, "source_changed")
                token = broker._materialization_plans.put(
                    scope, "canonical:" + fingerprint,
                    (configuration, "canonical", policy.revision,
                     session, core_epoch, provider_epoch),
                )
                return _result(
                    "prepared", ok=True, counts=counts, confirmation_token=token,
                    expires_in_seconds=_TTL, requires_explicit_confirmation=True,
                    acquisition_authorized=False, manifest_refreshed=True,
                    execution_path="canonical",
                )
            manifest, manifest_hash = _manifest(session)
            evidence, counts = _evidence(broker.service, account, manifest)
            _require(_source(broker, source_id)[2:] == (scope, configuration), "source_changed")
            token = broker._materialization_plans.put(scope, manifest_hash, (configuration, evidence))
        return _result("prepared", ok=True, counts=counts, confirmation_token=token,
                       expires_in_seconds=_TTL, requires_explicit_confirmation=True,
                       acquisition_authorized=False, manifest_refreshed=True)
    except LockBusyError:
        return _result("acquisition_busy")
    except _Rejected as exc:
        return _result(str(exc))
    except Exception:
        return _result("preparation_unavailable")


def execute(broker, source_id, token, control=None):
    # A v2/v1 complete delivery ABI has a single Core A->B writer. An old
    # legacy token must not get a second 0.7 GET after an ABI upgrade.
    try:
        account, session, _, _ = _source(
            broker, source_id, allow_canonical=True,
        )
        if has_complete_delivery_abi(session):
            return _execute_canonical(broker, source_id, token, control=control)
    except _Rejected as exc:
        return _result(str(exc))
    except Exception:
        return _result("source_unavailable")
    downloaded = 0
    durable_downloaded = 0
    attempted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    service = broker.service
    running = False
    lock = None
    locked = False
    terminal = None
    completion_unknown = False

    def cancelled():
        return bool(control is not None and control.cancelled())

    def delivery_committed():
        nonlocal durable_downloaded
        # This private hook comes from the Core executor after a physically
        # inspected delivery and durable backup_put; it is never invoked by a
        # backup_items reader. Public delivery events remain checkpoint-gated.
        durable_downloaded += 1

    def progress(event):
        nonlocal downloaded
        if event.get("event") == "downloaded":
            downloaded += 1
        # No provider strings or private event payload cross the public API.
        if control is not None:
            control.report(current=downloaded, message="Téléchargement des livres manquants")

    def finish(status, *, ok=False, counts=None):
        nonlocal terminal, downloaded, completion_unknown
        # #3196 D2: a changed legacy backup_items.complete row is NOT a
        # completed acquisition. Count only Core-owned delivery events while
        # running and the validated executor report on normal return. On a
        # crash or missing report, keep completion_unknown rather than
        # manufacturing a positive download from a 0.7 row/SHA/path.
        downloaded = max(downloaded, durable_downloaded)
        terminal = _result(status, ok=ok, changed=downloaded, counts=counts or {})
        if completion_unknown:
            terminal.facts["completion_unknown"] = True
        return terminal

    try:
        lock = InterProcessLock(acquisition_lock_file())
        lock.acquire()
        locked = True
        account, session, scope, configuration = _source(broker, source_id)
        plan = broker._materialization_plans.take(token, scope)
        if cancelled():
            return finish("cancelled")
        manifest, manifest_hash = _manifest(session)
        _require(manifest_hash == plan[2], "manifest_changed")
        evidence, _counts = _evidence(service, account, manifest)
        _require((configuration, evidence) == plan[3], "preview_changed")

        def guard():
            current_account, current_session, current_scope, current_configuration = _source(broker, source_id)
            _require(current_scope == scope and current_configuration == configuration
                     and current_session is session, "source_changed")

        revisions = {row.get("product_id", row.get("id")).strip(): row["revision_id"].strip() for row in manifest}

        def guard_item(item, product_id, revision_id):
            guard()
            _require(item.source_provider == account.provider_id
                     and item.source_credential == account.credential_id
                     and item.record_id == product_id and item.revision_id == revision_id
                     and revisions.get(product_id) == revision_id
                     and item.owned is True, "normalized_item_mismatch")

        guard()
        broker._set_materialization_state(source_id, "running", attempted_at)
        running = True
        report = kobo_backup._sync_kobo_backup_locked(
            service, logger=_LOG, credential_id=account.credential_id,
            confirmed_manifest=manifest, source_guard=guard, item_guard=guard_item,
            cancelled=cancelled, progress=progress,
            on_delivery_committed=delivery_committed,
        )
        sources = report.get("sources", [])
        state = sources[0].get("status") if len(sources) == 1 else "unknown"
        status = {"complete": "success", "attention_required": "blocked", "partial": "partial",
                  "failed": "failed", "aborted": "failed", "cancelled": "cancelled"}.get(state, "partial")
        counts = {}
        for key in ("downloaded", "unchanged", "skipped", "skipped_archived", "errors", "pending", "attention_required"):
            value = report.get(key)
            _require(type(value) is int and 0 <= value <= 1000000, "invalid_result")
            counts[key] = value
        downloaded = counts["downloaded"]
        return finish(status, ok=status == "success", counts=counts)
    except LockBusyError:
        return finish("acquisition_busy")
    except _Rejected as exc:
        return finish(str(exc))
    except Exception:
        completion_unknown = running
        return finish("partial" if downloaded else "interrupted" if running else "materialization_failed")
    finally:
        if running:
            try:
                status = terminal.facts["status"] if terminal else "interrupted"
                if status not in {"success", "partial", "blocked", "failed", "cancelled", "interrupted"}:
                    status = "partial" if downloaded else "failed"
                broker._set_materialization_state(source_id, status,
                                                 attempted_at)
                broker._notify()
            except Exception:
                # A failed final status write must not hide committed files.
                if terminal is not None:
                    terminal.facts["state_persistence_failed"] = True

        if locked:
            try:
                lock.release()
            except Exception:
                if terminal is not None:
                    terminal.facts["lock_cleanup_failed"] = True
