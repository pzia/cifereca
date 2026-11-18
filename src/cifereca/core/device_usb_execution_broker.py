"""P1 #4985: conservative Core broker for explicit, one-book USB operations.

The GTK layer never receives USB file descriptors or writes to the device.
The broker reattests the selected identity, binding, mount and exact Core
delta, then delegates to the existing locked Core executor. It does NOT
manufacture independent DRM/copy rights. For now, only a completed managed
copy may be considered for a separately confirmed removal; uploads remain
blocked until a trusted positive rights resolver is available.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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

    def _scope(self, identity_id: str, device_id: str) -> _ReaderScope | None:
        service = self.service
        if (type(identity_id) is not str or not identity_id
                or type(device_id) is not str or not device_id):
            return None
        try:
            identity = service.current_identity
            epoch = service._account_context_epoch
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
                    or getattr(service, "current_identity", None) is not identity
                    or service._account_context_epoch != epoch):
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
            return _ReaderScope(identity, identity_id, epoch,
                                device_id, fingerprint, roots[0], binding)
        except Exception:
            return None

    def _current(self, scope: _ReaderScope) -> bool:
        try:
            fresh = self._scope(scope.identity_id, scope.device_id)
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

    def _mount_attested(self, scope: _ReaderScope, mountpoint: Path) -> bool:
        if (not isinstance(mountpoint, Path) or not mountpoint.is_absolute()
                or mountpoint == Path("/") or ".." in mountpoint.parts
                or not self._current(scope)):
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
                    and self._current(scope))
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

    def execute_one(
        self, *, confirmation: UsbRemovalConsent | UsbCopyConsent,
        mountpoint: Path, expected_profile_epoch: int,
        check_cancelled: Callable[[], None] | None = None,
    ) -> UsbSyncResult:
        """Run *one* confirmed managed removal; no broad implicit sync.

        Never accept a rights witness or a policy callback from GTK/plugins.
        Uploads are fail-closed until an independent Core source-rights
        authority can issue their proof, rather than deriving rights from
        the local file, acquisition history or selected reader.

        The UI-pinned account/profile epoch is mandatory. It must be the
        generation *at confirmation*, not the current value looked up in
        this worker: switching A→B→A before execution revokes consent.
        """
        blocked = UsbSyncResult("blocked")
        if type(confirmation) is UsbCopyConsent:
            return blocked
        if (type(confirmation) is not UsbRemovalConsent
                or type(expected_profile_epoch) is not int
                or expected_profile_epoch < 0
                or (check_cancelled is not None and not callable(check_cancelled))):
            return blocked
        scope = self._scope(confirmation.identity_id, confirmation.device_id)
        if (scope is None or scope.epoch != expected_profile_epoch
                or confirmation.usb_fingerprint != scope.fingerprint
                or not self._mount_attested(scope, mountpoint)):
            return blocked
        try:
            storage = self.service.storage
            placed = storage.device_placement_get(scope.device_id, confirmation.artifact_id)
            if (type(placed) is not dict
                    or placed.get("device_id") != scope.device_id
                    or placed.get("artifact_id") != confirmation.artifact_id
                    or placed.get("desired_state") != "remove"
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
                    or report.rows[0].action != "remove_candidate"
                    or report.rows[0].reason != "managed_removal_requires_confirmation"
                    or report.rows[0].executable is not False
                    or not self._current(scope)
                    or not self._mount_attested(scope, mountpoint)):
                return UsbSyncResult("unavailable")

            def current_profile() -> tuple[str, Path, int]:
                # The locked removal review calls this callback before and
                # after physical inode verification. It must also recheck
                # the originating GTK selection/cancellation, not merely
                # the profile epoch, before the journal commits a deletion.
                if check_cancelled is not None:
                    check_cancelled()
                if not self._current(scope):
                    raise ValueError("usb_reader_scope_revoked")
                if check_cancelled is not None:
                    check_cancelled()
                return scope.identity_id, scope.identity_root, scope.epoch

            if check_cancelled is not None:
                check_cancelled()
            from ..paths import state_dir
            manager = getattr(self.service, "_plugin_manager", None)
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
