"""P1 #2854: explicit, asynchronous, read-only USB examination in Liseuses.

Opening a page, changing the profile or selecting a book never starts a
filesystem scan. Only an explicit click starts read-only USB discovery in a
worker; the manual mountpoint chooser is a fallback, never the default.
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


def _discover_mounted_binding(*, storage, identity_id: str, device_id: str,
                              fingerprint: str):
    """Worker-only: one exactly scoped, independently reattested USB mount.

    UI discovery is not an execution token; the subsequent scan reattests
    again and the Core scanner independently qualifies the mount under lock.
    """
    from ..core.device_usb_discovery import (
        MountedUsbVolume, discover_linux_usb_volumes,
    )
    from ..core.device_usb_mount import LinuxUsbMount, observe_linux_usb_mount
    try:
        binding = storage.device_binding_get(device_id)
        if (type(binding) is not dict
                or binding.get("identity_id") != identity_id
                or binding.get("device_id") != device_id
                or binding.get("enabled") is not True
                or type(binding.get("metadata")) is not dict
                or binding["metadata"].get("usb_fingerprint") != fingerprint):
            return None
        # A legacy/foreign binding with the same UUID makes association
        # ownership ambiguous. Do not infer exclusivity from the chosen
        # row alone; bound the complete transactional storage projection.
        bindings = storage.device_bindings()
        if type(bindings) is not list or len(bindings) > 4096:
            return None
        exact_bindings = [
            item for item in bindings
            if type(item) is dict
            and type(item.get("metadata")) is dict
            and type(item["metadata"].get("usb_fingerprint")) is str
            and item["metadata"]["usb_fingerprint"].casefold()
                == fingerprint.casefold()
        ]
        if (len(exact_bindings) != 1
                or exact_bindings[0].get("identity_id") != identity_id
                or exact_bindings[0].get("device_id") != device_id):
            return None
        volumes = discover_linux_usb_volumes()
        if (type(volumes) is not tuple or len(volumes) > 128
                or any(type(item) is not MountedUsbVolume for item in volumes)):
            return None
        exact = [item for item in volumes
                 if item.usb_fingerprint == fingerprint]
        # Ambiguity includes two mounts with the same UUID. Never choose by
        # pathname, label, presentation order, or a cached SQLite binding.
        if len(exact) != 1:
            return None
        selected = exact[0]
        proof = observe_linux_usb_mount(
            identity_id=identity_id, binding=binding,
            mountpoint=selected.mountpoint,
        )
        if (type(proof) is not LinuxUsbMount
                or proof.identity_id != identity_id
                or proof.device_id != device_id
                or proof.usb_fingerprint != fingerprint
                or proof.mountpoint != selected.mountpoint
                or proof.filesystem != selected.filesystem
                or (proof.major, proof.minor) != (selected.major, selected.minor)
                or proof.mount_id != selected.mount_id):
            return None
        return selected
    except Exception:
        return None


def request_reader_usb_preview(window, button, *, Gtk, manual: bool = False) -> None:
    """User click -> worker mount discovery -> worker scan or manual fallback.

    The Core scanner independently attests an exact block-device mount,
    never trusting the chosen folder or this read-only discovery as authority.
    No scan occurs during page construction or implicit navigation.
    """
    if type(manual) is not bool or getattr(window, "_closed", False):
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

    def queue_scan(mountpoint: Path, *, observed=None):
        if not current():
            return
        def work():
            # Both discovery and every physical read remain in TaskRunner.
            from ..core.device_usb_discovery import MountedUsbVolume
            from ..core.device_usb_dry_run import inspect_usb_dry_run
            # Worker thread: never interrogate GTK window/widget state.
            # Revalidate durable binding + configured library root here;
            # UI generations are rechecked on GTK callbacks only.
            if (not root_current()
                    or not _exact_binding(storage, identity, device, fingerprint)):
                raise ValueError("reader scope changed")
            if observed is not None:
                live = _discover_mounted_binding(
                    storage=storage, identity_id=identity,
                    device_id=device, fingerprint=fingerprint,
                )
                if (type(observed) is not MountedUsbVolume
                        or type(live) is not MountedUsbVolume
                        or live.selection_key() != observed.selection_key()):
                    raise ValueError("USB mount changed since discovery")
            return inspect_usb_dry_run(
                storage=storage, identity_id=identity,
                device_id=device, identity_root=scope[7],
                mountpoint=mountpoint,
            )

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

            # Manual expert fallback still goes through the exact Core
            # scanner. A selected folder is never evidence of USB presence.
            queue_scan(mountpoint)
        except Exception:
            error(None)
        finally:
            try:
                dialog.destroy()
            finally:
                if getattr(window, "_reader_usb_preview_chooser", None) is dialog:
                    window._reader_usb_preview_chooser = None

    def show_chooser():
        if not current():
            return
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
        chooser.connect("response", respond)
        chooser.show()

    if manual:
        # Explicit expert invocation, also reused by legacy chooser tests.
        show_chooser()
        return

    def discover():
        # Worker-only discovery. The already-pinned UI scope is checked
        # before submit and on callback, never from the TaskRunner thread.
        return _discover_mounted_binding(
            storage=storage, identity_id=identity,
            device_id=device, fingerprint=fingerprint,
        )

    def discovered(result):
        if not current():
            return
        from ..core.device_usb_discovery import MountedUsbVolume
        if type(result) is MountedUsbVolume:
            queue_scan(result.mountpoint, observed=result)
            return
        window._reader_status_label.set_label(
            "Liseuse non connectée ou montage non vérifiable. "
            "Choisissez le volume manuellement ou réessayez. "
            "Aucun transfert effectué."
        )
        show_chooser()

    def discovery_failed(_failure):
        discovered(None)

    window.runner.submit(
        "Rechercher la liseuse USB associée…", discover,
        ok=discovered, err=discovery_failed, control=button,
        priority=TaskPriority.USER_ACTION,
        kind="maintenance-inspection", origin="user",
    )
    if current():
        window._reader_status_label.set_label(
            "Recherche du volume USB associé… aucun transfert."
        )


__all__ = ["request_reader_usb_preview", "_discover_mounted_binding"]
