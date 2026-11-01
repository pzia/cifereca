"""#2847: private, revocable Core operator session for missing Placements.

This is NOT a plugin capability or a GTK action. The CoreUI caller retains the
session and provides its active identity/SQLite-stamp validity predicate.
An opaque plan token never leaves this object. An explicit second action and
the acquisition lock are required for either SQLite-only repair.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from ..locking import InterProcessLock, LockBusyError
from . import canonical_recovery
from .missing_placement_relocation_preview import preview_missing_placement_relocation
from .missing_placement_relocation_transaction import relocate_missing_placement
from .missing_placement_retirement import (
    prepare_missing_placement_retirement, retire_missing_placement,
)


def _public(result: object, *, preview: bool) -> dict[str, object]:
    if type(result) is not dict:
        return {"status": "blocked", "reason": "unavailable", "read_only": True}
    status = result.get("status")
    if type(status) is not str or status not in {
        "blocked", "ready", "review_required", "relocated", "retired",
    }:
        return {"status": "blocked", "reason": "unavailable", "read_only": True}
    outcome: dict[str, object] = {
        "status": status,
        "read_only": status not in {"relocated", "retired"},
    }
    reason = result.get("reason")
    # Caller-facing reasons are bounded fixed Core codes, never SQL errors,
    # private paths, title, receipt, account, token or exception messages.
    if type(reason) is str and 0 < len(reason) <= 80 and all(
        c.isascii() and (c.islower() or c.isdigit() or c == "_")
        for c in reason
    ):
        outcome["reason"] = reason
    if preview and status in {"ready", "review_required"}:
        outcome["requires_explicit_confirmation"] = True
        for key in ("catalogue_rows", "backup_rows"):
            count = result.get(key)
            if type(count) is int and 0 <= count <= 128:
                outcome[key] = count
    if status == "relocated":
        count = result.get("catalogue_rows")
        if type(count) is int and 0 <= count <= 128:
            outcome["catalogue_rows"] = count
    return outcome


class MissingPlacementOperatorSession:
    """Revocable one-identity repair dialogue; private plan never serialized.

    Instantiate only from CoreUI's active scoped inspection. The predicate
    must check both the active identity and its SQLite/WAL audit stamp. On
    selection change, cancel() discards the previous plan before any write.
    """

    def __init__(self, service, *, identity_id: str,
                 scope_valid: Callable[[], bool]) -> None:
        if (type(identity_id) is not str or not identity_id
                or len(identity_id) > 256 or not callable(scope_valid)):
            raise ValueError("invalid_missing_placement_operator_scope")
        self._service = service
        self._identity_id = identity_id
        self._scope_valid = scope_valid
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
            configured = str(
                self._service.config.get("library.directory", "~/EBooks")
                or "~/EBooks"
            ).strip()
            raw = Path(os.path.expandvars(os.path.expanduser(configured)))
            root = raw if raw.is_absolute() else Path.home() / raw
            return root if root.is_absolute() else None
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            self.cancel()
            return None

    def preview_retirement(self, *, placement_id: str) -> dict[str, object]:
        self.cancel()
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

    def preview_relocation(self, *, placement_id: str,
                           candidate_path: Path) -> dict[str, object]:
        self.cancel()
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

    def confirm(self, *, user_confirmed: bool = False) -> dict[str, object]:
        pending, self._pending = self._pending, None
        if user_confirmed is not True or pending is None:
            return _public({"status": "blocked", "reason": "confirmation_required"},
                           preview=False)
        operation, placement_id, candidate, root, token = pending
        if self._root() != root:
            return _public({"status": "blocked", "reason": "scope_changed"}, preview=False)
        try:
            with InterProcessLock(canonical_recovery.acquisition_lock_file()) as locked:
                # Identity/session may have changed while another writer owned
                # the acquisition lock. Never use a former operator's token.
                if self._root() != root:
                    return _public({"status": "blocked", "reason": "scope_changed"},
                                   preview=False)
                if operation == "retire":
                    result = retire_missing_placement(
                        self._service.storage, placement_id=placement_id,
                        identity_id=self._identity_id, library_root=root,
                        plan_token=token, confirmed=True,
                    )
                elif operation == "relocate" and candidate is not None:
                    result = relocate_missing_placement(
                        self._service.storage, placement_id=placement_id,
                        identity_id=self._identity_id, library_root=root,
                        candidate_path=candidate, plan_token=token,
                        confirmed=True, shared_lock=locked,
                    )
                else:
                    return _public({"status": "blocked", "reason": "invalid_plan"},
                                   preview=False)
        except (LockBusyError, OSError, RuntimeError):
            return _public({"status": "blocked", "reason": "acquisition_busy"},
                           preview=False)
        return _public(result, preview=False)
