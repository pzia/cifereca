"""P1 #4985: two-stage GTK confirmation for one Core-managed USB removal.

Only a voluntary click starts worker-side discovery and Core dry-run. A later,
separate destructive confirmation starts the Core broker's exact removal.
No GTK filesystem access, no inferred copy rights, no automatic operation.
The physical scan/receipt and in-lock revalidation remain Core authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import secrets
import time

from ..core.device_usb_dry_run import UsbDryRun
from ..core.device_usb_discovery import MountedUsbVolume
from ..core.device_usb_preview import UsbPreviewRow
from ..core.device_usb_removal_review import UsbRemovalConsent
from ..core.device_usb_sync_engine import UsbSyncResult
from ..task_queue import TaskPriority
from .reader_library_projection import ReaderLibraryBook, ReaderLibrarySnapshot

_OPERATION = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_FAILED = "Retrait USB non confirmé. Réexaminez la liseuse ; aucun succès présumé."


@dataclass(frozen=True, slots=True)
class _RemovalCandidate:
    mountpoint: Path
    mount_key: tuple[object, ...]
    copy_operation_id: str
    artifact_id: str
    target_relative_path: str


def append_reader_usb_execute_action(window, detail, book: ReaderLibraryBook, *,
                                     Gtk, Adw=None) -> bool:
    """Offer a visible action only when Core broker and receipts are installed.

    Render merely checks trusted broker availability and scope. No mount,
    filesystem, journal content or provider is read until explicit click.
    """
    if (type(book) is not ReaderLibraryBook
            or book.desired_state != "remove"
            or type(book.target_relative_path) is not str
            or Gtk is None or not callable(getattr(Gtk, "Button", None))
            or not callable(getattr(detail, "add_actions", None))
            or getattr(window, "_closed", False)):
        return False
    service = getattr(window, "service", None)
    storage = getattr(service, "storage", None)
    identity_id = getattr(getattr(service, "current_identity", None), "identity_id", None)
    device_id = getattr(window, "_reader_selected_device_id", None)
    snapshot = getattr(window, "_reader_library_snapshot", None)
    manager = getattr(window, "plugin_manager", None)
    broker_getter = getattr(manager, "broker", None)
    broker = broker_getter("reader.usb") if callable(broker_getter) else None
    if (type(identity_id) is not str or not identity_id
            or type(device_id) is not str or not device_id
            or type(snapshot) is not ReaderLibrarySnapshot
            or snapshot.identity_id != identity_id
            or snapshot.device_id != device_id
            or snapshot.device_enabled is not True
            or getattr(window, "_reader_scope_identity", None) != identity_id
            or getattr(window, "_reader_items", {}).get(book.key) is not book
            or storage is None or not callable(getattr(broker, "preview", None))
            or not callable(getattr(broker, "admit_removal", None))
            or not callable(getattr(getattr(window, "runner", None), "submit", None))):
        return False
    # The Core broker owns journal access. Its optional read-only facet
    # needs #6295 on main; missing integrations hide this action entirely.
    if not callable(getattr(broker, "completed_copy_operation", None)):
        return False
    try:
        binding = storage.device_binding_get(device_id)
        meta = binding.get("metadata") if type(binding) is dict else None
        fingerprint = meta.get("usb_fingerprint") if type(meta) is dict else None
        intent = storage.device_placement_get(device_id, book.artifact_id)
        if (type(binding) is not dict or binding.get("enabled") is not True
                or binding.get("identity_id") != identity_id
                or binding.get("device_id") != device_id
                or type(fingerprint) is not str or not 8 <= len(fingerprint) <= 128
                or type(intent) is not dict
                or intent.get("desired_state") != "remove"
                or intent.get("target_relative_path") != book.target_relative_path
                or type(intent.get("metadata")) is not dict
                or intent["metadata"].get("intent_identity_id") != identity_id):
            return False
    except Exception:
        return False

    button = Gtk.Button(label="Retirer réellement du support USB…")
    button._cifereca_action_id = "reader-usb-execute-remove"
    button.set_tooltip_text(
        "Examine à nouveau le support et la copie gérée, puis demande une "
        "seconde confirmation avant une suppression supervisée par Core."
    )
    account_epoch = getattr(service, "_account_context_epoch", None)
    generation = (
        getattr(window, "_identity_context_generation", None),
        getattr(window, "_account_context_generation", None),
        getattr(window, "_reader_selection_generation", None),
    )
    route = getattr(window, "_route_display_token", None)

    def current() -> bool:
        if (getattr(window, "_closed", False)
                or getattr(getattr(service, "current_identity", None),
                           "identity_id", None) != identity_id
                or getattr(service, "_account_context_epoch", None) != account_epoch
                or getattr(window, "_reader_scope_identity", None) != identity_id
                or getattr(window, "_reader_selected_device_id", None) != device_id
                or getattr(window, "_reader_library_snapshot", None) is not snapshot
                or getattr(window, "_reader_items", {}).get(book.key) is not book
                or (getattr(window, "_identity_context_generation", None),
                    getattr(window, "_account_context_generation", None),
                    getattr(window, "_reader_selection_generation", None)) != generation
                or getattr(window, "_route_display_token", None) is not route):
            return False
        page = getattr(window, "readers_page", None)
        getter = getattr(getattr(page, "selection", None), "get_selected_item", None)
        row = getter() if callable(getter) else None
        return row is not None and getattr(row, "key", None) == book.key

    def exact_intent() -> bool:
        """Worker-safe durable facts; never read GUI widgets here."""
        try:
            selected = storage.device_binding_get(device_id)
            saved = storage.device_placement_get(device_id, book.artifact_id)
            metadata = selected.get("metadata") if type(selected) is dict else None
            return (
                type(selected) is dict
                and selected.get("enabled") is True
                and selected.get("identity_id") == identity_id
                and type(metadata) is dict
                and metadata.get("usb_fingerprint") == fingerprint
                and type(saved) is dict
                and saved.get("desired_state") == "remove"
                and saved.get("target_relative_path") == book.target_relative_path
                and type(saved.get("metadata")) is dict
                and saved["metadata"].get("intent_identity_id") == identity_id
            )
        except Exception:
            return False

    def mounted() -> MountedUsbVolume | None:
        from .reader_usb_preview_actions import _discover_mounted_binding
        found = _discover_mounted_binding(
            storage=storage, identity_id=identity_id,
            device_id=device_id, fingerprint=fingerprint,
        )
        return found if type(found) is MountedUsbVolume else None

    def copy_id() -> str | None:
        if not exact_intent():
            return None
        operation_id = broker.completed_copy_operation(
            identity_id=identity_id, device_id=device_id,
            artifact_id=book.artifact_id,
        )
        return (
            operation_id if type(operation_id) is str
            and _OPERATION.fullmatch(operation_id) else None
        )

    def prepare() -> _RemovalCandidate | None:
        if not exact_intent():
            return None
        selected = mounted()
        if selected is None:
            return None
        report = broker.preview(
            identity_id=identity_id, device_id=device_id,
            mountpoint=selected.mountpoint,
        )
        if (type(report) is not UsbDryRun
                or report.status != "scanned"
                or report.scan_attested is not True
                or report.executable is not False
                or report.identity_id != identity_id
                or report.device_id != device_id
                or type(report.rows) is not tuple or len(report.rows) > 1000):
            return None
        qualified = [
            row for row in report.rows
            if type(row) is UsbPreviewRow
            and row.artifact_id == book.artifact_id
            and row.action == "remove_candidate"
            and row.reason == "managed_removal_requires_confirmation"
            and row.executable is False
        ]
        if len(qualified) != 1:
            return None
        operation_id = copy_id()
        if operation_id is None:
            return None
        # Discovery may have been invalidated during hashing/journal reads.
        live = mounted()
        if (live is None or live.selection_key() != selected.selection_key()
                or not exact_intent()):
            return None
        return _RemovalCandidate(
            selected.mountpoint, selected.selection_key(),
            operation_id, book.artifact_id, book.target_relative_path,
        )

    def admit(candidate: _RemovalCandidate):
        # The second explicit click starts the validity window. No new
        # permission or generation may be manufactured by the queued worker.
        if not exact_intent():
            raise ValueError("usb_reader_scope_revoked")
        now = int(time.time())
        consent = UsbRemovalConsent(
            identity_id, device_id, fingerprint, candidate.artifact_id,
            candidate.artifact_id.removeprefix("sha256:"),
            candidate.target_relative_path, candidate.copy_operation_id,
            secrets.token_hex(16), now, now + 60,
        )
        return broker.admit_removal(
            confirmation=consent, mountpoint=candidate.mountpoint,
            expected_profile_epoch=account_epoch, mount_key=candidate.mount_key,
        )

    def clicked(_button) -> None:
        if not current():
            return
        root = getattr(button, "get_root", None)
        mapped = getattr(button, "get_mapped", None)
        if ((callable(root) and root() is None)
                or (callable(mapped) and not mapped())):
            return
        nonce = object()
        window._reader_usb_execute_nonce = nonce
        window._reader_usb_execute_control = button

        def worker_scope_current() -> None:
            # Worker-thread check: read only atomic Python scope tokens, no
            # GTK widgets, models, signals or visual state. The originating
            # button's root-property notification revokes a detached detail.
            if (getattr(window, "_closed", False)
                    or getattr(window, "_reader_usb_execute_nonce", None) is not nonce
                    or getattr(window, "_reader_usb_execute_control", None) is not button
                    or getattr(getattr(service, "current_identity", None),
                               "identity_id", None) != identity_id
                    or getattr(service, "_account_context_epoch", None) != account_epoch
                    or getattr(window, "_reader_scope_identity", None) != identity_id
                    or getattr(window, "_reader_selected_device_id", None) != device_id
                    or getattr(window, "_reader_library_snapshot", None) is not snapshot
                    or getattr(window, "_reader_items", {}).get(book.key) is not book
                    or (getattr(window, "_identity_context_generation", None),
                        getattr(window, "_account_context_generation", None),
                        getattr(window, "_reader_selection_generation", None)) != generation
                    or getattr(window, "_route_display_token", None) is not route):
                raise RuntimeError("reader_usb_selection_scope_revoked")

        def prepare_work():
            worker_scope_current()
            candidate = prepare()
            worker_scope_current()
            return candidate

        def active() -> bool:
            return (current()
                    and getattr(window, "_reader_usb_execute_nonce", None) is nonce)

        def rejected(_error=None):
            if active():
                window._reader_status_label.set_label(_FAILED)

        def finished(result):
            if not active():
                return
            if (type(result) is UsbSyncResult and result.status == "completed"
                    and result.removed == 1 and result.failed == 0
                    and result.observed is True):
                window._render_readers()
                if (not getattr(window, "_closed", False)
                        and getattr(getattr(service, "current_identity", None),
                                    "identity_id", None) == identity_id
                        and getattr(window, "_reader_selected_device_id", None)
                            == device_id):
                    window._reader_status_label.set_label(
                        "Retrait USB supervisé terminé · nouvelle observation "
                        "à examiner pour confirmer la présence des autres livres."
                    )
            else:
                rejected()

        def ready(candidate):
            if not active():
                return
            if type(candidate) is not _RemovalCandidate:
                rejected()
                return
            try:
                dialog_api = Adw
                if dialog_api is None:
                    from gi.repository import Adw as dialog_api
                dialog = dialog_api.MessageDialog.new(
                    window, "Supprimer cette copie gérée de la liseuse ?",
                    "Cette action supprime effectivement le fichier géré sur "
                    "le volume USB vérifié. Elle ne touche pas au livre local. "
                    "La copie est identifiée par un reçu Core achevé ; une "
                    "nouvelle vérification précède la suppression.",
                )
                dialog.add_response("cancel", "Conserver le fichier")
                dialog.add_response("delete", "Supprimer la copie USB")
                dialog.set_close_response("cancel")
                if hasattr(dialog, "set_response_appearance"):
                    dialog.set_response_appearance(
                        "delete", dialog_api.ResponseAppearance.DESTRUCTIVE,
                    )
                window._reader_usb_execute_dialog = dialog

                def answered(_dialog, response):
                    if getattr(window, "_reader_usb_execute_dialog", None) is not dialog:
                        return
                    window._reader_usb_execute_dialog = None
                    if response != "delete" or not active():
                        return
                    try:
                        work = admit(candidate)
                        if not callable(work) or not active():
                            rejected()
                            return
                    except Exception:
                        rejected()
                        return
                    window._reader_status_label.set_label(
                        "Retrait USB supervisé en cours… aucune réussite présumée."
                    )
                    window.runner.submit(
                        "Retirer la copie USB gérée…",
                        work,
                        ok=finished, err=rejected,
                        control=button, priority=TaskPriority.USER_ACTION,
                        kind="maintenance", origin="user",
                    )

                dialog.connect("response", answered)
                dialog.present()
            except Exception:
                rejected()

        window._reader_status_label.set_label(
            "Vérification du support et du reçu de copie en cours… "
            "aucun fichier supprimé."
        )
        window.runner.submit(
            "Vérifier un retrait USB géré…", prepare_work, ok=ready, err=rejected,
            control=button, priority=TaskPriority.USER_ACTION,
            kind="maintenance-inspection", origin="user",
        )

    def root_changed(_button, _pspec):
        # Gtk.Widget:root is an observable property, not a GObject signal
        # named "unroot". Replacing a selected detail detaches its control.
        root = getattr(button, "get_root", None)
        if (callable(root) and root() is None
                and getattr(window, "_reader_usb_execute_control", None)
                    is button):
            window._reader_usb_execute_nonce = None

    button.connect("notify::root", root_changed)
    button.connect("clicked", clicked)
    detail.add_actions([button])
    return True


__all__ = ["append_reader_usb_execute_action"]
