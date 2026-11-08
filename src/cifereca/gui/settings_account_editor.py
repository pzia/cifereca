"""GTK account rows for Settings → Identités et comptes (CoreUI P0 #1127).

The builder owns widgets only; MainWindow retains source authorization,
account-id allocation, create/delete callbacks, dirty/revision tracking and
the existing save pipeline. Gtk/Adw are injected to remain CLI/headless safe.
"""
from __future__ import annotations

from typing import Any

from .borrower_scoped_options import linked_borrower_choices
from .settings_loan_observation import loan_cache_observations


def build_provider_account_editor(
    *, data: dict[str, Any], provider_id: str, account_id: str,
    descriptor: Any, configured_identities: list[dict[str, Any]],
    accounts_container: Any, config: Any, Gtk: Any, Adw: Any,
    observed_borrowers: Any = (), observed_loans: Any = (),
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
    # A persisted account with no valid owner must not look like it belongs
    # to the first profile. Require an explicit choice before any save.
    # Newly created accounts retain the preselected default identity.
    saved_owner = data.get("identity_id")
    existing = type(data.get("id")) is str and bool(data["id"])
    chosen_owner = (
        saved_owner.strip() if type(saved_owner) is str
        and saved_owner.strip() else ("" if existing else default_identity_id)
    )
    identity_row.set_text(chosen_owner); group.add(identity_row)
    raw_credentials = data.get("credentials", {})
    raw_options = data.get("options", {})
    invalid_saved_snapshot = (
        type(raw_credentials) is not dict or type(raw_options) is not dict
        or ("enabled" in data and type(data["enabled"]) is not bool)
    )
    # An existing, declared TEXT value that is not actually text must not be
    # rendered as blank/default and then overwritten by an unrelated Save.
    # Retired/undeclared fields remain opaque and are preserved verbatim.
    if not invalid_saved_snapshot:
        declared_credentials = getattr(descriptor, "credential_fields", ())
        declared_options = getattr(descriptor, "account_options", ())
        invalid_saved_snapshot = any(
            field.key in raw_credentials and type(raw_credentials[field.key]) is not str
            for field in declared_credentials
        ) or any(
            field.key in raw_options and type(raw_options[field.key]) is not str
            for field in declared_options
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
        if field.key in options:
            # Explicitly persisted empty strings are intentional. Never
            # resurrect a retired global/provider default over an existing
            # per-account value during an unrelated Settings save.
            stored = options[field.key]
            selected = stored if type(stored) is str else ""
            # A malformed stored value is separately flagged above, so an
            # unrelated save cannot replace it with a plausible default.
        else:
            fallback = None
            if field.legacy_config_key and existing:
                # Unscoped legacy values belong to historical account editors
                # only. A new connection must use its descriptor default,
                # never inherit another account's former global setting.
                provider_option = getattr(config, "provider_option", None)
                if callable(provider_option):
                    # A legacy value belongs to one exact connection. With
                    # no credential_id, Config.provider_option reads the
                    # provider's *first* account and could silently copy its
                    # endpoint into another (or a newly created) account.
                    fallback = provider_option(
                        provider_id, field.key, None, credential_id=account_id,
                    )
                if not fallback and hasattr(config, "get"):
                    fallback = config.get(field.legacy_config_key)
            # Missing optional text has no value; never display "None".
            default = field.default if type(field.default) is str else ""
            selected = next(
                (value for value in (fallback, default)
                 if type(value) is str and value),
                "",
            )
        entry.set_text(selected)
        group.add(entry)
        option_fields[field.key] = entry
        help_text = getattr(field, 'help_text', '')
        if type(help_text) is str and help_text:
            note = Adw.ActionRow()
            note.set_title(field.label)
            note.set_subtitle(help_text)
            group.add(note)
    # Declarative borrower-specific fields consume only accounts previously
    # observed by Core; opening Settings never connects, inventories or GETs.
    # Persisted borrower keys remain available for offline re-editing.
    scoped_option_fields: dict[str, dict[str, dict[str, Any]]] = {}
    for spec in getattr(descriptor, "borrower_scoped_options", ()):
        scoped_option_fields[spec.key] = {}
        stored = options.get(spec.key, {})
        try:
            borrowers = linked_borrower_choices(
                stored, spec, observed_borrowers,
                provider_id=provider_id, credential_id=account_id,
                identity_id=chosen_owner,
            )
        except ValueError:
            invalid_saved_snapshot = True
            borrowers = ()
        for borrower, label in borrowers:
            branch_fields = {}
            for medium, branch_name in spec.branches:
                entry = Adw.EntryRow()
                entry.set_title(f"{spec.label} · {label} · {branch_name}")
                saved = stored.get(borrower, {}).get(medium) if type(stored) is dict else None
                entry.set_text(saved if type(saved) is str else spec.default)
                group.add(entry)
                branch_fields[medium] = entry
            scoped_option_fields[spec.key][borrower] = branch_fields
        if borrowers and type(getattr(spec, "help_text", None)) is str and spec.help_text:
            note = Adw.ActionRow()
            note.set_title(spec.label)
            note.set_subtitle(spec.help_text)
            group.add(note)
    account_note = getattr(descriptor, "account_note", None)
    if option_fields and account_note:
        note = Adw.ActionRow()
        note.set_title(account_note[0])
        note.set_subtitle(account_note[1])
        group.add(note)
    # Settings projects positive local observations only. Existing loans may
    # belong to other identities or credentials; never infer a provider quota,
    # a zero-loan account or a remaining allowance from this cached list.
    # The text is read-only and never participates in the settings draft.
    if getattr(getattr(descriptor, "capabilities", None), "loans", None) is True:
        for title, subtitle in loan_cache_observations(
            provider_id=provider_id, credential_id=account_id,
            identity_id=chosen_owner, observed_borrowers=observed_borrowers,
            cached_loans=observed_loans,
        ):
            observation = Adw.ActionRow()
            observation.set_title(title)
            observation.set_subtitle(subtitle)
            group.add(observation)
    # An explicit per-account diagnostic is separate from startup reconnect.
    # The host binds the callback only for a persisted enabled account.
    probe_button = None
    probe_status = None
    if getattr(descriptor, "interactive_authentication", False) is True:
        probe_row = Adw.ActionRow()
        probe_row.set_title("Vérifier la connexion au compte")
        probe_row.set_subtitle(
            "Une tentative SSO ponctuelle avec les identifiants enregistrés. "
            "La session est refermée et n'active pas la synchronisation.")
        probe_button = Gtk.Button(label="Tester ce compte")
        probe_button.set_valign(Gtk.Align.CENTER)
        probe_row.add_suffix(probe_button)
        group.add(probe_row)
        probe_status = probe_row
    delete_row = Adw.ActionRow(); delete_row.set_title("Supprimer cette source de la configuration")
    delete_button = Gtk.Button(label="Supprimer"); delete_button.add_css_class("destructive-action"); delete_button.set_valign(Gtk.Align.CENTER); delete_row.add_suffix(delete_button); group.add(delete_row)
    # The preview values are the baseline for the explicit connection test.
    # Compare *what GTK actually rendered*, not raw storage: absent legacy
    # options may display declared defaults without being modified. Never
    # store this baseline in diagnostics or any persistent configuration.
    def rendered_text(widget):
        # Standard GTK exposes get_text(); lightweight headless widget stubs
        # store their last set_text value directly.
        getter = getattr(widget, "get_text", None)
        return getter() if callable(getter) else widget.text

    probe_rendered_baseline = {
        "enabled": saved_enabled is True,
        "identity": chosen_owner,
        "label": rendered_text(label_row),
        "fields": {key: rendered_text(widget) for key, widget in fields.items()},
        "option_fields": {
            key: rendered_text(widget) for key, widget in option_fields.items()
        },
    }
    editor = {
        "id": account_id, "provider": provider_id, "group": group, "enabled": enabled,
        "label": label_row, "identity": identity_row, "fields": fields,
        "option_fields": option_fields,
        "scoped_option_fields": scoped_option_fields,
        "original_options": options, "original_credentials": credentials,
        "original_account": dict(data),
        "invalid_saved_snapshot": invalid_saved_snapshot,
        "probe_rendered_baseline": probe_rendered_baseline,
        "deleted": False,
        "explicit_probe_button": probe_button,
        "explicit_probe_status": probe_status,
    }
    return editor, delete_button
