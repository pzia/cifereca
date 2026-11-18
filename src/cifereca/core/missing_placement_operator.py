"""#2847: private, revocable Core operator session for missing Placements.

This is NOT a plugin capability or a GTK action. The CoreUI caller retains the
session and provides its active identity/SQLite-stamp validity predicate,
plus proof that the scoped inspection actually completed.
An opaque plan token never leaves this object. An explicit second action and
the acquisition lock are required for either SQLite-only repair.
"""
from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Callable

from ..locking import InterProcessLock, LockBusyError
from . import canonical_recovery
from .library_audit_roots import configured_identity_roots
from .library_sqlite_freshness import sqlite_source_stamp
from .source_context import source_context_epoch
from .missing_placement_relocation_preview import preview_missing_placement_relocation
from .missing_placement_relocation_transaction import relocate_missing_placement
from .missing_placement_retirement import (
    prepare_missing_placement_retirement, retire_missing_placement,
)
from .missing_placement_same_path import (
    preview_reappeared_missing_placement, restore_reappeared_missing_placement,
)
from .missing_placement_linked_same_path import (
    preview_reappeared_linked_placement, restore_reappeared_linked_placement,
)
from .missing_placement_catalogue_detachment import (
    prepare_missing_catalogue_detachment, retire_missing_linked_catalogue,
)


def _public(result: object, *, preview: bool) -> dict[str, object]:
    if type(result) is not dict:
        return {"status": "blocked", "reason": "unavailable", "read_only": True}
    status = result.get("status")
    if type(status) is not str or status not in {
        "blocked", "ready", "review_required", "relocated", "retired",
        "restored",
    }:
        return {"status": "blocked", "reason": "unavailable", "read_only": True}
    outcome: dict[str, object] = {
        "status": status,
        "read_only": status not in {"relocated", "retired", "restored"},
    }
    reason = result.get("reason")
    # Caller-facing reasons are bounded fixed Core codes, never SQL errors,
    # private paths, title, receipt, account, token or exception messages.
    if type(reason) is str and 0 < len(reason) <= 80 and all(
        c.isascii() and (c.islower() or c.isdigit() or c == "_")
        for c in reason
    ):
        outcome["reason"] = reason
    # Direct reference counts are UI diagnostics, *not* evidence that the
    # unknown operational graph is free of dependencies. Blocked retirement
    # has no confirmation token and never turns into a writable action.
    if preview and (
        status in {"ready", "review_required"}
        or (status == "blocked" and reason == "dependent_references")
    ):
        if status != "blocked":
            outcome["requires_explicit_confirmation"] = True
        for key in ("catalogue_rows", "backup_rows"):
            count = result.get(key)
            if type(count) is int and 0 <= count <= 128:
                outcome[key] = count
    if status in {"relocated", "restored"}:
        count = result.get("catalogue_rows")
        if type(count) is int and 0 <= count <= 128:
            outcome["catalogue_rows"] = count
    return outcome


def _configured_root(config):
    configured = str(config.get("library.directory", "~/EBooks") or "~/EBooks").strip()
    raw = Path(os.path.expandvars(os.path.expanduser(configured)))
    root = raw if raw.is_absolute() else Path.home() / raw
    return root if root.is_absolute() else None


class _AdmittedMissingRepair:
    """Private one-shot scope; navigation is not a source revocation."""

    def __init__(self, service, identity_id, root):
        self.service, self.identity_id, self.root = service, identity_id, root
        self.owner = service.current_identity
        if self.owner is None or self.owner.identity_id != identity_id:
            raise ValueError("scope_changed")
        self.directory = self.owner.directory_name or identity_id
        self.config, self.storage = service.config, service.storage
        self.data = self.config.data
        self.identities, self.accounts = service.identities, service.accounts
        self.registry = service.registry
        self.path = Path(self.storage.path)
        self.roots = configured_identity_roots(self.config, identity_id)
        self.epoch = source_context_epoch(service)
        self.stamp = sqlite_source_stamp(self.path)

    def current(self, *, initial=True):
        try:
            s = self.service
            return (
                s.storage is self.storage and Path(self.storage.path) == self.path
                and s.config is self.config and self.config.data is self.data
                and s.identities is self.identities and s.accounts is self.accounts
                and s.registry is self.registry
                and source_context_epoch(s) == self.epoch
                and self.owner.identity_id == self.identity_id
                and (self.owner.directory_name or self.identity_id) == self.directory
                and sum(i.identity_id == self.identity_id for i in self.identities) == 1
                and any(i is self.owner for i in self.identities)
                and _configured_root(self.config) == self.root
                and configured_identity_roots(self.config, self.identity_id) == self.roots
                and self.stamp is not None
                and (not initial or sqlite_source_stamp(self.path) == self.stamp)
            )
        except Exception:
            return False

    def in_transaction(self, db, *, initial):
        # BEGIN IMMEDIATE protects the original SQLite image. The final guard
        # omits the stamp because our own uncommitted writes may change WAL.
        try:
            return (self.current(initial=initial)
                    and db.execute(
                        "SELECT directory_name FROM identities WHERE identity_id=?",
                        (self.identity_id,),
                    ).fetchone() == (self.directory,))
        except Exception:
            return False


