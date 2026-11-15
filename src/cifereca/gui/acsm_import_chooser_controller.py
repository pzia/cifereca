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
    identity_id = getattr(identity, "identity_id", None)
    identity_generation = getattr(window, "_identity_context_generation", None)
    account_generation = getattr(window, "_account_context_generation", None)
    # Native scalars only: an untrusted subclass may override comparison.
    if (type(identity_id) is not str or not 0 < len(identity_id) <= 256
            or not identity_id.isprintable() or identity_id != identity_id.strip()
            or type(identity_generation) is not int or identity_generation < 0
            or type(account_generation) is not int or account_generation < 0):
        window.show_error("Choisissez une identité pour importer un ACSM")
        return

    def current() -> bool:
        current_identity = getattr(
            getattr(window.service, "current_identity", None), "identity_id", None,
        )
        current_identity_generation = getattr(
            window, "_identity_context_generation", None,
        )
        current_account_generation = getattr(
            window, "_account_context_generation", None,
        )
        return (
            not getattr(window, "_closed", False)
            and type(current_identity) is str and current_identity == identity_id
            and type(current_identity_generation) is int
            and current_identity_generation == identity_generation
            and type(current_account_generation) is int
            and current_account_generation == account_generation
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
    filt.set_name("ACSM (*.acsm)")
    filt.add_pattern("*.acsm")
    filt.add_pattern("*.ACSM")
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
        if (type(path) is not str or not 0 < len(path) <= 4096
                or "\x00" in path):
            return

        def done(result):
            if not current():
                return
            # An invalid result cannot assert acquisition success; never
            # render a provider-controlled code containing private data.
            if type(result) is not dict or type(result.get("status")) is not str:
                window.show_error(
                    "Résultat de l’import ACSM indisponible.", "Import ACSM impossible",
                )
                return
            status = result["status"]
            if status == "attention_required":
                raw_code = result.get("code")
                code = (raw_code if type(raw_code) is str and raw_code in {
                    "adept_profile_required", "adept_profile_ambiguous",
                    "runtime_missing",
                } else None)
                window.show_attention(
                    "Import en attente : "
                    + (code if code is not None else "runtime DRM à configurer")
                    + "\nLe staging est conservé pour une nouvelle tentative.",
                    code=code or "attention_required",
                )
            elif status in {"complete", "unchanged"}:
                window._activity_result(
                    "ACSM déjà importé" if status == "unchanged" else "ACSM importé",
                )
                window.refresh_library(False)
            else:
                window.show_error(
                    "Résultat de l’import ACSM indisponible.", "Import ACSM impossible",
                )

        window.runner.submit(
            "Import ACSM…",
            lambda: window.service.import_acsm(Path(path), identity_id=identity_id),
            done,
            priority=TaskPriority.USER_ACTION,
            kind="acsm-import",
        )

    chooser.connect("response", chosen)
    chooser.present()
