"""Identity directory and ADEPT profile choosers with GTK-free ownership (#1132).

MainWindow injects GTK/Gio/Adw. Neither chooser saves settings or changes
identity. A pending response cannot update a field after window teardown
or after another identity/account has been activated.
"""
from __future__ import annotations

from pathlib import Path

from ..identities.directory_selection import directory_choice


def _context(window):
    return (
        getattr(window, "_identity_context_generation", None),
        getattr(window, "_account_context_generation", None),
    )


def choose_identity_directory(window, entry, root: Path, *, Gtk, Gio) -> None:
    if getattr(window, "_closed", False):
        return
    context = _context(window)
    chooser = Gtk.FileChooserNative.new(
        "Choisir un dossier d’identité", window,
        Gtk.FileChooserAction.SELECT_FOLDER, "Choisir", "Annuler",
    )
    chooser.set_current_folder(Gio.File.new_for_path(str(root.expanduser())))
    window._identity_directory_chooser = chooser

    def response(dialog, selected):
        try:
            if getattr(window, "_closed", False) or _context(window) != context:
                return
            # Multiple outstanding native dialogs must not overwrite the
            # editor field based on an older chooser response.
            if getattr(window, "_identity_directory_chooser", None) is not dialog:
                return
            if selected == Gtk.ResponseType.ACCEPT:
                file = dialog.get_file()
                if file is None or not file.get_path():
                    raise ValueError("Choisissez un dossier local.")
                entry.set_text(directory_choice(file.get_path(), root))
        except Exception as exc:
            if not getattr(window, "_closed", False):
                window.show_error(str(exc), "Choix du répertoire impossible")
        finally:
            dialog.destroy()
            if getattr(window, "_identity_directory_chooser", None) is dialog:
                window._identity_directory_chooser = None

    chooser.connect("response", response)
    chooser.show()


def choose_adept_profile(window, entry, control, *, Adw) -> None:
    if getattr(window, "_closed", False):
        return
    context = _context(window)

    def current():
        return not getattr(window, "_closed", False) and _context(window) == context

    def done(choices):
        if not current():
            return
        if not choices:
            window.show_error(
                "Autorisez un profil Adobe capable de récupérer les ACSM "
                "dans Kobo-Adept, puis réessayez.",
                "Aucun profil ADE prêt",
            )
            return
        dialog = Adw.MessageDialog.new(
            window, "Choisir le profil ADE",
            "Choisissez le profil Adobe correspondant aux prêts de cette identité. "
            "Enregistrez ensuite les paramètres, puis relancez la synchronisation.",
        )
        references = {}
        for index, choice in enumerate(choices):
            response = "profile-" + str(index)
            references[response] = choice["reference"]
            dialog.add_response(
                response, choice["name"] + " · " + choice["reference"],
            )
        dialog.add_response("cancel", "Annuler")
        dialog.set_close_response("cancel")

        def selected(_dialog, response):
            if current() and response in references:
                entry.set_text(references[response])

        dialog.connect("response", selected)
        dialog.present()

    def error(exc):
        if current():
            window.show_error(str(exc), "Profils ADE")

    window.runner.submit(
        "Profils ADE…",
        window.service.adept_profile_choices,
        done,
        error,
        control=control,
    )
