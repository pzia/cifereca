"""Settings editor loading and actions; explicit host, dirty and directory ports."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from .paris_adept_presentation import (
    normal_paris_adept_summary, normal_paris_adept_runtime_summary,
)
from .settings_accounts_section import (build_identity_settings_header, build_provider_accounts_header, build_provider_account_buttons)
from .settings_identity_editor import build_identity_settings_editor
from .settings_account_editor import build_provider_account_editor


def _saved_interactive_probe_ready(row: dict[str, Any]) -> bool:
    """Use the same legacy enabled semantics as Config.provider_accounts.

    A persisted account without 'enabled' remains ON. Corrupt truthy values
    must not unlock an authentication action.
    """
    return (
        type(row.get("id")) is str and bool(row["id"])
        and row.get("enabled", True) is True
    )


def _probe_has_unsaved_draft(editor, saved, descriptor) -> bool:
    """Compare against the *rendered* initial form, not raw legacy defaults.

    The old check treated e.g. an option missing in persisted data but shown
    with its descriptor default as an unsaved user edit. We must still refuse
    a genuinely changed secret, option, identity or account status.
    """
    if editor.get("invalid_saved_snapshot") is True:
        return True
    baseline = editor.get("probe_rendered_baseline")
    if type(baseline) is dict:
        return (
            editor["enabled"].get_active() != baseline.get("enabled")
            or editor["identity"].get_text() != baseline.get("identity")
            or editor["label"].get_text() != baseline.get("label")
            or {key: field.get_text() for key, field in editor["fields"].items()}
               != baseline.get("fields")
            or {key: field.get_text() for key, field
                in editor["option_fields"].items()} != baseline.get("option_fields")
        )
    # Compatibility with headless legacy editor shims, which did not project
    # the rendered baseline. This path is not used by real Settings GTK.
    return (
        editor["enabled"].get_active() is not True
        or editor["identity"].get_text() != saved.get("identity_id")
        or editor["label"].get_text() != str(
            saved.get("label") or descriptor.display_name)
        or any(
            row.get_text() != str((saved.get("credentials") or {}).get(key) or "")
            for key, row in editor["fields"].items()
        )
        or any(
            row.get_text() != str(
                (saved.get("options") or {}).get(key, next(
                    (spec.default for spec in
                     getattr(descriptor, "account_options", ())
                     if spec.key == key), ""))
            )
            for key, row in editor["option_fields"].items()
        )
    )


def _current_probe_account(config, *, provider_id, account_id):
    """Resolve the current persisted connection, not the Settings-open copy.

    Save+reconnect may complete while the Settings page remains mounted.
    Selecting the unique saved record also rules out stale/detached credentials.
    """
    try:
        rows = config.provider_accounts()
    except Exception:
        return None
    if type(rows) is not list:
        return None
    matching = [
        row for row in rows if type(row) is dict
        and row.get("provider") == provider_id and row.get("id") == account_id
    ]
    return matching[0] if len(matching) == 1 else None


def _probe_form_matches_saved(editor, persisted, descriptor) -> bool:
    """Typed equality only; no normalization of malformed secret values."""
    if (editor.get("invalid_saved_snapshot") is True
            or not _saved_interactive_probe_ready(persisted)
            or type(persisted.get("identity_id")) is not str
            or not persisted["identity_id"]
            or editor["enabled"].get_active() is not True
            or editor["identity"].get_text() != persisted["identity_id"]
            or editor["label"].get_text() != (
                persisted.get("label") or descriptor.display_name)):
        return False
    creds = persisted.get("credentials")
    opts = persisted.get("options")
    if type(creds) is not dict or type(opts) is not dict:
        return False
    for key, entry in editor["fields"].items():
        value = creds.get(key, "")
        if type(value) is not str or entry.get_text() != value:
            return False
    for key, entry in editor["option_fields"].items():
        spec = next((s for s in getattr(descriptor, "account_options", ())
                     if s.key == key), None)
        if spec is None:
            return False
        value = opts.get(key, getattr(spec, "default", ""))
        if type(value) is not str or entry.get_text() != value:
            return False
    return True


def build_settings_editors(self, *, box, descriptors, public_ids,
                           mark_settings_dirty, library_directory, Gtk, Adw, add_css,
                           page_lifetime=None):
    identities_container = build_identity_settings_header(
        section=box, Gtk=Gtk, add_css=add_css,
    )
    identity_editors: list[dict[str, Any]] = []
    configured_identities = self.config.identities()

    def add_identity_editor(data: dict[str, Any] | None = None) -> None:
        data = dict(data or {})
        existing_ids = {str(editor["id"].get_text()).strip() for editor in identity_editors}
        identity_id = str(data.get("id") or "identite").strip() or "identite"
        base = identity_id
        index = 2
        while identity_id in existing_ids:
            identity_id = f"{base}-{index}"; index += 1
        adept_profile = str(
            data.get("adept_profile")
            or self.service.storage.adept_profile_binding_get("identity", identity_id)
            or ""
        )
        normal_subtitle = (
            normal_paris_adept_summary(
                self.service.normal_paris_adept_status(identity_id)
            )
            if data.get("id")
            else "À configurer · enregistrez l’identité pour vérifier ses liaisons."
        )
        editor, choose_directory, choose_adept, delete_button = build_identity_settings_editor(
            data=data, identity_id=identity_id,
            adept_profile=adept_profile, normal_subtitle=normal_subtitle,
            identities_container=identities_container, Gtk=Gtk, Adw=Adw,
        )
        identity_editors.append(editor)
        directory_row = editor["directory"]
        adept_row = editor["adept_profile"]
        choose_directory.connect(
            "clicked",
            lambda _b, entry=directory_row: self._choose_identity_directory(
                entry, Path(library_directory().strip() or "~/EBooks")
            ),
        )
        choose_adept.connect(
            "clicked", lambda button, entry=adept_row:
            self._choose_adept_profile(entry, button),
        )

        def check_adept(_button, e=editor, saved_id=data.get("id")):
            # Runtime inspection is explicit, never a side effect of opening
            # Settings, switching identities or editing another source.
            if (not saved_id or e.get("deleted")
                    or getattr(self, "_closed", False)
                    or (page_lifetime is not None and not page_lifetime.alive)
                    or e.get("_adept_check_pending") is True):
                return
            # A runtime check concerns the SAVED binding, not an unsaved
            # draft. A save/reopen is required before testing changed inputs.
            saved = self.config.identity(saved_id) or {}
            stored_profile = self.service.storage.adept_profile_binding_get(
                "identity", saved_id,
            ) or ""
            stored_primary = saved.get("primary_paris_connection_id") or ""
            if (e["adept_profile"].get_text() != stored_profile
                    or e["primary_paris_connection_id"].get_text()
                       != stored_primary):
                e["normal_adept_status"].set_subtitle(
                    "À vérifier · enregistrez les modifications avant le contrôle ADEPT."
                )
                return
            start_status = self.service.normal_paris_adept_status(saved_id)
            if start_status.get("status") != "ready":
                e["normal_adept_status"].set_subtitle(
                    normal_paris_adept_summary(start_status)
                )
                return
            generations = (
                getattr(self, "_identity_context_generation", None),
                getattr(self, "_account_context_generation", None),
            )
            e["_adept_check_pending"] = True
            button = e["normal_adept_check"]
            button.set_sensitive(False)

            def current():
                return (
                    not getattr(self, "_closed", False)
                    and not e.get("deleted")
                    and (page_lifetime is None or page_lifetime.alive)
                    and generations == (
                        getattr(self, "_identity_context_generation", None),
                        getattr(self, "_account_context_generation", None),
                    )
                    and e["adept_profile"].get_text() == stored_profile
                    and e["primary_paris_connection_id"].get_text()
                        == stored_primary
                    and self.service.normal_paris_adept_status(saved_id)
                        == start_status
                )

            def release():
                e["_adept_check_pending"] = False
                if page_lifetime is None or page_lifetime.alive:
                    button.set_sensitive(True)

            def done(result):
                release()
                if current():
                    e["normal_adept_status"].set_subtitle(
                        normal_paris_adept_runtime_summary(result)
                    )

            def error(_exc):
                release()
                if current():
                    e["normal_adept_status"].set_subtitle(
                        normal_paris_adept_runtime_summary({
                            "reason": "adept_runtime_unavailable",
                        })
                    )

            try:
                self.runner.submit(
                    "Vérification ADEPT…",
                    lambda: self.service.check_normal_paris_adept_runtime(saved_id),
                    done, error, control=button,
                )
            except Exception:
                release()
                error(None)

        # Legacy test/headless builders may not expose the optional row.
        if editor.get("normal_adept_check") is not None:
            editor["normal_adept_check"].connect("clicked", check_adept)
        def delete_identity(_button, e=editor):
            e["deleted"] = True
            e["group"].set_visible(False)
            mark_settings_dirty("accounts")
        delete_button.connect("clicked", delete_identity)

    for configured_identity in configured_identities:
        add_identity_editor(configured_identity)
    add_identity = Gtk.Button(label="Ajouter une identité")
    def add_identity_clicked(_button):
        add_identity_editor({})
        mark_settings_dirty("accounts")
    add_identity.connect("clicked", add_identity_clicked); box.append(add_identity)

    account_section = build_provider_accounts_header(
        section=box, Gtk=Gtk, add_css=add_css,
    )
    account_section.plugins_button.connect("clicked", lambda _b: self.navigate("plugins"))
    accounts_container = account_section.accounts_container
    account_editors: list[dict[str, Any]] = []
    configured_rows = self.config.provider_accounts()

    def add_account_editor(data: dict[str, Any] | None = None, provider_id: str = "") -> None:
        data = dict(data or {})
        provider_id = str(data.get("provider") or provider_id).lower()
        descriptor = descriptors.get(provider_id)
        if descriptor is None or descriptor.state != "ready" or provider_id in public_ids:
            return
        existing_ids = {str(x.get("id")) for x in account_editors}
        if not data.get("id"):
            existing_ids |= {str(x.get("id")) for x in configured_rows}
        base_id = f"{provider_id}-default"
        account_id = str(data.get("id") or base_id)
        n = 2
        while account_id in existing_ids:
            account_id = f"{provider_id}-{n}"; n += 1

        editor, delete_button = build_provider_account_editor(
            data=data, provider_id=provider_id, account_id=account_id,
            descriptor=descriptor, configured_identities=configured_identities,
            accounts_container=accounts_container, config=self.config,
            Gtk=Gtk, Adw=Adw,
            # Read only already observed Core accounts; never ask providers
            # for linked borrowers while rendering Settings.
            observed_borrowers=getattr(self.service, "accounts", ()),
            observed_loans=getattr(self.service, "last_loans", ()),
        )
        account_editors.append(editor)

        probe_button = editor.get("explicit_probe_button")
        if probe_button is not None:
            eligible = _saved_interactive_probe_ready(data)
            probe_button.set_sensitive(eligible)
            if not eligible:
                editor["explicit_probe_status"].set_subtitle(
                    "Enregistrer et activer ce compte avant la recette.")

            def explicit_probe(_button, *, e=editor, saved=dict(data),
                               pid=provider_id):
                # Closed, value-free diagnostic for the repeated field case
                # where no provider HTTP request follows an attempted test.
                # No credential, account ID, draft or exception is logged.
                def record(phase, status="ok"):
                    try:
                        self.service.diagnostics.add(
                            "PLUGIN", "explicit provider probe UI", status,
                            detail={"provider_id": pid, "phase": phase})
                    except Exception:
                        pass

                record("clicked")
                # Resolve the saved row again: Save+reconnect can happen
                # without rebuilding the Settings widgets. Never run a
                # detached/disabled account, or use a stale credential copy.
                current = _current_probe_account(
                    self.config, provider_id=pid, account_id=saved.get("id"))
                if (not _saved_interactive_probe_ready(current or {})
                        or e.get("deleted") or e.get("_probe_pending")
                        or getattr(self, "_closed", False)
                        or (page_lifetime is not None and not page_lifetime.alive)):
                    record("blocked_unavailable", "warning")
                    return
                # An actually edited draft must be saved before its
                # credentials may be sent. A legacy missing option rendered
                # with its default is not a user edit.
                unchanged_original = current == saved
                form_valid = (
                    not _probe_has_unsaved_draft(e, saved, descriptor)
                    if unchanged_original else
                    _probe_form_matches_saved(e, current, descriptor)
                )
                if (type(current.get("identity_id")) is not str
                        or not current["identity_id"] or not form_valid):
                    e["explicit_probe_status"].set_subtitle(
                        "Enregistrer les modifications avant de tester le compte.")
                    record("blocked_unsaved", "warning")
                    return
                e["_probe_pending"] = True
                button = e["explicit_probe_button"]
                button.set_sensitive(False)
                e["explicit_probe_status"].set_subtitle(
                    "Recette HTTP du compte sélectionné en cours…")

                def release():
                    e["_probe_pending"] = False
                    if page_lifetime is None or page_lifetime.alive:
                        button.set_sensitive(True)

                def done(result):
                    release()
                    status = getattr(result, "status", None)
                    live = status == "connected_ephemeral"
                    observed = status == "observed"
                    record(
                        "test_connected_ephemeral" if live else
                        "completed_unverified" if observed else "unavailable",
                        "ok" if (live or observed) else "warning",
                    )
                    if not e.get("deleted") and (page_lifetime is None or page_lifetime.alive):
                        e["explicit_probe_status"].set_subtitle(
                            "Connexion vérifiée pour ce test. "
                            "Session temporaire refermée ; aucune modification du compte."
                            if live else
                            "Diagnostic effectué sans compte connecté. Consulter le journal."
                            if observed else
                            "Diagnostic indisponible. Consulter le journal brut.")

                def error(_exc):
                    release()
                    record("failed", "warning")
                    if not e.get("deleted") and (page_lifetime is None or page_lifetime.alive):
                        e["explicit_probe_status"].set_subtitle(
                            "Diagnostic refusé. Enregistrer, activer le compte "
                            "et vérifier l’option de recette.")

                try:
                    self.runner.submit(
                        "Recette HTTP fournisseur…",
                        lambda: self.service.explicit_provider_diagnostic(
                            identity_id=current["identity_id"], provider_id=pid,
                            credential_id=current["id"], user_consent=True,
                        ),
                        done, error, control=button,
                    )
                    record("dispatched")
                except Exception:
                    error(None)
            probe_button.connect("clicked", explicit_probe)

        def delete_account(_button, e=editor):
            e["deleted"] = True
            e["group"].set_visible(False)
            mark_settings_dirty("accounts")
        delete_button.connect("clicked", delete_account)

    for configured in configured_rows:
        add_account_editor(configured, str(configured.get("provider") or ""))

    for add, provider_id in build_provider_account_buttons(
        section=box, descriptors=descriptors.values(),
        public_ids=public_ids, Gtk=Gtk,
    ):
        def add_account_clicked(_button, pid=provider_id):
            add_account_editor({}, pid)
            mark_settings_dirty("accounts")
        add.connect("clicked", add_account_clicked)
    return identity_editors, account_editors
