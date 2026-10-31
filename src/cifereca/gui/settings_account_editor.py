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
    """Create descriptor-driven provider rows and return (editor, delete button)."""
    group = Adw.PreferencesGroup(); group.set_title(f"{descriptor.display_name} — {account_id}"); accounts_container.append(group)
    enabled_row = Adw.ActionRow(); enabled_row.set_title("Source activée" if not descriptor.credential_fields else "Compte activé")
    # Only a literal persisted True can enable an existing source. A newly
    # created account stays OFF; old accounts missing the flag retain their
    # historical default, but corrupt truthy values must fail closed.
    saved_enabled = data.get("enabled", bool(data))
    enabled = Gtk.Switch(); enabled.set_active(saved_enabled is True); enabled.set_valign(Gtk.Align.CENTER); enabled_row.add_suffix(enabled); group.add(enabled_row)
    label_row = Adw.EntryRow(); label_row.set_title("Libellé local"); label_row.set_text(str(data.get("label") or descriptor.display_name)); group.add(label_row)
    identity_row = Adw.EntryRow(); identity_row.set_title("Identité (ID)")
    default_identity_id = str((configured_identities[0].get("id") if configured_identities else "default") or "default")
    identity_row.set_text(str(data.get("identity_id") or default_identity_id)); group.add(identity_row)
    raw_credentials = data.get("credentials", {})
    raw_options = data.get("options", {})
    invalid_saved_snapshot = (
        type(raw_credentials) is not dict or type(raw_options) is not dict
    )
    # Display can recover from corrupt saved data. Do not mutate config and
    # never allow a later unrelated Save to silently replace malformed fields.
    credentials = dict(raw_credentials) if type(raw_credentials) is dict else {}
    fields: dict[str, Gtk.Widget] = {}
    for field in descriptor.credential_fields:
        # A malformed persisted value is not a valid credential. In
        # particular, bools, numbers and containers must not be converted to
        # plausible strings and silently re-saved with an unrelated edit.
        saved_credential = credentials.get(field.key)
        credential_text = saved_credential if type(saved_credential) is str else ""
        if field.secret:
            entry = Adw.PasswordEntryRow(); entry.set_title(field.label); entry.set_text(credential_text)
        else:
            entry = Adw.EntryRow(); entry.set_title(field.label); entry.set_text(credential_text)
        group.add(entry); fields[field.key] = entry
    options = dict(raw_options) if type(raw_options) is dict else {}
    option_fields: dict[str, Any] = {}
    for field in getattr(descriptor, "account_options", ()):
        entry = Adw.PasswordEntryRow() if field.secret else Adw.EntryRow()
        entry.set_title(field.label)
        fallback = None
        if field.legacy_config_key:
            # Preserve historical provider-level overrides before global defaults.
            provider_option = getattr(config, "provider_option", None)
            if callable(provider_option):
                fallback = provider_option(provider_id, field.key, None)
            if not fallback and hasattr(config, "get"):
                fallback = config.get(field.legacy_config_key)
        # Missing optional text has no value; never persist a synthesized "None".
        default = field.default if type(field.default) is str else ""
        selected = next(
            (value for value in (options.get(field.key), fallback, default)
             if type(value) is str and value),
            "",
        )
        entry.set_text(selected)
        group.add(entry)
        option_fields[field.key] = entry
    account_note = getattr(descriptor, "account_note", None)
    if option_fields and account_note:
        note = Adw.ActionRow()
        note.set_title(account_note[0])
        note.set_subtitle(account_note[1])
        group.add(note)
    delete_row = Adw.ActionRow(); delete_row.set_title("Supprimer cette source de la configuration")
    delete_button = Gtk.Button(label="Supprimer"); delete_button.add_css_class("destructive-action"); delete_button.set_valign(Gtk.Align.CENTER); delete_row.add_suffix(delete_button); group.add(delete_row)
    editor = {
        "id": account_id, "provider": provider_id, "group": group, "enabled": enabled,
        "label": label_row, "identity": identity_row, "fields": fields,
        "option_fields": option_fields,
        "original_options": options, "original_credentials": credentials,
        "original_account": dict(data),
        "invalid_saved_snapshot": invalid_saved_snapshot,
        "deleted": False,
    }
    return editor, delete_button
