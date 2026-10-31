"""Capture GTK values and bind validated, revision-safe asynchronous settings commits."""
from __future__ import annotations

from .settings_model import SettingsCommitController
from .settings_draft import build_settings_draft, SettingsValidationError


def bind_settings_commit(self, *, page_lifetime, settings_state,
                         identity_editors, account_editors, descriptors, public_ids,
                         download_settings, application_ui, kobo_settings,
                         settings_bars, ai_ui, ai_cfg, capture_ai_settings,
                         refresh_text, track_all_settings_widgets,
                         update_settings_actions, refresh_kobo_backup_status):
    global_dir_row = download_settings.global_dir_row
    global_ebook_row = download_settings.global_ebook_row
    global_audio_row = download_settings.global_audio_row
    global_cmd_row = download_settings.global_cmd_row
    global_post_row = download_settings.global_post_row
    provider_download_editors = download_settings.provider_download_editors
    exp_switch = application_ui.experimental_switch
    level_drop = application_ui.level_dropdown
    levels = application_ui.levels
    library_dir_row = kobo_settings.library_dir_row
    backup_dir_row = kobo_settings.backup_dir_row
    backup_interval_row = kobo_settings.backup_interval_row
    vault_profiles = kobo_settings.vault_profiles
    vault_profile_drop = kobo_settings.vault_profile_drop
    save, reconnect = settings_bars.save, settings_bars.reconnect
    ai_status = ai_ui.status
    settings_committer = SettingsCommitController(
        self.service.save_configuration, self.runner.submit, self.runner.present_error,
    )

    def capture_settings_values():
        identities = []
        for editor in identity_editors:
            if editor.get("deleted"):
                continue
            row = {key: editor[key].get_text() for key in
                   ("id", "label", "directory", "adept_profile", "languages")}
            row["prefer_vo"] = editor["prefer_vo"].get_active()
            identities.append(row)
        accounts = []
        for editor in account_editors:
            if editor.get("deleted"):
                continue
            row = {key: editor[key] for key in ("id", "provider")}
            row.update({key: editor[key].get_text() for key in ("label", "identity")})
            row["enabled"] = editor["enabled"].get_active()
            row["original_options"] = settings_committer.stage(editor.get("original_options") or {})
            row["fields"] = {key: widget.get_text() for key, widget in editor["fields"].items()}
            row["option_fields"] = {
                key: widget.get_text() for key, widget in editor.get("option_fields", {}).items()
            }
            accounts.append(row)
        values = {
            "identities": identities, "accounts": accounts,
            "provider_downloads": {
                pid: {key: widget.get_text() for key, widget in editor.items()}
                for pid, editor in provider_download_editors.items()
            },
            "levels": tuple(levels), "vault_profiles": tuple(vault_profiles),
            "ai": capture_ai_settings() if settings_state.dirty("ai") else {},
        }
        values["backup_directory"] = backup_dir_row.get_text()
        values["backup_interval"] = backup_interval_row.get_text()
        values["diagnostics_enabled"] = True
        values["experimental_actions"] = exp_switch.get_active()
        values["audiobook_directory"] = global_audio_row.get_text()
        values["download_command"] = global_cmd_row.get_text()
        values["download_directory"] = global_dir_row.get_text()
        values["ebook_directory"] = global_ebook_row.get_text()
        values["post_download_command"] = global_post_row.get_text()
        values["diagnostics_level_index"] = level_drop.get_selected()
        values["library_directory"] = library_dir_row.get_text()
        values["vault_profile_index"] = vault_profile_drop.get_selected()
        return values

    def save_settings(_button=None, *, reconnect_after=False) -> bool:
        if not page_lifetime.alive or getattr(self, "_closed", False):
            return False
        if settings_committer.pending:
            return False
        try:
            draft = build_settings_draft(
                self.config, capture_settings_values(), descriptors=descriptors,
                public_ids=public_ids,
                current_dirs={i.identity_id: i.directory_name for i in self.service.identities},
                has_active_tasks=bool(getattr(self, "_active_tasks", {})),
                ai_dirty=settings_state.dirty("ai"),
            )
        except SettingsValidationError as exc:
            if exc.title is None:
                self.show_error(str(exc))
            else:
                self.show_error(str(exc), exc.title)
            return False
        directory_change = draft.directory_change
        ai_restart_required = draft.ai_restart_required
        new_ai_settings = draft.data.get("llm", {})
        staged = settings_committer.stage(draft.data)
        staged_settings_revisions = settings_state.snapshot()
        def ok(result):
            if not page_lifetime.alive:
                return
            self.diagnostics.enabled = True
            self.diagnostics.level = staged["diagnostics"]["level"]
            from ..logging_utils import set_diagnostics_level
            set_diagnostics_level(self.diagnostics.level)
            message = "Configuration enregistrée"
            if ai_restart_required:
                # Cancel/reset must now restore the newly saved values.
                ai_cfg.clear()
                ai_cfg.update(new_ai_settings)
                message = "Configuration IA enregistrée — redémarrage nécessaire"
                ai_status.set_text(
                    "Enregistré · redémarrer Cifereca pour actualiser "
                    "le service IA partagé par les plugins"
                )

            if result.get("migrated"):
                message = f"Répertoire déplacé · {result['collisions']} collision(s) préservée(s) · bibliothèque actualisée"
                self.refresh_library(False); self.refresh_recent(); self._render_dashboard()
            if hasattr(self, "identity_dropdown"):
                self._populate_accounts()
                self._render_dashboard()
            reporter = getattr(self, "_activity_result", None)
            if callable(reporter):
                reporter(message)
            else:
                self.toast(message, 5)
            self._configure_kobo_backup_timer(run_now=False)
            refresh_text()
            settings_state.commit(staged_settings_revisions)
            track_all_settings_widgets()
            update_settings_actions()
            refresh_kobo_backup_status()
            if reconnect_after:
                self.reconnect_sources()
        return settings_committer.submit(
            "Enregistrement et migration des répertoires…" if directory_change else "Enregistrement de la configuration…",
            staged, ok, control=_button or save,
        )

    def save_and_reconnect(_button=None) -> None:
        save_settings(_button or reconnect, reconnect_after=True)
    return save_settings, save_and_reconnect
