"""P1 #4985: direct, explicit *wishes* in the selected reader detail.

This controller deliberately never invokes a USB operation: the Core USB
executors require a separately attested, fresh copy right or managed-removal
receipt. A GTK click may only record an exact per-device SQLite intention.
"""
from __future__ import annotations

from .reader_library_projection import ReaderLibraryBook, ReaderLibrarySnapshot


def append_reader_intent_action(window, detail, book: ReaderLibraryBook, *,
                                Gtk, Adw=None) -> bool:
    """Add one selected-device action; confirmation is mandatory for removal.

    An old button/dialog is revoked by selection, route, account, device,
    identity, Library snapshot or binding changes. Storage validates ownership
    again in its SQLite transaction. Nothing is scanned, copied or deleted.
    """
    if (Gtk is None or not callable(getattr(Gtk, "Button", None))
            or getattr(window, "_closed", False)
            or type(book) is not ReaderLibraryBook
            or book.desired_state not in ("none", "keep", "remove")
            or not callable(getattr(detail, "add_actions", None))):
        return False
    service = getattr(window, "service", None)
    storage = getattr(service, "storage", None)
    identity_id = getattr(getattr(service, "current_identity", None), "identity_id", None)
    device_id = getattr(window, "_reader_selected_device_id", None)
    snapshot = getattr(window, "_reader_library_snapshot", None)
    if (type(identity_id) is not str or not identity_id
            or identity_id != getattr(window, "_reader_scope_identity", None)
            or type(device_id) is not str or not device_id
            or type(snapshot) is not ReaderLibrarySnapshot
            or snapshot.identity_id != identity_id or snapshot.device_id != device_id
            or getattr(window, "_reader_items", {}).get(book.key) is not book
            or storage is None):
        return False

    try:
        binding = storage.device_binding_get(device_id)
        meta = binding.get("metadata") if type(binding) is dict else None
        fingerprint = meta.get("usb_fingerprint") if type(meta) is dict else None
        saved = storage.device_placement_get(device_id, book.artifact_id)
        if (type(binding) is not dict
                or binding.get("identity_id") != identity_id
                or binding.get("device_id") != device_id
                or type(fingerprint) is not str or not fingerprint
                or (book.desired_state == "none" and saved is not None)
                or (book.desired_state != "none" and (
                    type(saved) is not dict
                    or saved.get("desired_state") != book.desired_state
                    or type(saved.get("metadata")) is not dict
                    or saved["metadata"].get("intent_identity_id") != identity_id))):
            return False
    except Exception:
        return False

    generations = (
        getattr(window, "_identity_context_generation", None),
        getattr(window, "_account_context_generation", None),
        getattr(window, "_reader_selection_generation", None),
    )
    route_token = getattr(window, "_route_display_token", None)
    if book.desired_state == "none":
        wished, caption = "keep", "Mettre sur cette liseuse"
    elif book.desired_state == "keep":
        wished, caption = "remove", "Préparer le retrait…"
    else:
        wished, caption = "keep", "Annuler le retrait"
    button = Gtk.Button(label=caption)
    button._cifereca_action_id = "reader-usb-intent:" + wished
    button.set_tooltip_text(
        "Enregistre seulement un souhait pour cette liseuse. "
        "Aucune copie ni suppression sur le support USB."
    )

    def current() -> bool:
        if (getattr(window, "_closed", False)
                or getattr(getattr(service, "current_identity", None),
                           "identity_id", None) != identity_id
                or getattr(window, "_reader_scope_identity", None) != identity_id
                or getattr(window, "_reader_selected_device_id", None) != device_id
                or getattr(window, "_reader_library_snapshot", None) is not snapshot
                or getattr(window, "_reader_items", {}).get(book.key) is not book
                or (getattr(window, "_identity_context_generation", None),
                    getattr(window, "_account_context_generation", None),
                    getattr(window, "_reader_selection_generation", None))
                   != generations
                or getattr(window, "_route_display_token", None) is not route_token):
            return False
        page = getattr(window, "readers_page", None)
        selected_getter = getattr(getattr(page, "selection", None),
                                  "get_selected_item", None)
        selected = selected_getter() if callable(selected_getter) else None
        if selected is None or getattr(selected, "key", None) != book.key:
            return False
        root = getattr(button, "get_root", None)
        if callable(root) and root() is None:
            return False
        mapped = getattr(button, "get_mapped", None)
        if callable(mapped) and not mapped():
            return False
        return True

    def commit() -> None:
        if not current():
            return
        try:
            live_binding = storage.device_binding_get(device_id)
            live_meta = (live_binding.get("metadata")
                         if type(live_binding) is dict else None)
            placement = storage.placement_get(book.placement_id)
            live = storage.device_placement_get(device_id, book.artifact_id)
            if (type(live_binding) is not dict
                    or live_binding.get("identity_id") != identity_id
                    or live_binding.get("device_id") != device_id
                    or type(live_meta) is not dict
                    or live_meta.get("usb_fingerprint") != fingerprint
                    or type(placement) is not dict
                    or placement.get("placement_id") != book.placement_id
                    or placement.get("identity_id") != identity_id
                    or placement.get("artifact_id") != book.artifact_id
                    or placement.get("state") != "present"
                    or (book.desired_state == "none" and live is not None)
                    or (book.desired_state != "none" and (
                        type(live) is not dict
                        or live.get("desired_state") != book.desired_state
                        or type(live.get("metadata")) is not dict
                        or live["metadata"].get("intent_identity_id") != identity_id))
                    or not current()):
                raise ValueError("reader_intent_scope_changed")
            # A transactional Core storage operation; it does not touch USB.
            storage.device_usb_intent_set(
                identity_id=identity_id, device_id=device_id,
                artifact_id=book.artifact_id, desired_state=wished,
            )
            if wished == "keep":
                reserve = getattr(storage, "device_usb_reserve_pending_target", None)
                if callable(reserve):
                    reserve(identity_id=identity_id, device_id=device_id,
                            artifact_id=book.artifact_id)
        except Exception:
            if current():
                window.show_error(
                    "Souhait non enregistré : revérifiez le profil, le livre et la liseuse.",
                    "Liseuses",
                )
            return
        if current():
            window._render_readers()
            if (not getattr(window, "_closed", False)
                    and getattr(getattr(service, "current_identity", None),
                                "identity_id", None) == identity_id
                    and getattr(window, "_reader_selected_device_id", None) == device_id):
                window._reader_status_label.set_label(
                    "Souhait enregistré · présence USB non confirmée. "
                    "Aucun fichier transféré ou supprimé."
                )

    def clicked(_button):
        if not current():
            return
        if wished != "remove":
            commit()
            return
        # A removal wish never unlinks anything. Still require a separate,
        # deliberate confirmation instead of acting on a stale row button.
        try:
            dialog_api = Adw
            if dialog_api is None:
                from gi.repository import Adw as dialog_api
            dialog = dialog_api.MessageDialog.new(
                window, "Préparer le retrait de cette liseuse ?",
                "Cela enregistre uniquement un souhait de retrait pour le "
                "livre sélectionné. Aucun fichier USB ne sera supprimé. "
                "Une exécution ultérieure demandera une nouvelle confirmation "
                "et la vérification du reçu de copie gérée.",
            )
            dialog.add_response("cancel", "Annuler")
            dialog.add_response("remove", "Enregistrer le souhait")
            dialog.set_close_response("cancel")
            nonce = object()
            window._reader_intent_dialog = dialog
            window._reader_intent_nonce = nonce

            def answered(_dialog, response):
                if (response == "remove" and current()
                        and getattr(window, "_reader_intent_dialog", None) is dialog
                        and getattr(window, "_reader_intent_nonce", None) is nonce):
                    commit()
                if getattr(window, "_reader_intent_dialog", None) is dialog:
                    window._reader_intent_dialog = None

            dialog.connect("response", answered)
            dialog.present()
        except Exception:
            if current():
                window.show_error(
                    "Confirmation de retrait indisponible. Aucun souhait modifié.",
                    "Liseuses",
                )

    button.connect("clicked", clicked)
    detail.add_actions([button])
    return True


__all__ = ["append_reader_intent_action"]
