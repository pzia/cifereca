"""P1 #2854: explicit GTK action for a declarative USB target reservation.

Reserves a SHA-named path for the selected local book and chosen reader
through Storage.device_usb_target_set. No scan, copy, remove, DRM decision,
source entitlement, provider request, or automatic transfer is triggered.
"""
from __future__ import annotations

from .reader_library_projection import ReaderLibraryBook


def append_reader_target_action(window, detail, book: ReaderLibraryBook, *, Gtk) -> bool:
    """Show one explicit, non-executable destination choice in Liseuses detail.

    A stale selection, identity/device switch, changed target or forged
    intention fails closed. Storage owns the atomic one-time reservation.
    """
    if (Gtk is None or not callable(getattr(Gtk, "Button", None))
            or getattr(window, "_closed", False)
            or type(book) is not ReaderLibraryBook
            or book.desired_state != "keep"
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
            or getattr(snapshot, "identity_id", None) != identity_id
            or getattr(snapshot, "device_id", None) != device_id
            or getattr(window, "_reader_items", {}).get(book.key) is not book
            or storage is None):
        return False
    from ..core.device_usb_target_proposal import propose_usb_target

    try:
        binding = storage.device_binding_get(device_id)
        intent = storage.device_placement_get(device_id, book.artifact_id)
        artifact = storage.artifact_get(book.artifact_id)
        if (type(binding) is not dict or type(intent) is not dict
                or type(artifact) is not dict
                or intent.get("target_relative_path") is not None):
            return False
        proposal = propose_usb_target(
            identity_id=identity_id, binding=binding,
            intention=intent, artifact=artifact,
            selected_format=artifact.get("media_format"),
            other_intentions=storage.device_placements(device_id=device_id),
        )
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        return False

    generation = (
        getattr(window, "_identity_context_generation", None),
        getattr(window, "_account_context_generation", None),
        getattr(window, "_route_display_token", None),
        getattr(window, "_reader_selection_generation", None),
    )
    button = Gtk.Button(
        label="Réserver emplacement " + proposal.selected_format.upper(),
    )
    button._cifereca_action_id = "reader-usb-target-reserve"
    button.set_tooltip_text(
        "Fixe un chemin dans le dossier géré de la liseuse. "
        "Aucun transfert, aucun droit de copie ni support USB attesté."
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
                    getattr(window, "_route_display_token", None),
                    getattr(window, "_reader_selection_generation", None))
                   != generation):
            return False
        page = getattr(window, "readers_page", None)
        selection = getattr(page, "selection", None)
        get = getattr(selection, "get_selected_item", None)
        selected = get() if callable(get) else None
        if selected is None or getattr(selected, "key", None) != book.key:
            return False
        root = getattr(button, "get_root", None)
        if callable(root) and root() is None:
            return False
        mapped = getattr(button, "get_mapped", None)
        if callable(mapped) and not mapped():
            return False
        return True

    def commit(_button) -> None:
        if not current():
            return
        try:
            live_binding = storage.device_binding_get(device_id)
            live_intent = storage.device_placement_get(device_id, book.artifact_id)
            live_artifact = storage.artifact_get(book.artifact_id)
            placement = storage.placement_get(book.placement_id)
            if (type(placement) is not dict
                    or placement.get("identity_id") != identity_id
                    or placement.get("artifact_id") != book.artifact_id
                    or placement.get("state") != "present"
                    or type(live_intent) is not dict
                    or live_intent.get("target_relative_path") is not None):
                raise ValueError("reader_source_or_intent_changed")
            live = propose_usb_target(
                identity_id=identity_id, binding=live_binding,
                intention=live_intent, artifact=live_artifact,
                selected_format=proposal.selected_format,
                other_intentions=storage.device_placements(device_id=device_id),
            )
            if live != proposal or not current():
                return
            # This SQLite transaction is the authority for the immutable
            # reservation and collision guard. It has no USB file side effect.
            storage.device_usb_target_set(
                identity_id=identity_id, device_id=device_id,
                artifact_id=book.artifact_id,
                target_relative_path=proposal.target_relative_path,
            )
        except (ValueError, OSError, TypeError, KeyError, AttributeError):
            if current():
                window.show_error(
                    "Destination non réservée : vérifiez le livre, le profil et la liseuse.",
                    "Liseuses",
                )
            return
        if current():
            window._render_readers()
            window._reader_status_label.set_label(
                "Destination USB réservée · aucun fichier transféré. "
                "Les droits, le format de la liseuse et le support restent à vérifier."
            )

    button.connect("clicked", commit)
    detail.add_actions([button])
    return True


__all__ = ["append_reader_target_action"]
