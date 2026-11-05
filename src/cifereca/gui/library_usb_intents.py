"""P1 #2854: declarative reader intentions on the existing Library detail.

Only SQLite *wishes* are written on a deliberate click. No USB inventory,
file transfer, delete, DRM, supplier request or source entitlement is granted.
A local artifact and exact currently selected identity must be reverified.
"""
from __future__ import annotations

from typing import Any


_MAX_READERS = 16


def _selected_local_artifact(window, *, placement_id: str, identity_id: str) -> str | None:
    """Require the existing exact local placement, not the catalog row's SHA."""
    storage = getattr(getattr(window, "service", None), "storage", None)
    getter = getattr(storage, "placement_get", None)
    if not callable(getter):
        return None
    try:
        placement = getter(placement_id)
    except Exception:
        return None
    if (type(placement) is not dict
            or placement.get("placement_id") != placement_id
            or placement.get("identity_id") != identity_id
            or placement.get("state") != "present"
            or type(placement.get("artifact_id")) is not str
            or not placement["artifact_id"]):
        return None
    return placement["artifact_id"]


def _safe_device_label(binding: dict[str, Any], ordinal: int) -> str:
    label = binding.get("label")
    if (type(label) is str and 0 < len(label) <= 90
            and label == label.strip() and label.isprintable()):
        return label
    return f"Liseuse {ordinal}"


