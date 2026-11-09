"""Explicit source confirmation, isolated from default metadata synchronization.

The temporary Kobo adapter reuses the existing preflight/executor policy. Plans
are bounded, memory-only and one-use; losing a process requires a new preview.
Neither advisory previews nor persisted counters are acquisition permissions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time

from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file
from ..plugin_api import SyncResult
from .canonical_delivery_abi import has_complete_delivery_abi
from .kobo_local_reconciliation import _roots

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
    # Kobo's exact primary borrower has account_id="" (not None). Only
    # this explicitly primary account may use the empty upstream ID; every
    # other scope coordinate still has to be a non-empty exact string.
    # This is not entitlement or GET admission: canonical Sync independently
    # rechecks A, the persisted account binding and CAS consent.
    _require(
        all(type(value) is str and len(value) <= 1024
            and not any(c in value for c in "\0\n\r") for value in scope)
        and all(scope[:4])
        and (bool(account.unique_id) or account.is_primary is True)
        and (not account.unique_id
             or account.unique_id == account.unique_id.strip()),
        "invalid_source",
    )
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
    # The session selected by provider+credential must also be the exact
    # registered session, not a stale alias surviving a reconnect. A source
    # manifest belongs to its issuing borrower, not merely to an upstream
    # account_id that can be reused under another profile or provider.
    _require(service._sessions.get(
        service._session_key("kobo", account.credential_id)
    ) is session, "source_changed")
    session_accounts = session.accounts
    _require(
        len(session_accounts) == 1
        and session_accounts[0].provider_id == "kobo"
        and session_accounts[0].credential_id == account.credential_id
        and session_accounts[0].unique_id == account.unique_id
        # Empty Kobo borrower is a valid *primary* source only. The
        # upstream adapter may rebind an empty secondary under the same
        # credential; never issue a manual confirmation on that alias.
        and (account.unique_id != ""
             or session_accounts[0].is_primary is True)
        # Kobo-Vault's legacy Account model uses "default" as an adapter
        # identity label even for a named Core profile; the actual owner is
        # independently pinned by identity_for_connection above. Only this
        # known adapter label may differ from the selected Core identity.
        and session_accounts[0].identity_id in {
            account.identity_id, "default",
        },
        "ambiguous_account",
    )
    _require(sum(a.provider_id == "kobo" and a.credential_id == account.credential_id
                 for a in service.accounts) == 1, "ambiguous_account")
    if not has_complete_delivery_abi(session):
        # A configured source-wide CAS policy, even OFF or unreadable,
        # supersedes the old manually confirmed W2 backup grant. Even
        # allow_canonical=True cannot skip this when prepare first probes
        # a partial ABI; refuse before reading its provider manifest.
        from .source_sync_consent import read_policy, scope_for_account
        try:
            legacy_unset = (
                read_policy(service.storage, scope_for_account(account)).mode == "unset"
            )
        except Exception:
            legacy_unset = False
        _require(legacy_unset, "canonical_source_policy_configured")
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
                     # A valid legacy confirmation cannot become a Core A->B
                     # permit merely because this plugin gained an ABI.
                     "canonical_source_delivery_active")
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
            # #4858 D2: legacy 0.7 cannot issue confirmation tokens. A
            # partial ABI is not a delivery contract, even when the account
            # or an old local receipt looks valid. No manifest read follows.
            return _result("canonical_delivery_unavailable")
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
    # A partial ABI cannot redeem an old confirmation token or touch W2.
    return _result("canonical_delivery_unavailable")
