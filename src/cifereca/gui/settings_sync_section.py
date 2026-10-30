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
    """Describe safe source navigation, not permission for local acquisition.

    The existing settings widget is a compatibility alias: its button now
    navigates to the identity-scoped Synchronisation page and never starts a
    global Kobo download batch (#1347).
    """
    if (dirty or type(persisted_enabled) is not bool
            or type(draft_enabled) is not bool or draft_enabled is not persisted_enabled):
        return ("Modifications à enregistrer avant de consulter la synchronisation.", False)
    if persisted_enabled is False:
        return (
            "Ancien réglage global désactivé. Les téléchargements au démarrage "
            "se règlent désormais compte par compte dans Synchronisation ; "
            "l’actualisation des notices ne télécharge aucun livre.", True,
        )
    return (
        "Ancien réglage global enregistré, mais sans effet sur l’acquisition "
        "au démarrage. Seule l’autorisation explicite de chaque compte dans "
        "Synchronisation peut activer ses téléchargements.", True,
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
    """Build original controls in original order, without binding new signals."""
    backup_group = Adw.PreferencesGroup(); backup_group.set_title("Kobo — acquisition locale des fichiers"); section.append(backup_group)
    backup_auto_row = Adw.ActionRow(); backup_auto_row.set_title(
        "Ancien réglage global Kobo (compatibilité)"
    )
    backup_auto_row.set_subtitle(
        "Ancien réglage en lecture seule : il ne lance plus de téléchargements. "
        "Les acquisitions au démarrage se configurent et se révoquent par compte "
        "dans Synchronisation. Il n’existe pas de minuterie périodique de téléchargement."
    )
    backup_switch = Gtk.Switch()
    backup_switch.set_active(config.get("kobo_backup.enabled", False) is True)
    # Keep the persisted legacy bit visible for migration/rollback, but it is
    # not a source-specific consent and must never be editable as one.
    backup_switch.set_sensitive(False)
    backup_switch.set_valign(Gtk.Align.CENTER)
    backup_auto_row.add_suffix(backup_switch)
    backup_group.add(backup_auto_row)
    # The source sync page only refreshes online metadata. Downloading books
    # is a separate, explicit operation with the persisted safety settings.
    manual_row = Adw.ActionRow()
    manual_row.set_title("Synchronisation Kobo — comptes et aperçus")
    status, allowed = kobo_backup_action_state(
        persisted_enabled=(config.get("kobo_backup.enabled", False) is True),
        draft_enabled=(config.get("kobo_backup.enabled", False) is True), dirty=False,
    )
    manual_row.set_subtitle(status)
    backup_status = manual_row
    # Internal field name remains stable for existing Settings composition ports.
    # The button is now navigation-only: no global acquisition without review.
    run_backup_now = Gtk.Button(label="Examiner les comptes Kobo…")
    run_backup_now.set_sensitive(allowed)
    run_backup_now.set_valign(Gtk.Align.CENTER)
    manual_row.add_suffix(run_backup_now)
    backup_group.add(manual_row)
    unlinked_row = Adw.ActionRow(); unlinked_row.set_title(
        "Ancienne dérogation Kobo (compatibilité)"
    )
    unlinked_row.set_subtitle(
        "Ancien réglage en lecture seule : il ne contourne ni les refus explicites "
        "du fournisseur, ni l’intégrité des fichiers, ni l’anti-doublons après "
        "placement. Une présence locale incertaine ne bloque pas à elle seule "
        "une livraison autrement autorisée ; seul le consentement de la source "
        "dans Synchronisation s’applique."
    )
    unlinked_switch = Gtk.Switch()
    unlinked_switch.set_active(config.get("kobo_backup.allow_unlinked_acquisition", False) is True)
    # This historical bypass is inert. It grants no consent and does not
    # override explicit source restrictions. Ambiguous local presence is not
    # an automatic veto on delivery under the canonical P0 Sync contract.
    unlinked_switch.set_sensitive(False)
    unlinked_switch.set_valign(Gtk.Align.CENTER)
    unlinked_row.add_suffix(unlinked_switch)
    backup_group.add(unlinked_row)
    library_dir_row = Adw.EntryRow(); library_dir_row.set_title("Bibliothèque locale possédée par cifereca")
    library_dir_row.set_text(str(config.get("library.directory", "~/EBooks") or "~/EBooks")); download_group.add(library_dir_row)
    backup_dir_row = Adw.EntryRow(); backup_dir_row.set_title("Sous-répertoire/override Kobo (vide = bibliothèque locale)")
    backup_dir_row.set_text(str(config.get("kobo_backup.directory", "") or "")); backup_group.add(backup_dir_row)
    backup_interval_row = Adw.EntryRow(); backup_interval_row.set_title("Délai entre tentatives au démarrage (minutes, minimum 15)")
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