def build_library_usb_intents(
    window, detail, *,
    Gtk, placement_id: str, identity_id: str,
):
    """Return an optional dropdown; build no action for remote or foreign rows.

    Called on a selected book only, never for each list row. The callback
    verifies identity, selection epoch, placement, device binding and the
    intended persisted operation immediately before the exact SQLite write.
    """
    if getattr(window, "_closed", False):
        return None
    service = getattr(window, "service", None)
    current = getattr(service, "current_identity", None)
    if (type(identity_id) is not str or not identity_id
            or getattr(current, "identity_id", None) != identity_id
            or type(placement_id) is not str or not placement_id):
        return None
    storage = getattr(service, "storage", None)
    if storage is None:
        return None
    artifact_id = _selected_local_artifact(
        window, placement_id=placement_id, identity_id=identity_id,
    )
    if artifact_id is None:
        return None
    try:
        bindings = storage.device_bindings(identity_id=identity_id)
    except Exception:
        return None
    if (type(bindings) is not list or not 1 <= len(bindings) <= _MAX_READERS):
        return None
    devices = []
    known = set()
    status_rows = []
    for ordinal, binding in enumerate(bindings, start=1):
        if (type(binding) is not dict
                or binding.get("identity_id") != identity_id
                or type(binding.get("device_id")) is not str
                or not binding["device_id"] or binding["device_id"] in known):
            return None
        device_id = binding["device_id"]
        known.add(device_id)
        metadata = binding.get("metadata")
        fingerprint = (
            metadata.get("usb_fingerprint")
            if type(metadata) is dict else None
        )
        # Legacy device bindings may represent a different target kind.
        # A configured USB identity must be explicit and format-bounded;
        # the actual mounted volume is independently attested by the scanner.
        if (type(fingerprint) is not str
                or not 8 <= len(fingerprint) <= 128
                or any(char not in
                       "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                       "0123456789._:-" for char in fingerprint)):
            continue
        try:
            placed = storage.device_placement_get(device_id, artifact_id)
        except Exception:
            return None
        if placed is not None:
            # A reader may have been reassigned: do not show the previous
            # profile's stored intent, title, path or observation.
            if (type(placed) is not dict
                    or type(placed.get("metadata")) is not dict
                    or placed["metadata"].get("intent_identity_id") != identity_id):
                return None
        desired = placed.get("desired_state") if placed is not None else None
        if desired not in (None, "keep", "remove"):
            return None
        label = _safe_device_label(binding, ordinal)
        label_state = {
            None: "Aucun souhait",
            "keep": "Souhait : mettre",
            "remove": "Souhait : retirer",
        }[desired]
        status_rows.append((
            label,
            label_state + " · Présence actuelle non confirmée"
            + (" · Appareil désactivé" if binding.get("enabled") is not True else ""),
        ))
        devices.append((device_id, label, desired, fingerprint))
    if not devices:
        return None
    menu = Gtk.MenuButton(label="Liseuses…")
    menu._cifereca_action_id = "usb-intents"
    menu.set_tooltip_text(
        "Enregistrer Mettre/Retirer comme souhaits ; aucune copie ou suppression USB."
    )
    popover = Gtk.Popover()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.set_margin_top(8)
    box.set_margin_bottom(8)
    box.set_margin_start(8)
    box.set_margin_end(8)
    generation = getattr(window, "_identity_context_generation", None)

    def still_current(button) -> bool:
        if (getattr(window, "_closed", False)
                or getattr(getattr(service, "current_identity", None),
                           "identity_id", None) != identity_id
                or getattr(window, "_identity_context_generation", None)
                           != generation):
            return False
        root = getattr(button, "get_root", None)
        if callable(root) and root() is None:
            return False
        mapped = getattr(button, "get_mapped", None)
        if callable(mapped) and not mapped():
            return False
        return True

    def clicked(button, *, device_id: str, desired: str,
                fingerprint: str) -> None:
        if not still_current(button):
            return
        try:
            owned = _selected_local_artifact(
                window, placement_id=placement_id,
                identity_id=identity_id,
            )
            if owned != artifact_id:
                raise ValueError("local placement changed")
            # The physical source is not copied, but a missing/symlinked
            # local file must not silently become a positive intent.
            service.local_book_path(placement_id, identity_id=identity_id)
            live = storage.device_binding_get(device_id)
            live_meta = live.get("metadata") if type(live) is dict else None
            if (type(live) is not dict
                    or live.get("identity_id") != identity_id
                    or type(live_meta) is not dict
                    or live_meta.get("usb_fingerprint") != fingerprint):
                raise ValueError("device binding changed")
            # A stale 'remove' button must not create an intention for a
            # previously unmanaged file. Storage validates original owner
            # under BEGIN IMMEDIATE again.
            if desired == "remove":
                saved = storage.device_placement_get(device_id, artifact_id)
                if (type(saved) is not dict
                        or saved.get("desired_state") != "keep"
                        or type(saved.get("metadata")) is not dict
                        or saved["metadata"].get("intent_identity_id") != identity_id):
                    raise ValueError("previous device intent changed")
            storage.device_usb_intent_set(
                identity_id=identity_id, device_id=device_id,
                artifact_id=artifact_id, desired_state=desired,
            )
        except Exception:
            if still_current(button):
                window.show_error(
                    "Intention non enregistrée : vérifiez le profil, le livre et la liseuse.",
                    "Liseuses",
                )
            return
        if still_current(button):
            window._activity_result(
                "Souhait de liseuse enregistré. Aucun fichier n’a été transféré ou supprimé."
            )

    for device_id, label, desired, fingerprint in devices:
        next_state = "remove" if desired == "keep" else "keep"
        action = "Retirer de " if next_state == "remove" else "Mettre sur "
        button = Gtk.Button(label=action + label)
        button._cifereca_action_id = "usb-intent:" + next_state + ":" + device_id
        button.add_css_class("flat")
        button.set_tooltip_text(
            "Enregistrer un souhait de synchronisation ; aucune opération USB immédiate."
        )
        button.connect(
            "clicked",
            lambda clicked_button, device_id=device_id, state=next_state,
                   fingerprint=fingerprint:
                clicked(clicked_button, device_id=device_id, desired=state,
                        fingerprint=fingerprint),
        )
        box.append(button)
    popover.set_child(box)
    menu.set_popover(popover)
    if callable(getattr(detail, "add_group", None)):
        detail.add_group("Liseuses · intentions", status_rows)
    return menu


__all__ = ["build_library_usb_intents"]
