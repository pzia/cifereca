"""GTK-only Identity + ADEPT editor component for Settings (#1127).

No PyGObject import at module import time. The caller owns the exact saved
identity, ADEPT binding read, profile/directory chooser callbacks, delete
callback, revision tracking and asynchronous commit/rollback. Rendering an
identity never authorizes an ADEPT profile or refreshes a provider.
"""
from __future__ import annotations

from typing import Any


def build_identity_settings_editor(
    *, data: dict[str, Any], identity_id: str, adept_profile: str,
    normal_subtitle: str, identities_container: Any, Gtk: Any, Adw: Any,
) -> tuple[dict[str, Any], Any, Any, Any]:
    """Create the original widgets and return controls for caller-owned signals."""
    prefs = dict(data.get("preferences") or {})
    group = Adw.PreferencesGroup()
    group.set_title(str(data.get("label") or identity_id))
    identities_container.append(group)

    id_row = Adw.EntryRow()
    id_row.set_title("Identifiant stable")
    id_row.set_text(identity_id)
    if data.get("id"):
        id_row.set_sensitive(False)
    group.add(id_row)

    label_row = Adw.EntryRow()
    label_row.set_title("Nom affiché")
    label_row.set_text(str(data.get("label") or identity_id))
    group.add(label_row)

    directory_row = Adw.EntryRow()
    directory_row.set_title("Nom du répertoire bibliothèque")
    directory_row.set_text(str(data.get("directory_name") or identity_id))
    directory_row.set_tooltip_text(
        "Changer ce dossier déplace les fichiers, fusionne sans écrasement et actualise la bibliothèque. Le libellé seul ne déplace rien."
    )
    group.add(directory_row)
    choose_directory = Gtk.Button.new_from_icon_name("folder-open-symbolic")
    choose_directory.set_tooltip_text("Choisir un dossier existant dans la bibliothèque")
    directory_row.add_suffix(choose_directory)

    languages_row = Adw.EntryRow()
    languages_row.set_title("Langues de lecture (ordre, séparées par des virgules)")
    languages_row.set_text(", ".join(
        str(x) for x in (prefs.get("reading_languages") or ["fr"])
    ))
    group.add(languages_row)

    vo_row = Adw.ActionRow()
    vo_row.set_title("Préférer la langue originale")
    vo_switch = Gtk.Switch()
    vo_switch.set_active(bool(prefs.get("prefer_original_language", False)))
    vo_switch.set_valign(Gtk.Align.CENTER)
    vo_row.add_suffix(vo_switch)
    group.add(vo_row)

    adept_row = Adw.EntryRow()
    adept_row.set_title("Profil ADE lié à cette identité (optionnel)")
    adept_row.set_text(adept_profile)
    group.add(adept_row)
    choose_adept = Gtk.Button(label="Choisir…")
    choose_adept.set_valign(Gtk.Align.CENTER)
    choose_adept.set_tooltip_text("Lister les profils Adobe capables de récupérer les prêts ACSM")
    adept_row.add_suffix(choose_adept)

    # This is recorded local state, not Adobe authorization or the exceptional
    # historical borrower/profile resolver from P0 #976.
    normal_row = Adw.ActionRow()
    normal_row.set_title("Parcours normal · Paris principal et ADEPT")
    normal_row.set_subtitle(normal_subtitle)
    normal_row.set_tooltip_text(
        "État local enregistré, sans test Adobe ni connexion réseau. "
        "Après modification, enregistrez et rouvrez les paramètres pour actualiser cet état."
    )
    group.add(normal_row)

    delete_row = Adw.ActionRow()
    delete_row.set_title("Supprimer cette identité de la configuration")
    delete_button = Gtk.Button(label="Supprimer")
    delete_button.add_css_class("destructive-action")
    delete_button.set_valign(Gtk.Align.CENTER)
    delete_row.add_suffix(delete_button)
    group.add(delete_row)

    editor = {
        "group": group, "id": id_row, "label": label_row,
        "directory": directory_row, "languages": languages_row,
        "prefer_vo": vo_switch, "adept_profile": adept_row,
        "deleted": False,
    }
    return editor, choose_directory, choose_adept, delete_button