class MissingPlacementOperatorSession:
    """Revocable one-identity repair dialogue; private plan never serialized.

    Instantiate only from CoreUI's active scoped inspection. The predicate
    must check both the active identity and its SQLite/WAL audit stamp. On
    selection change, cancel() discards the previous plan before any write.
    """

    def __init__(self, service, *, identity_id: str,
                 scope_valid: Callable[[], bool],
                 inspection_complete: bool = False) -> None:
        if (type(identity_id) is not str or not identity_id
                or len(identity_id) > 256 or not callable(scope_valid)):
            raise ValueError("invalid_missing_placement_operator_scope")
        self._service = service
        self._identity_id = identity_id
        self._scope_valid = scope_valid
        # A partial inventory cannot authorize a destructive SQLite repair.
        # The CoreUI passes this only from a fully completed scoped audit.
        self._inspection_complete = inspection_complete is True
        self._pending: tuple[str, str, Path | None, Path, str] | None = None
        self._closed = False

    def cancel(self) -> None:
        self._pending = None

    def close(self) -> None:
        self.cancel()
        self._closed = True
        self._service = None

    def _root(self) -> Path | None:
        if self._closed or self._service is None:
            return None
        try:
            if not self._scope_valid():
                self.cancel()
                return None
            current = getattr(self._service, "current_identity", None)
            if getattr(current, "identity_id", None) != self._identity_id:
                self.cancel()
                return None
            if self._service.identity_by_id(self._identity_id) is None:
                self.cancel()
                return None
            # Match LibraryService.library_dir() configuration *without*
            # calling it: that method creates an absent mount as a directory.
            return _configured_root(self._service.config)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            self.cancel()
            return None

    def preview_same_path_restoration(self, *, placement_id: str) -> dict[str, object]:
        """One orphan, exact old filename, independent physical SHA proof."""
        self.cancel()
        if not self._inspection_complete:
            return _public(
                {"status": "blocked", "reason": "incomplete_inspection"},
                preview=True,
            )
        root = self._root()
        if root is None:
            return _public(
                {"status": "blocked", "reason": "scope_changed"},
                preview=True,
            )
        result = preview_reappeared_missing_placement(
            self._service.storage, placement_id=placement_id,
            identity_id=self._identity_id, library_root=root,
        )
        token = result.get("plan_token")
        if (result.get("status") == "ready" and type(token) is str
                and len(token) == 64 and self._root() == root):
            self._pending = ("restore", placement_id, None, root, token)
        else:
            self.cancel()
            if result.get("status") == "ready":
                return _public(
                    {"status": "blocked", "reason": "scope_changed"},
                    preview=True,
                )
        return _public(result, preview=True)

    def preview_linked_same_path_restoration(
        self, *, placement_id: str,
    ) -> dict[str, object]:
        """Re-attest returned bytes and exact linked catalogue state only."""
        self.cancel()
        if not self._inspection_complete:
            return _public(
                {"status": "blocked", "reason": "incomplete_inspection"},
                preview=True,
            )
        root = self._root()
        if root is None:
            return _public(
                {"status": "blocked", "reason": "scope_changed"},
                preview=True,
            )
        result = preview_reappeared_linked_placement(
            self._service.storage, placement_id=placement_id,
            identity_id=self._identity_id, library_root=root,
        )
        token = result.get("plan_token")
        if (result.get("status") == "ready" and type(token) is str
                and len(token) == 64 and self._root() == root):
            self._pending = ("restore_linked", placement_id, None, root, token)
        else:
            self.cancel()
            if result.get("status") == "ready":
                return _public(
                    {"status": "blocked", "reason": "scope_changed"},
                    preview=True,
                )
        return _public(result, preview=True)

    def preview_retirement(self, *, placement_id: str) -> dict[str, object]:
        self.cancel()
        if not self._inspection_complete:
            return _public({"status": "blocked", "reason": "incomplete_inspection"},
                           preview=True)
        root = self._root()
        if root is None:
            return _public({"status": "blocked", "reason": "scope_changed"}, preview=True)
        result = prepare_missing_placement_retirement(
            self._service.storage, placement_id=placement_id,
            identity_id=self._identity_id, library_root=root,
        )
        token = result.get("plan_token")
        if (result.get("status") == "ready" and type(token) is str
                and len(token) == 64 and self._root() == root):
            self._pending = ("retire", placement_id, None, root, token)
        else:
            self.cancel()
            if result.get("status") == "ready":
                return _public({"status": "blocked", "reason": "scope_changed"}, preview=True)
        return _public(result, preview=True)

    def preview_linked_catalogue_retirement(
        self, *, placement_id: str,
    ) -> dict[str, object]:
        """Review only a source-compatible, directly linked local projection.

        Never interpret a generic dependent_references count as authority.
        Only the specialized strict Core preparer may produce this plan.
        """
        self.cancel()
        if not self._inspection_complete:
            return _public(
                {"status": "blocked", "reason": "incomplete_inspection"},
                preview=True,
            )
        root = self._root()
        if root is None:
            return _public(
                {"status": "blocked", "reason": "scope_changed"}, preview=True,
            )
        result = prepare_missing_catalogue_detachment(
            self._service.storage, placement_id=placement_id,
            identity_id=self._identity_id, library_root=root,
        )
        token = result.get("plan_token")
        if (result.get("status") == "review_required"
                and type(token) is str and len(token) == 64
                and self._root() == root):
            self._pending = ("linked_retire", placement_id, None, root, token)
        else:
            self.cancel()
            if result.get("status") == "review_required":
                return _public(
                    {"status": "blocked", "reason": "scope_changed"}, preview=True,
                )
        return _public(result, preview=True)

    def preview_relocation(self, *, placement_id: str,
                           candidate_path: Path) -> dict[str, object]:
        self.cancel()
        if not self._inspection_complete:
            return _public({"status": "blocked", "reason": "incomplete_inspection"},
                           preview=True)
        root = self._root()
        if root is None:
            return _public({"status": "blocked", "reason": "scope_changed"}, preview=True)
        result = preview_missing_placement_relocation(
            self._service.storage, placement_id=placement_id,
            identity_id=self._identity_id, library_root=root,
            candidate_path=candidate_path,
        )
        token = result.get("plan_token")
        if (result.get("status") == "review_required" and type(token) is str
                and len(token) == 64 and self._root() == root):
            self._pending = ("relocate", placement_id, candidate_path, root, token)
        else:
            self.cancel()
            if result.get("status") == "review_required":
                return _public({"status": "blocked", "reason": "scope_changed"}, preview=True)
        return _public(result, preview=True)

    def admit_confirmation(self, *, user_confirmed=False):
        """Consume the private preview at the human click, before queueing."""
        pending, self._pending = self._pending, None
        if user_confirmed is not True or pending is None or not self._inspection_complete:
            return None
        root = pending[3]
        if self._root() != root:
            return None
        try:
            admitted = _AdmittedMissingRepair(self._service, self._identity_id, root)
            if not admitted.current() or self._root() != root:
                return None
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return None
        lock, consumed = Lock(), False

        def work():
            nonlocal consumed
            with lock:
                if consumed:
                    return _public({"status": "blocked", "reason": "confirmation_required"},
                                   preview=False)
                consumed = True
            return _execute_pending(
                admitted.storage, admitted.identity_id, pending, admitted.current,
                admitted_scope=admitted.in_transaction,
            )

        return work

    def confirm(self, *, user_confirmed: bool = False) -> dict[str, object]:
        pending, self._pending = self._pending, None
        if user_confirmed is not True or pending is None:
            return _public({"status": "blocked", "reason": "confirmation_required"},
                           preview=False)
        root = pending[3]
        if self._root() != root:
            return _public({"status": "blocked", "reason": "scope_changed"}, preview=False)
        return _execute_pending(
            self._service.storage, self._identity_id, pending,
            lambda: self._root() == root,
        )


