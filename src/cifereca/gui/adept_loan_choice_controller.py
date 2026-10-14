"""Explicit ADEPT manual loan-binding dialog (#1051).

The UI never guesses a match from title/path: only the user's chosen exact
profile ID and loan ID reach the Core service. Window ownership and the
existing TaskRunner queue remain unchanged; GTK/Adw are injected.
"""
from __future__ import annotations

from ..task_queue import TaskPriority


def _selected_index(dropdown, count: int) -> int | None:
    index = dropdown.get_selected()
    if isinstance(index, int) and 0 <= index < count:
        return index
    return None


def present_adept_loan_choices(window, acquisition_id, loan, result, *, Gtk, Adw) -> None:
    if getattr(window, "_closed", False):
        return
    options = result["options"]
    if not options:
        window.toast(
            "Aucun prêt ADEPT dans les profils liés"
            if result["complete"] else
            "Inventaire incomplet : aucun prêt disponible",
            6,
        )
        return
    dialog = Adw.MessageDialog.new(
        window,
        "Rattacher un prêt ADEPT",
        "Choisissez le prêt correspondant au fichier local. "
        "Le titre aide au choix ; il ne constitue pas une preuve automatique. "
        "Aucun livre ne sera téléchargé ni restitué.",
    )
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    title = Gtk.Label(label=loan.title, xalign=0)
    title.set_wrap(True)
    box.append(title)
    if not result["complete"]:
        warning = Gtk.Label(
            label="Inventaire partiel : seuls les profils lus avec succès sont proposés.",
            xalign=0,
        )
        warning.set_wrap(True)
        box.append(warning)
    labels = [
        f"{option['title']} · {option['profile_name']} · {option['state']} · {option['loan_id']}"
        for option in options
    ]
    dropdown = Gtk.DropDown.new_from_strings(labels)
    dropdown.set_enable_search(True)
    dropdown.set_selected(Gtk.INVALID_LIST_POSITION)
    dropdown.set_tooltip_text("Titre · profil · état · identifiant ADEPT")
    box.append(dropdown)
    detail = Gtk.Label(
        label="Sélectionnez un prêt pour voir sa validité et son chemin connu.",
        xalign=0,
    )
    detail.set_wrap(True)
    box.append(detail)
    dialog.set_extra_child(box)
    dialog.add_response("cancel", "Annuler")
    dialog.add_response("attach", "Confirmer le rattachement")
    dialog.set_response_enabled("attach", False)
    dialog.set_close_response("cancel")

    identity_generation = getattr(window, "_identity_context_generation", None)
    account_generation = getattr(window, "_account_context_generation", None)

    def context_current() -> bool:
        return (
            not getattr(window, "_closed", False)
            and getattr(window, "_identity_context_generation", None) == identity_generation
            and getattr(window, "_account_context_generation", None) == account_generation
        )

    def selected(_dropdown, _pspec):
        if not context_current():
            return
        index = _selected_index(dropdown, len(options))
        dialog.set_response_enabled("attach", index is not None)
        if index is not None:
            option = options[index]
            detail.set_label(
                f"Validité : {option.get('validity') or 'Inconnue'}\n"
                f"Chemin ADEPT : {option.get('local_book') or 'Non fourni'}"
            )

    dropdown.connect("notify::selected", selected)

    def response(_dialog, choice):
        if choice != "attach" or not context_current():
            return
        index = _selected_index(dropdown, len(options))
        if index is None:
            return
        option = options[index]

        def ok(_result):
            if not context_current():
                return
            window.loan_page.detail.show_loan(loan)
            window.refresh_library(False)
            window._activity_result("Prêt ADEPT rattaché · fichier conservé")

        def error(exc):
            if context_current():
                window.show_error(str(exc), "Rattachement impossible")

        window.runner.submit(
            "Rattachement du prêt ADEPT…",
            lambda: window.service.attach_adept_loan(
                acquisition_id, option["profile_id"], option["loan_id"],
            ),
            ok,
            error,
            priority=TaskPriority.USER_ACTION,
            kind="adept-binding",
        )

    dialog.connect("response", response)
    dialog.present()
