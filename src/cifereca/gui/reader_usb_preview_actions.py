"""P1 #2854: explicit, asynchronous, read-only USB examination in Liseuses.

Opening a page, changing the profile or selecting a book never starts a
filesystem scan. Only the user's click opens a native mountpoint chooser.
The result is an ephemeral advisory status, never an executable permission.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ..core.library_audit_roots import configured_identity_roots
from ..task_queue import TaskPriority


def _exact_binding(storage: Any, identity_id: str, device_id: str,
                   fingerprint: str) -> bool:
    try:
        row = storage.device_binding_get(device_id)
    except Exception:
        return False
    return (
        type(row) is dict
        and row.get("identity_id") == identity_id
        and row.get("device_id") == device_id
        and row.get("enabled") is True
        and type(row.get("metadata")) is dict
        and row["metadata"].get("usb_fingerprint") == fingerprint
    )


def _summary(result: object) -> str:
    """Only closed counts go to GTK; never device paths, titles or SHA."""
    from ..core.device_usb_dry_run import UsbDryRun
    if type(result) is not UsbDryRun or result.executable is not False:
        return "Aperçu indisponible. Aucun transfert effectué."
    if result.status != "scanned" or result.scan_attested is not True:
        return ("Support USB non attesté : présence inconnue. "
                "Aucun fichier transféré ou supprimé.")
    rows = result.rows
    if type(rows) is not tuple or len(rows) > 1000 or any(
        row.executable is not False for row in rows
    ):
        return "Aperçu indisponible. Aucun transfert effectué."
    tally = Counter(row.action for row in rows)
    if set(tally) - {"upload_candidate", "skip", "verify", "blocked"}:
        return "Aperçu indisponible. Aucun transfert effectué."
    return (
        f"Aperçu USB ponctuel · {tally['upload_candidate']} envoi(s) à examiner · "
        f"{tally['skip']} identique(s) · {tally['verify']} à vérifier · "
        f"{tally['blocked']} bloqué(s). "
        "Aucun transfert ni retrait ; toute opération exige une nouvelle vérification."
    )


def request_reader_usb_preview(window, button, *, Gtk) -> None:
    """User click -> folder chooser -> TaskRunner worker -> scoped GTK result.

    The chosen folder MUST be the exact block-device mountpoint. Core's USB
    scanner independently rejects ordinary directories or another device.
    A remote/catalogue/borrowed book never gains any permission from here.
    """
    if getattr(window, "_closed", False):
        return
    service = getattr(window, "service", None)
    storage = getattr(service, "storage", None)
    config = getattr(window, "config", None)
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    device = getattr(window, "_reader_selected_device_id", None)
    if (type(identity) is not str or not identity
            or identity != getattr(window, "_reader_scope_identity", None)
            or type(device) is not str or not device
            or storage is None or config is None):
        return
    try:
        binding = storage.device_binding_get(device)
    except Exception:
        return
    meta = binding.get("metadata") if type(binding) is dict else None
    fingerprint = meta.get("usb_fingerprint") if type(meta) is dict else None
    if (type(fingerprint) is not str
            or not 8 <= len(fingerprint) <= 128
            or not _exact_binding(storage, identity, device, fingerprint)):
        window._reader_status_label.set_label(
            "Liseuse désactivée ou non vérifiée. Aucun scan effectué."
        )
        return
    # Pin the canonical root before choosing the USB mount. Reconfiguration
    # of the profile/library during a native dialog revokes that request.
    try:
        roots = configured_identity_roots(config, identity).get(identity)
        root = roots[0] if type(roots) is tuple and roots else None
        if not isinstance(root, Path) or not root.is_absolute():
            raise ValueError("invalid library root")
    except Exception:
        window._reader_status_label.set_label(
            "Racine du profil à vérifier. Aucun scan effectué."
        )
        return
    # The Library snapshot is an opaque, ephemeral scope token. A refresh,
    # filter change or profile switch revokes the eventual per-book details.
    source_snapshot = getattr(window, "_reader_library_snapshot", None)
    scope = (
        identity, device, fingerprint,
        getattr(window, "_identity_context_generation", None),
        getattr(window, "_account_context_generation", None),
        getattr(window, "_route_display_token", None),
        getattr(window, "_reader_selection_generation", None),
        root,
    )
    nonce = object()
    window._reader_usb_preview_nonce = nonce

    def root_current() -> bool:
        try:
            current_roots = configured_identity_roots(config, scope[0]).get(scope[0])
            return (type(current_roots) is tuple and bool(current_roots)
                    and current_roots[0] == scope[7])
        except Exception:
            return False

    def current() -> bool:
        return (
            not getattr(window, "_closed", False)
            and getattr(window, "_reader_usb_preview_nonce", None) is nonce
            and getattr(getattr(service, "current_identity", None),
                        "identity_id", None) == scope[0]
            and getattr(window, "_reader_scope_identity", None) == scope[0]
            and getattr(window, "_reader_selected_device_id", None) == scope[1]
            and getattr(window, "_identity_context_generation", None) == scope[3]
            and getattr(window, "_account_context_generation", None) == scope[4]
            and getattr(window, "_route_display_token", None) is scope[5]
            and getattr(window, "_reader_selection_generation", None) == scope[6]
            and root_current()
            and (source_snapshot is None
                 or getattr(window, "_reader_library_snapshot", None)
                    is source_snapshot)
            and _exact_binding(storage, scope[0], scope[1], scope[2])
        )

    try:
        chooser = Gtk.FileChooserNative.new(
            "Choisir la racine du volume USB de la liseuse", window,
            Gtk.FileChooserAction.SELECT_FOLDER, "Examiner", "Annuler",
        )
    except Exception:
        if current():
            window._reader_status_label.set_label(
                "Choix du volume USB indisponible. Aucun scan effectué."
            )
        return
    window._reader_usb_preview_chooser = chooser

    def finish(report):
        if not current():
            return
        window._reader_status_label.set_label(_summary(report))
        if source_snapshot is None:
            # Legacy headless integrations may not publish a Library
            # snapshot. The conservative aggregate summary still works.
            return
        try:
            from .reader_usb_decisions import project_reader_usb_decisions
            decisions = project_reader_usb_decisions(
                report=report, library=source_snapshot,
            )
            if decisions.status != "scanned" or decisions.executable is not False:
                window._reader_usb_decision_details = None
                window._reader_usb_detail_source_snapshot = None
                window._reader_status_label.set_label(decisions.message)
                return
            window._reader_usb_decision_details = decisions
            window._reader_usb_detail_source_snapshot = source_snapshot
            # Re-render only the selected book detail, never the entire
            # virtualized list or its cached provider notices.  This is a
            # pure presentation of the user's explicit read-only scan.
            page = getattr(window, "readers_page", None)
            selection = getattr(page, "selection", None)
            selected = (
                selection.get_selected_item()
                if callable(getattr(selection, "get_selected_item", None))
                else None
            )
            if (selected is not None
                    and type(getattr(selected, "key", None)) is str
                    and any(row.book_key == selected.key
                            for row in decisions.books)
                    and current()):
                from .reader_page_controller import reader_selected
                reader_selected(window, selected)
        except Exception:
            window._reader_usb_decision_details = None
            window._reader_usb_detail_source_snapshot = None
            if current():
                window._reader_status_label.set_label(
                    "Aperçu des livres indisponible. Aucun transfert effectué."
                )

    def error(_failure):
        if current():
            window._reader_status_label.set_label(
                "Aperçu USB indisponible. Aucun transfert effectué."
            )

    def respond(dialog, response):
        try:
            if (not current()
                    or getattr(window, "_reader_usb_preview_chooser", None)
                        is not dialog
                    or response != Gtk.ResponseType.ACCEPT):
                return
            candidate = dialog.get_file()
            text = candidate.get_path() if candidate is not None else None
            if type(text) is not str or not text or "\x00" in text:
                error(None)
                return
            mountpoint = Path(text)
            if (not mountpoint.is_absolute()
                    or ".." in mountpoint.parts
                    or mountpoint == Path("/")):
                error(None)
                return

            def work():
                # All physical reads and SHA calculations run in TaskRunner,
                # never in GTK response or navigation handlers.
                from ..core.device_usb_dry_run import inspect_usb_dry_run
                if not root_current():
                    raise ValueError("identity root changed")
                # Use the pinned primary library root. An optional
                # separately configured Kobo root cannot be silently merged
                # with an unrelated profile's directory.
                return inspect_usb_dry_run(
                    storage=storage, identity_id=identity,
                    device_id=device, identity_root=scope[7],
                    mountpoint=mountpoint,
                )

            # Revalidate before queueing; a chooser can outlive a profile
            # change, account reconfiguration or device rebinding.
            if not current():
                return
            window.runner.submit(
                "Examiner les changements USB…", work,
                ok=finish, err=error, control=button,
                priority=TaskPriority.USER_ACTION,
                kind="maintenance-inspection", origin="user",
            )
            if current():
                window._reader_status_label.set_label(
                    "Examen USB en cours… aucune copie ni suppression."
                )
        except Exception:
            error(None)
        finally:
            try:
                dialog.destroy()
            finally:
                if getattr(window, "_reader_usb_preview_chooser", None) is dialog:
                    window._reader_usb_preview_chooser = None

    chooser.connect("response", respond)
    chooser.show()


__all__ = ["request_reader_usb_preview"]