def _execute_pending(storage, identity_id, pending, scope_current, *, admitted_scope=None):
    guard = {} if admitted_scope is None else {"admitted_scope": admitted_scope}
    operation, placement_id, candidate, root, token = pending
    if not scope_current():
        return _public({"status": "blocked", "reason": "scope_changed"}, preview=False)
    try:
        with InterProcessLock(canonical_recovery.acquisition_lock_file()) as locked:
            # Identity/session may have changed while another writer owned
            # the acquisition lock. Never use a former operator's token.
            if not scope_current():
                return _public({"status": "blocked", "reason": "scope_changed"},
                               preview=False)
            if operation == "restore":
                result = restore_reappeared_missing_placement(
                    storage, placement_id=placement_id,
                    identity_id=identity_id, library_root=root,
                    plan_token=token, confirmed=True, shared_lock=locked, **guard,
                )
            elif operation == "restore_linked":
                result = restore_reappeared_linked_placement(
                    storage, placement_id=placement_id,
                    identity_id=identity_id, library_root=root,
                    plan_token=token, confirmed=True, shared_lock=locked, **guard,
                )
            elif operation == "retire":
                result = retire_missing_placement(
                    storage, placement_id=placement_id,
                    identity_id=identity_id, library_root=root,
                    plan_token=token, confirmed=True, **guard,
                )
            elif operation == "linked_retire":
                result = retire_missing_linked_catalogue(
                    storage, placement_id=placement_id,
                    identity_id=identity_id, library_root=root,
                    plan_token=token, confirmed=True, shared_lock=locked, **guard,
                )
            elif operation == "relocate" and candidate is not None:
                result = relocate_missing_placement(
                    storage, placement_id=placement_id,
                    identity_id=identity_id, library_root=root,
                    candidate_path=candidate, plan_token=token,
                    confirmed=True, shared_lock=locked, **guard,
                )
            else:
                return _public({"status": "blocked", "reason": "invalid_plan"},
                               preview=False)
    except (LockBusyError, OSError, RuntimeError):
        return _public({"status": "blocked", "reason": "acquisition_busy"},
                       preview=False)
    return _public(result, preview=False)
