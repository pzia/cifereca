"""ACSM chooser and deferred import UI contract (#1132).

The user must select an ACSM file for the active identity explicitly.
GTK and TaskRunner stay owned by MainWindow. A stale dialog can neither
queue work for an unrelated active identity nor repaint a later session.
"""
from __future__ import annotations

from pathlib import Path

from ..task_queue import TaskPriority


def choose_acsm(window, *, Gtk):
    if getattr(window, "_closed", False):
        return
    existing = getattr(window, "_acsm_chooser", None)
    if existing is not None:
        existing.present()
        return
    identity = window.service.current_identity
    if identity is None:
        window.show_error("Choisissez une identité pour importer un ACSM")
        return
    identity_id = identity.identity_id
    identity_generation = getattr(window, "_identity_context_generation", None)
    account_generation = getattr(window, "_account_context_generation", None)

    def current() -> bool:
        return (
            not getattr(window, "_closed", False)
            and getattr(window, "_identity_context_generation", None) == identity_generation
            and getattr(window, "_account_context_generation", None) == account_generation
            and getattr(getattr(window.service, "current_identity", None), "identity_id", None) == identity_id
        )

    chooser = Gtk.FileChooserDialog(
        title=f"Importer un ACSM — {identity.label}",
        transient_for=window,
        modal=True,
        action=Gtk.FileChooserAction.OPEN,
    )
    chooser.add_buttons(
        "Annuler", Gtk.ResponseType.CANCEL,
        "Importer", Gtk.ResponseType.ACCEPT,
    )
    window._acsm_chooser = chooser
    filt = Gtk.FileFilter()
    # The Core validates .bin content before *any* staging or ADEPT action;
    # extension alone is never evidence of an ACSM or a valid BnF loan.
    filt.set_name("ACSM ou jeton ADEPT téléchargé (*.acsm, *.bin)")
    filt.add_pattern("*.acsm")
    filt.add_pattern("*.ACSM")
    filt.add_pattern("*.bin")
    filt.add_pattern("*.BIN")
    chooser.add_filter(filt)
    completed = False

    def chosen(dialog, response):
        nonlocal completed
        if completed:
            return
        completed = True
        active = getattr(window, "_acsm_chooser", None) is dialog
        try:
            if not active or response != Gtk.ResponseType.ACCEPT or not current():
                return
            file = dialog.get_file()
            path = file.get_path() if file else None
        finally:
            dialog.destroy()
            if getattr(window, "_acsm_chooser", None) is dialog:
                window._acsm_chooser = None
        if not path:
            return

        def done(result):
            if not current():
                return
            if result.get("status") == "attention_required":
                window.show_attention(
                    "Import en attente : "
                    + str(result.get("code") or "runtime DRM à configurer")
                    + "\nLe staging est conservé pour une nouvelle tentative.",
                    code=result.get("code") or "attention_required",
                )
            else:
                window._activity_result(
                    "ACSM importé"
                    if result.get("status") != "unchanged" else "ACSM déjà importé"
                )
                window.refresh_library(False)

        window.runner.submit(
            "Import ACSM…",
            lambda: window.service.import_acsm(Path(path), identity_id=identity_id),
            done,
            priority=TaskPriority.USER_ACTION,
            kind="acsm-import",
        )

    chooser.connect("response", chosen)
    chooser.present()
