"""GTK account rows for Settings → Identités et comptes (CoreUI P0 #1127).

The builder owns widgets only; MainWindow retains source authorization,
account-id allocation, create/delete callbacks, dirty/revision tracking and
the existing save pipeline. Gtk/Adw are injected to remain CLI/headless safe.
"""
from __future__ import annotations

from typing import Any


def build_provider_account_editor(
    *, data: dict[str, Any], provider_id: str, account_id: str,
    descriptor: Any, configured_identities: list[dict[str, Any]],
    accounts_container: Any, config: Any, Gtk: Any, Adw: Any,
) -> tuple[dict[str, Any], Any]:
    """Create original provider-specific rows and return (editor, delete button)."""
    group = Adw.PreferencesGroup(); group.set_title(f"{descriptor.display_name} — {account_id}"); accounts_container.append(group)
    enabled_row = Adw.ActionRow(); enabled_row.set_title("Source activée" if not descriptor.credential_fields else "Compte activé")
    enabled = Gtk.Switch(); enabled.set_active(bool(data.get("enabled", True))); enabled.set_valign(Gtk.Align.CENTER); enabled_row.add_suffix(enabled); group.add(enabled_row)
    label_row = Adw.EntryRow(); label_row.set_title("Libellé local"); label_row.set_text(str(data.get("label") or descriptor.display_name)); group.add(label_row)
    identity_row = Adw.EntryRow(); identity_row.set_title("Identité (ID)")
    default_identity_id = str((configured_identities[0].get("id") if configured_identities else "default") or "default")
    identity_row.set_text(str(data.get("identity_id") or default_identity_id)); group.add(identity_row)
    credentials = dict(data.get("credentials") or {})
    fields: dict[str, Gtk.Widget] = {}
    for field in descriptor.credential_fields:
        if field.secret:
            entry = Adw.PasswordEntryRow(); entry.set_title(field.label); entry.set_text(str(credentials.get(field.key) or ""))
        else:
            entry = Adw.EntryRow(); entry.set_title(field.label); entry.set_text(str(credentials.get(field.key) or ""))
        group.add(entry); fields[field.key] = entry
    options = dict(data.get("options") or {})
    option_fields: dict[str, Any] = {}
    for field in getattr(descriptor, "account_options", ()):
        entry = Adw.PasswordEntryRow() if field.secret else Adw.EntryRow()
        entry.set_title(field.label)
        fallback = (config.get(field.legacy_config_key)
                    if field.legacy_config_key and hasattr(config, "get") else None)
        entry.set_text(str(options.get(field.key) or fallback or field.default))
        group.add(entry)
        option_fields[field.key] = entry
    account_note = getattr(descriptor, "account_note", None)
    if option_fields and account_note:
        note = Adw.ActionRow()
        note.set_title(account_note[0])
        note.set_subtitle(account_note[1])
        group.add(note)
    # Compatibility path until the existing providers publish their descriptors.
    base_url_row = None
    opds_url_row = None
    vault_url_row = None
    if not option_fields and provider_id == "paris":
        base_url_row = Adw.EntryRow(); base_url_row.set_title("URL du portail")
        base_url_row.set_text(str(options.get("base_url") or (config.paris_base_url() if hasattr(config, "paris_base_url") else config.get("base_url")) or "https://bibliotheques.paris.fr")); group.add(base_url_row)
    elif not option_fields and provider_id == "gallica":
        opds_url_row = Adw.EntryRow(); opds_url_row.set_title("URL du catalogue OPDS")
        opds_url_row.set_text(str(options.get("opds_url") or "https://gallica.bnf.fr/opds")); group.add(opds_url_row)
        public = Adw.ActionRow(); public.set_title("Accès public")
        public.set_subtitle("Aucun identifiant requis · EPUB + documents sonores publics Gallica")
        group.add(public)
    elif not option_fields and provider_id == "kobo":
        vault_url_row = Adw.EntryRow(); vault_url_row.set_title("URL API Kobo-Vault")
        vault_url_row.set_text(str(options.get("base_url") or "")); group.add(vault_url_row)
        info = Adw.ActionRow(); info.set_title("Connexion indirecte au compte Kobo")
        info.set_subtitle("Kobo-Vault conserve la session Kobo. cifereca utilise uniquement l'URL API et un token dédié.")
        group.add(info)
    delete_row = Adw.ActionRow(); delete_row.set_title("Supprimer cette source de la configuration")
    delete_button = Gtk.Button(label="Supprimer"); delete_button.add_css_class("destructive-action"); delete_button.set_valign(Gtk.Align.CENTER); delete_row.add_suffix(delete_button); group.add(delete_row)
    editor = {
        "id": account_id, "provider": provider_id, "group": group, "enabled": enabled,
        "label": label_row, "identity": identity_row, "fields": fields, "base_url": base_url_row, "opds_url": opds_url_row, "vault_url": vault_url_row,
        "option_fields": option_fields,
        "original_options": options, "deleted": False,
    }
    return editor, delete_button
