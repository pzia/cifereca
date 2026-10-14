"""GTK presentation for Kobo/Paris synchronization settings (#1127).

This builder receives Gtk/Adw explicitly. Importing the module remains safe
in Core/CLI headless environments; no settings persistence, network, DRM,
new signals or TaskRunner operations occur here. Widgets remain owned by
MainWindow and participate in its existing dirty/save callbacks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def kobo_backup_action_state(*, persisted_enabled: bool, draft_enabled: bool, dirty: bool) -> tuple[str, bool]:
    """Status of legacy opening acquisition, not permission to download here."""
    if dirty or type(draft_enabled) is not bool or draft_enabled is not persisted_enabled:
        return ("Modifications à enregistrer : aucun téléchargement depuis les Paramètres.", False)
    if persisted_enabled is not True:
        return ("Téléchargements au démarrage désactivés ; l’actualisation en ligne reste possible.", False)
    return (
        "Téléchargements Kobo au démarrage activés (tous les comptes éligibles) "
        "si intervalle écoulé. Gérer les sources sans télécharger ici.", True,
    )


@dataclass(frozen=True)
class KoboSyncSettings:
    backup_switch: Any
    backup_status: Any
    run_backup_now: Any
    unlinked_switch: Any
    library_dir_row: Any
    backup_dir_row: Any
    backup_interval_row: Any
    vault_profiles: list[str]
    vault_profile_drop: Any


def build_kobo_sync_section(
    *, section: Any, download_group: Any, config: Any, Gtk: Any, Adw: Any,
) -> KoboSyncSettings:
    """Show the legacy opening policy separately from safe source operations."""
    backup_group = Adw.PreferencesGroup(); backup_group.set_title("Kobo : téléchargements optionnels, distincts des notices"); section.append(backup_group)
    backup_auto_row = Adw.ActionRow(); backup_auto_row.set_title("Télécharger les livres Kobo au démarrage (tous les comptes)")
    backup_auto_row.set_subtitle("Ancien réglage global : peut télécharger des fichiers au prochain démarrage, contrairement à l’actualisation des notices par source.")
    backup_switch = Gtk.Switch(); backup_switch.set_active((config.get("kobo_backup.enabled", False) is True)); backup_switch.set_valign(Gtk.Align.CENTER); backup_auto_row.add_suffix(backup_switch); backup_group.add(backup_auto_row)
    # The source sync page only refreshes online metadata. Downloading books
    # is a separate, explicit operation with the persisted safety settings.
    manual_row = Adw.ActionRow()
    manual_row.set_title("Gérer mes sources Kobo dans Synchronisation")
    status, allowed = kobo_backup_action_state(
        persisted_enabled=(config.get("kobo_backup.enabled", False) is True),
        draft_enabled=(config.get("kobo_backup.enabled", False) is True), dirty=False,
    )
    manual_row.set_subtitle(status)
    backup_status = manual_row
    run_backup_now = Gtk.Button(label="Ouvrir Synchronisation…")
    # Navigation does not start a transfer; it remains available even when
    # legacy automatic download is disabled or the draft is unsaved.
    run_backup_now.set_sensitive(True)
    run_backup_now.set_valign(Gtk.Align.CENTER)
    manual_row.add_suffix(run_backup_now)
    backup_group.add(manual_row)
    unlinked_row = Adw.ActionRow()
    unlinked_row.set_title("Dérogation historique : livres locaux non rapprochés")
    unlinked_row.set_subtitle(
        "Risque de doublons : une dérogation déjà enregistrée peut être "
        "révoquée ici, mais ne peut plus être activée depuis les Paramètres. "
        "L’audit Kobo doit précéder toute nouvelle acquisition incertaine."
    )
    previous_unlinked = config.get("kobo_backup.allow_unlinked_acquisition", False) is True
    unlinked_switch = Gtk.Switch()
    unlinked_switch.set_active(previous_unlinked)
    unlinked_switch.set_valign(Gtk.Align.CENTER)
    unlinked_row.add_suffix(unlinked_switch)
    backup_group.add(unlinked_row)
    # No new unsafe bypass. Still allow an existing true setting to be
    # explicitly revoked, persisted through the unchanged Settings contract.
    revoked = [not previous_unlinked]
    def prevent_new_unlinked_override(widget, _param):
        if not widget.get_active():
            revoked[0] = True
        elif revoked[0]:
            widget.set_active(False)
    unlinked_switch.connect("notify::active", prevent_new_unlinked_override)
    library_dir_row = Adw.EntryRow(); library_dir_row.set_title("Bibliothèque locale possédée par cifereca")
    library_dir_row.set_text(str(config.get("library.directory", "~/EBooks") or "~/EBooks")); download_group.add(library_dir_row)
    backup_dir_row = Adw.EntryRow(); backup_dir_row.set_title("Sous-répertoire/override Kobo (vide = bibliothèque locale)")
    backup_dir_row.set_text(str(config.get("kobo_backup.directory", "") or "")); backup_group.add(backup_dir_row)
    backup_interval_row = Adw.EntryRow(); backup_interval_row.set_title("Délai minimal au démarrage entre téléchargements Kobo (minutes)")
    backup_interval_row.set_text(str(config.get("kobo_backup.interval_minutes", 60) or 60)); backup_group.add(backup_interval_row)
    vault_profiles = ["Android", "Desktop"]
    current_vault_profile = str(config.get("kobo_backup.display_profile", "Android") or "Android")
    if current_vault_profile not in vault_profiles:
        current_vault_profile = "Android"
    vault_display_row = Adw.ActionRow(); vault_display_row.set_title("DisplayProfile Kobo-Vault")
    vault_display_row.set_subtitle("v2.3 : ACSM est prioritaire quand disponible ; le DisplayProfile pilote ensuite SignedNoDrm/KDRM")
    vault_profile_drop = Gtk.DropDown.new_from_strings(vault_profiles)
    vault_profile_drop.set_selected(vault_profiles.index(current_vault_profile))
    vault_profile_drop.set_valign(Gtk.Align.CENTER); vault_display_row.add_suffix(vault_profile_drop); backup_group.add(vault_display_row)
    return KoboSyncSettings(
        backup_switch=backup_switch, backup_status=backup_status,
        run_backup_now=run_backup_now, unlinked_switch=unlinked_switch,
        library_dir_row=library_dir_row, backup_dir_row=backup_dir_row,
        backup_interval_row=backup_interval_row, vault_profiles=vault_profiles,
        vault_profile_drop=vault_profile_drop,
    )
