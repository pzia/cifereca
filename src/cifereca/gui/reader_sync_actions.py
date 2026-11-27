"""Explicit GTK grouped synchronization via the trusted reader broker."""
from __future__ import annotations

import secrets
import time

from ..core.device_usb_copy_admission import UsbCopyConsent
from ..core.device_usb_removal_review import UsbRemovalConsent
from ..core.device_usb_sync_engine import UsbSyncResult
from ..task_queue import TaskPriority
from .reader_usb_preview_actions import _discover_mounted_binding, _summary


def request_reader_sync(window, button, *, Adw=None):
    service = window.service
    storage = service.storage
    owner = getattr(service.current_identity, "identity_id", None)
    device = getattr(window, "_reader_selected_device_id", None)
    epoch = getattr(service, "_account_context_epoch", None)
    generation = getattr(window, "_reader_selection_generation", None)
    route = getattr(window, "_route_display_token", None)
    if not owner or not device or owner != getattr(window, "_reader_scope_identity", None):
        return
    binding = storage.device_binding_get(device)
    if not binding or binding.get("enabled") is not True:
        return
    fingerprint = (binding.get("metadata") or {}).get("usb_fingerprint")
    broker = window.plugin_manager.broker("reader.usb")
    if not fingerprint or not callable(getattr(broker, "prepare_delta", None)):
        return
    if getattr(window, "_reader_sync_active", False):
        return
    nonce = object()
    window._reader_sync_nonce = nonce

    def current():
        return (not getattr(window, "_closed", False)
                and getattr(window, "_reader_sync_nonce", None) is nonce
                and getattr(service.current_identity, "identity_id", None) == owner
                and service._account_context_epoch == epoch
                and getattr(window, "_reader_selected_device_id", None) == device
                and getattr(window, "_reader_selection_generation", None) == generation
                and getattr(window, "_route_display_token", None) is route
                and storage.device_binding_get(device) == binding)

    def prepare():
        volume = _discover_mounted_binding(storage=storage, identity_id=owner,
                                          device_id=device, fingerprint=fingerprint)
        if volume is None:
            return None
        proposal = broker.prepare_delta(identity_id=owner, device_id=device,
                        mountpoint=volume.mountpoint, mount_key=volume.selection_key())
        return (volume, proposal) if proposal is not None else None

    def error(_failure=None):
        if current():
            window._reader_status_label.set_label(
                "Synchronisation indisponible : vérifiez le support et les anomalies.")

    def ready(result):
        if not current():
            return
        if result is None:
            error()
            return
        volume, (report, items) = result
        window._reader_status_label.set_label(_summary(report))
        if not items:
            return
        api = Adw
        if api is None:
            from gi.repository import Adw as api
        uploads = sum(fmt is not None for _, _, fmt, _, _ in items)
        size = sum(size for _, _, _, _, size in items)
        dialog = api.MessageDialog.new(
            window, "Confirmer la synchronisation ?",
            f"{uploads} envoi(s), {len(items)-uploads} retrait(s) de copies gérées. "
            f"{size} octets à copier. Les fichiers sources restent inchangés. "
            "Les anomalies ne seront pas corrigées automatiquement.")
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("sync", "Synchroniser")
        dialog.set_close_response("cancel")
        window._reader_sync_dialog = dialog

        def answered(_dialog, response):
            if getattr(window, "_reader_sync_dialog", None) is not dialog:
                return
            window._reader_sync_dialog = None
            if response != "sync" or not current() or getattr(window, "_reader_sync_active", False):
                return
            now = int(time.time())
            confirmations = []
            for artifact, path, fmt, receipt, _ in items:
                args = (owner, device, fingerprint, artifact, artifact[7:])
                tail = (secrets.token_hex(16), now, now + 240)
                confirmations.append(UsbCopyConsent(*args, fmt, path, *tail)
                    if fmt is not None else UsbRemovalConsent(*args, path, receipt, *tail))
            try:
                work = broker.admit_delta(identity_id=owner, device_id=device,
                    confirmations=tuple(confirmations), mountpoint=volume.mountpoint,
                    expected_profile_epoch=epoch, mount_key=volume.selection_key())
            except Exception:
                error()
                return
            window._reader_sync_active = True
            window._reader_status_label.set_label("Synchronisation en cours…")
            def finished(value):
                window._reader_sync_active = False
                if current():
                    window._render_readers()
                    if type(value) is UsbSyncResult:
                        window._reader_status_label.set_label(
                            f"Synchronisation : {value.copied} copié(s), {value.removed} retiré(s), "
                            f"{value.pending} en attente, {value.failed} échec(s). "
                            + ("Observation vérifiée." if value.observed else "Présence non confirmée."))
                    else:
                        error()
            def failed(exc):
                window._reader_sync_active = False
                error(exc)
            try:
                window.runner.submit("Synchroniser la liseuse…", work, ok=finished,
                    err=failed, control=button, priority=TaskPriority.USER_ACTION,
                    kind="maintenance", origin="user")
            except Exception as exc:
                failed(exc)
        dialog.connect("response", answered)
        dialog.present()

    window.runner.submit("Préparer la synchronisation…", prepare, ok=ready,
        err=error, control=button, priority=TaskPriority.USER_ACTION,
        kind="maintenance-inspection", origin="user")
