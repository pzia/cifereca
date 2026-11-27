"""P1 #4985: conservative Core broker for explicit, one-book USB operations.

The GTK layer never receives USB file descriptors or writes to the device.
The broker reattests the selected identity, binding, destination and exact
Core delta, then delegates to the existing locked Core executor. Local
placements are admissible without a separate provider rights policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from ..locking import InterProcessLock
from .device_usb_copy_admission import UsbCopyConsent
from .device_usb_dry_run import UsbDryRun, inspect_usb_dry_run
from .device_usb_mount import LinuxUsbMount, observe_linux_usb_mount
from .device_usb_preview import UsbPreviewRow
from .device_usb_removal_review import UsbRemovalConsent
from .device_usb_sync_engine import UsbSyncResult, synchronize_usb_reader
from .library_audit_roots import configured_identity_roots


@dataclass(frozen=True, slots=True)
class _ReaderScope:
    identity: object
    identity_id: str
    epoch: int
    device_id: str
    fingerprint: str
    identity_root: Path
    binding: dict[str, Any]


@dataclass(slots=True)
class ReaderUsbExecutionBrokerImpl:
    """Trust-restricted application boundary; never a storage.target driver."""

    service: Any

    def for_plugin(self, plugin_id: str, *, trusted: bool = False):
        # A third-party plugin may not invoke a destructive device operation,
        # even with a forged short-lived user confirmation dataclass.
        return self if trusted is True and type(plugin_id) is str else None

    @staticmethod
    def _unknown(identity_id: str, device_id: str) -> UsbDryRun:
        return UsbDryRun(
            identity_id if type(identity_id) is str else "",
            device_id if type(device_id) is str else "",
            "unavailable", False, None, (),
        )

    def _scope(self, identity_id: str, device_id: str, *,
               admitted: _ReaderScope | None = None) -> _ReaderScope | None:
        service = self.service
        if (type(identity_id) is not str or not identity_id
                or type(device_id) is not str or not device_id):
            return None
        try:
            identity = (service.current_identity if admitted is None
                        else admitted.identity)
            epoch = (service._account_context_epoch if admitted is None
                     else admitted.epoch)
            if (identity is None or getattr(identity, "identity_id", None) != identity_id
                    or type(epoch) is not int or epoch < 0):
                return None
            roots = configured_identity_roots(service.config, identity_id).get(identity_id)
            if (type(roots) is not tuple or not roots
                    or not isinstance(roots[0], Path)
                    or not roots[0].is_absolute()):
                return None
            storage = service.storage
            binding = storage.device_binding_get(device_id)
            meta = binding.get("metadata") if type(binding) is dict else None
            fingerprint = meta.get("usb_fingerprint") if type(meta) is dict else None
            if (type(binding) is not dict
                    or binding.get("identity_id") != identity_id
                    or binding.get("device_id") != device_id
                    or binding.get("enabled") is not True
                    or type(fingerprint) is not str
                    or not 8 <= len(fingerprint) <= 128
                    or (admitted is None and (
                        getattr(service, "current_identity", None) is not identity
                        or service._account_context_epoch != epoch))):
                return None
            # Verify the UUID is uniquely bound across profiles, not just
            # inside the selected reader's profile-scoped binding list.
            bindings = storage.device_bindings()
            if (type(bindings) is not list or len(bindings) > 4096
                    or sum(
                        type(row) is dict
                        and type(row.get("metadata")) is dict
                        and type(row["metadata"].get("usb_fingerprint")) is str
                        and row["metadata"]["usb_fingerprint"].casefold()
                            == fingerprint.casefold()
                        for row in bindings
                    ) != 1):
                return None
            if meta.get("reader_transport") == "local":
                local_root = Path(meta.get("local_root", ""))
                if not local_root.is_absolute():
                    return None
                for row in storage.device_bindings():
                    if row.get("device_id") == device_id:
                        continue
                    other = row.get("metadata") or {}
                    if other.get("reader_transport") == "local":
                        path = Path(other["local_root"])
                        if local_root.is_relative_to(path) or path.is_relative_to(local_root):
                            return None
                for owner in service.config.identities():
                    for path in configured_identity_roots(service.config, owner["id"])[owner["id"]]:
                        if local_root.is_relative_to(path) or path.is_relative_to(local_root):
                            return None
            return _ReaderScope(identity, identity_id, epoch,
                                device_id, fingerprint, roots[0], binding)
        except Exception:
            return None

    def _current(self, scope: _ReaderScope, *, admitted=False) -> bool:
        try:
            fresh = self._scope(scope.identity_id, scope.device_id,
                                admitted=scope if admitted else None)
            return (
                type(fresh) is _ReaderScope
                and fresh.identity is scope.identity
                and fresh.epoch == scope.epoch
                and fresh.identity_root == scope.identity_root
                and fresh.fingerprint == scope.fingerprint
                and fresh.binding == scope.binding
            )
        except Exception:
            return False

    def _mount_attested(self, scope: _ReaderScope, mountpoint: Path, *,
                        scope_current=None, mount_key=None) -> bool:
        current = scope_current or (lambda: self._current(scope))
        if (not isinstance(mountpoint, Path) or not mountpoint.is_absolute()
                or mountpoint == Path("/") or ".." in mountpoint.parts
                or not current()):
            return False
        try:
            proof = observe_linux_usb_mount(
                identity_id=scope.identity_id, binding=scope.binding,
                mountpoint=mountpoint,
            )
            return (type(proof) is LinuxUsbMount
                    and proof.identity_id == scope.identity_id
                    and proof.device_id == scope.device_id
                    and proof.usb_fingerprint == scope.fingerprint
                    and proof.mountpoint == mountpoint
                    and (mount_key is None or proof.scope_key()[2:] == mount_key)
                    and current())
        except Exception:
            return False

    def completed_copy_operation(
        self, *, identity_id: str, device_id: str, artifact_id: str,
    ) -> str | None:
        """Advisory exact finished-copy ID for a selected withdrawal.

        Only Core reads the canonical operation journal. This method does not
        probe the filesystem, construct a consent, or attest presence: the
        separate physical dry-run and the locked executor do that later.
        Dependency on #6295 is optional until merged; missing resolver is a
        closed negative, never a guessed operation ID.
        """
        from . import device_usb_operation_journal as journal
        from .device_usb_preview import _relative, _sha

        scope = self._scope(identity_id, device_id)
        if (scope is None or type(artifact_id) is not str
                or not artifact_id.startswith("sha256:")
                or not _sha(artifact_id[7:])):
            return None
        resolver = getattr(journal, "completed_usb_managed_copy_operations", None)
        if not callable(resolver):
            return None

        def intention():
            row = self.service.storage.device_placement_get(device_id, artifact_id)
            return row if (
                type(row) is dict
                and row.get("device_id") == device_id
                and row.get("artifact_id") == artifact_id
                and row.get("desired_state") == "remove"
                and type(row.get("metadata")) is dict
                and row["metadata"].get("intent_identity_id") == identity_id
                and _relative(row.get("target_relative_path"))
            ) else None

        try:
            before = intention()
            if before is None:
                return None
            receipts = journal.completed_usb_managed_receipts(
                storage=self.service.storage, identity_id=identity_id,
                device_id=device_id, fingerprint=scope.fingerprint,
            )
            receipt = receipts.get(artifact_id) if type(receipts) is dict else None
            if (receipt is None
                    or receipt.relative_path != before["target_relative_path"]
                    or receipt.sha256 != artifact_id[7:]):
                return None
            operations = resolver(
                storage=self.service.storage, identity_id=identity_id,
                device_id=device_id, fingerprint=scope.fingerprint,
            )
            if type(operations) is not dict or len(operations) > 1000:
                return None
            operation = operations.get(artifact_id)
            if (type(operation) is not str or len(operation) != 32
                    or any(ch not in "0123456789abcdef" for ch in operation)
                    or intention() != before or not self._current(scope)):
                return None
            return operation
        except Exception:
            return None

    def preview(self, *, identity_id: str, device_id: str,
                mountpoint: Path) -> UsbDryRun:
        """Explicit read-only worker entry, no implicit device discovery."""
        unknown = self._unknown(identity_id, device_id)
        scope = self._scope(identity_id, device_id)
        if scope is None or not self._mount_attested(scope, mountpoint):
            return unknown
        try:
            report = inspect_usb_dry_run(
                storage=self.service.storage, identity_id=identity_id,
                device_id=device_id, identity_root=scope.identity_root,
                mountpoint=mountpoint,
            )
            if (not self._current(scope)
                    or type(report) is not UsbDryRun
                    or report.identity_id != identity_id
                    or report.device_id != device_id
                    or report.executable is not False):
                return unknown
            return report
        except Exception:
            return unknown

    def admit_removal(
        self, *, confirmation: UsbRemovalConsent, mountpoint: Path,
        expected_profile_epoch: int, mount_key: tuple,
    ) -> Callable[[], UsbSyncResult]:
        """Capture the second, destructive confirmation locally before enqueue.

        No mount scan or file I/O on the UI thread. The returned one-use
        worker retains the physical generation, exact managed-copy ID and
        durable authority. Navigation is not business revocation.
        """
        from .source_context import source_context_epoch
        from .device_usb_preview import _relative, _sha
        from .device_usb_copy_admission import _MAX_AGE
        from .device_usb_removal_review import _OPERATION_ID

        service = self.service
        storage, config = service.storage, service.config
        manager = getattr(service, "_plugin_manager", None)
        registry_generation = getattr(manager, "registry_generation", -1)
        epoch = source_context_epoch(service)
        now = int(time.time())
        if (type(confirmation) is not UsbRemovalConsent
                or type(expected_profile_epoch) is not int
                or expected_profile_epoch < 0
                or type(confirmation.confirmed_at) is not int
                or type(confirmation.expires_at) is not int
                or not 0 <= now - confirmation.confirmed_at <= _MAX_AGE
                or not now < confirmation.expires_at <= confirmation.confirmed_at + _MAX_AGE
                or not _sha(confirmation.sha256)
                or confirmation.artifact_id != "sha256:" + confirmation.sha256
                or not _relative(confirmation.target_relative_path)
                or any(type(value) is not str or _OPERATION_ID.fullmatch(value) is None
                       for value in (confirmation.copy_operation_id, confirmation.operation_id))
                or confirmation.copy_operation_id == confirmation.operation_id
                or type(mount_key) is not tuple or len(mount_key) != 6
                or not isinstance(mountpoint, Path) or not mountpoint.is_absolute()
                or mountpoint == Path("/") or ".." in mountpoint.parts
                or mount_key[:2] != (confirmation.usb_fingerprint, mountpoint)
                or type(mount_key[2]) is not str
                or any(type(value) is not int for value in mount_key[3:])):
            raise ValueError("usb_removal_confirmation_invalid")
        scope = self._scope(confirmation.identity_id, confirmation.device_id)
        if (scope is None or scope.epoch != expected_profile_epoch
                or confirmation.usb_fingerprint != scope.fingerprint
                or self.completed_copy_operation(
                    identity_id=scope.identity_id, device_id=scope.device_id,
                    artifact_id=confirmation.artifact_id,
                ) != confirmation.copy_operation_id
                or not self._current(scope)):
            raise ValueError("usb_reader_scope_revoked")
        scope = deepcopy(scope)
        # The selected identity object itself is no longer an authority; the
        # admitted scope contains only its captured identity value.
        identity = deepcopy(storage.identity_get(scope.identity_id))
        if type(identity) is not dict:
            raise ValueError("usb_reader_scope_revoked")

        def current():
            try:
                placed = storage.device_placement_get(
                    scope.device_id, confirmation.artifact_id,
                )
                return (
                    self.service is service and service.storage is storage
                    and service.config is config
                    and getattr(service, "_plugin_manager", None) is manager
                    and getattr(manager, "registry_generation", -1) == registry_generation
                    and source_context_epoch(service) == epoch
                    and storage.identity_get(scope.identity_id) == identity
                    and type(placed) is dict
                    and placed.get("desired_state") == "remove"
                    and placed.get("target_relative_path") == confirmation.target_relative_path
                    and type(placed.get("metadata")) is dict
                    and placed["metadata"].get("intent_identity_id") == scope.identity_id
                    and self._current(scope, admitted=True)
                )
            except Exception:
                return False

        # Check that the second click remains valid until the exact local
        # facts are captured. This is the last navigation read.
        if (not current() or not self._current(scope, admitted=True)
                or getattr(getattr(service, "current_identity", None),
                           "identity_id", None) != scope.identity_id
                or service._account_context_epoch != expected_profile_epoch):
            raise ValueError("usb_reader_scope_revoked")
        lock, used = Lock(), [False]

        def check():
            if (not confirmation.confirmed_at <= int(time.time()) < confirmation.expires_at
                    or not current()
                    or not self._mount_attested(scope, mountpoint,
                                               scope_current=current,
                                               mount_key=mount_key)):
                raise ValueError("usb_reader_scope_revoked")

        def work():
            with lock:
                if used[0]:
                    return UsbSyncResult("blocked")
                used[0] = True
            if int(time.time()) >= confirmation.expires_at or not current():
                return UsbSyncResult("blocked")
            return self._execute_scoped(
                scope, confirmation=confirmation, mountpoint=mountpoint,
                scope_current=current, check_cancelled=check, mount_key=mount_key,
            )

        return work

    def execute_one(
        self, *, confirmation: UsbRemovalConsent | UsbCopyConsent,
        mountpoint: Path, expected_profile_epoch: int,
        check_cancelled: Callable[[], None] | None = None,
    ) -> UsbSyncResult:
        """Run one confirmed local copy or managed removal; no implicit sync.

        Never accept a rights witness or a policy callback from GTK/plugins.
        Local copy admission is verified by the locked Core executor.

        The UI-pinned account/profile epoch is mandatory. It must be the
        generation *at confirmation*, not the current value looked up in
        this worker: switching A→B→A before execution revokes consent.
        """
        blocked = UsbSyncResult("blocked")
        if (type(confirmation) not in (UsbRemovalConsent, UsbCopyConsent)
                or type(expected_profile_epoch) is not int
                or expected_profile_epoch < 0
                or (check_cancelled is not None and not callable(check_cancelled))):
            return blocked
        scope = self._scope(confirmation.identity_id, confirmation.device_id)
        if (scope is None or scope.epoch != expected_profile_epoch
                or confirmation.usb_fingerprint != scope.fingerprint):
            return blocked
        return self._execute_scoped(
            scope, confirmation=confirmation, mountpoint=mountpoint,
            scope_current=lambda: self._current(scope),
            check_cancelled=check_cancelled,
        )

    def _execute_scoped(self, scope, *, confirmation, mountpoint,
                        scope_current, check_cancelled, mount_key=None):
        blocked = UsbSyncResult("blocked")
        if not self._mount_attested(scope, mountpoint,
                                   scope_current=scope_current,
                                   mount_key=mount_key):
            return blocked
        try:
            storage = self.service.storage
            placed = storage.device_placement_get(scope.device_id, confirmation.artifact_id)
            if (type(placed) is not dict
                    or placed.get("device_id") != scope.device_id
                    or placed.get("artifact_id") != confirmation.artifact_id
                    or placed.get("desired_state") != ("keep" if type(confirmation) is UsbCopyConsent else "remove")
                    or placed.get("target_relative_path") != confirmation.target_relative_path
                    or type(placed.get("metadata")) is not dict
                    or placed["metadata"].get("intent_identity_id") != scope.identity_id):
                return blocked
            # A positive remove candidate requires the current physical
            # scan AND an exact completed managed-copy receipt. This dry-run
            # itself never grants deletion; the supervisor checks again.
            report = inspect_usb_dry_run(
                storage=storage, identity_id=scope.identity_id,
                device_id=scope.device_id, identity_root=scope.identity_root,
                mountpoint=mountpoint, artifact_id=confirmation.artifact_id,
            )
            if (type(report) is not UsbDryRun
                    or report.status != "scanned"
                    or report.scan_attested is not True
                    or report.executable is not False
                    or type(report.rows) is not tuple
                    or len(report.rows) != 1
                    or type(report.rows[0]) is not UsbPreviewRow
                    or report.rows[0].artifact_id != confirmation.artifact_id
                    or report.rows[0].action != ("upload_candidate" if type(confirmation) is UsbCopyConsent else "remove_candidate")
                    or (type(confirmation) is UsbRemovalConsent
                        and report.rows[0].reason != "managed_removal_requires_confirmation")
                    or report.rows[0].executable is not False
                    or not scope_current()
                    or not self._mount_attested(scope, mountpoint,
                                                scope_current=scope_current,
                                                mount_key=mount_key)):
                return UsbSyncResult("unavailable")

            def current_profile() -> tuple[str, Path, int]:
                # The locked removal review calls this callback before and
                # after physical inode verification. The admitted route
                # checks durable scope/mount; the historical direct API
                # retains its original active-profile/cancellation fence.
                if check_cancelled is not None:
                    check_cancelled()
                if not scope_current():
                    raise ValueError("usb_reader_scope_revoked")
                if check_cancelled is not None:
                    check_cancelled()
                return scope.identity_id, scope.identity_root, scope.epoch

            if check_cancelled is not None:
                check_cancelled()
            from ..paths import state_dir
            manager = getattr(self.service, "_plugin_manager", None)
            with InterProcessLock(state_dir(create=False) / "usb" / "sync.lock"):
                result = synchronize_usb_reader(
                    storage=storage, identity_id=scope.identity_id,
                    device_id=scope.device_id, identity_root=scope.identity_root,
                    mountpoint=mountpoint, lock_root=state_dir(create=False) / "usb",
                    current_profile=current_profile,
                    plugin_manager=manager, plugin_id="storage.usb",
                    registry_generation=getattr(manager, "registry_generation", -1),
                    confirmations={confirmation.artifact_id: confirmation},
                    rights={}, policy_verifier=None,
                    artifact_id=confirmation.artifact_id,
                    check_cancelled=check_cancelled,
                )
            return result if type(result) is UsbSyncResult else blocked
        except Exception:
            return blocked


__all__ = ["ReaderUsbExecutionBrokerImpl"]
