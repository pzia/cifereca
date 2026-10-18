"""GTK-neutral wiring for the Settings AI controls (P0 #1127).

Only the owning window connects these handlers to its widgets.  The UI
lifetime, consent confirmation, asynchronous generation and TaskRunner
contract remain unchanged. No service is contacted when this factory runs.
"""
from __future__ import annotations

import logging
from time import monotonic

from .llm_settings import (
    LLMSettingsError, normalize_llm_settings, destination_summary,
    env_key_is_set, LLMProbeResult, probe_llm_connection_result,
    _probe_outcome,
)


_TRACE_LOG = logging.getLogger("cifereca")


def bind_settings_ai_callbacks(
    self, *, ai_ui, ai_cfg, page_lifetime, settings_state,
    update_settings_actions, Adw, TaskPriority,
):
    ai_enabled = ai_ui.enabled
    ai_endpoint = ai_ui.endpoint
    ai_model = ai_ui.model
    ai_remote = ai_ui.remote
    ai_key = ai_ui.key_env
    ai_timeout = ai_ui.timeout
    ai_input_limit = ai_ui.input_limit
    ai_output_limit = ai_ui.output_limit
    ai_status = ai_ui.status
    ai_test = ai_ui.test_button
    ai_reset = ai_ui.reset_button
    ai_ui.preset_button.connect(
        "clicked", lambda *_: ai_endpoint.set_text(
            "http://127.0.0.1:11434/v1/chat/completions"
        ),
    )

    def capture_ai_settings():
        return {
            "enabled": ai_enabled.get_active(),
            "provider": "openai-compatible",
            "endpoint": ai_endpoint.get_text(),
            "model": ai_model.get_text(),
            "allow_remote": ai_remote.get_active(),
            "api_key_env": ai_key.get_text(),
            "timeout_seconds": ai_timeout.get_text(),
            "max_input_chars": ai_input_limit.get_text(),
            "max_output_tokens": ai_output_limit.get_text(),
        }

    def read_ai_settings():
        return normalize_llm_settings(capture_ai_settings())

    ai_probe_version = [0]

    def ai_setting_changed(*_args):
        ai_probe_version[0] += 1
        host, remote = destination_summary({
            "endpoint": ai_endpoint.get_text(),
            "model": ai_model.get_text(),
            "enabled": ai_enabled.get_active(),
            "allow_remote": ai_remote.get_active(),
            "api_key_env": ai_key.get_text(),
            "timeout_seconds": ai_timeout.get_text(),
            "max_input_chars": ai_input_limit.get_text(),
            "max_output_tokens": ai_output_limit.get_text(),
        })
        ai_status.set_text(
            "Désactivé (modifications non enregistrées)"
            if not ai_enabled.get_active() else
            f"Non testé · {'distant' if remote else 'local'} · {host} · "
            f"clé d’environnement {'définie' if env_key_is_set({'api_key_env': ai_key.get_text()}) else 'absente'}"
        )

    def endpoint_changed(*_args):
        # Editing the endpoint revokes a previously granted remote opt-in.
        if ai_remote.get_active():
            ai_remote.set_active(False)
        ai_setting_changed()

    ai_endpoint.connect("notify::text", endpoint_changed)
    for widget, signal in (
        (ai_enabled, "notify::active"), (ai_remote, "notify::active"),
        (ai_model, "notify::text"), (ai_key, "notify::text"),
        (ai_timeout, "notify::text"), (ai_input_limit, "notify::text"),
        (ai_output_limit, "notify::text"),
    ):
        widget.connect(signal, ai_setting_changed)

    def reset_ai(_button):
        ai_enabled.set_active(ai_cfg.get("enabled") is True)
        ai_endpoint.set_text(str(ai_cfg.get("endpoint") or ""))
        ai_model.set_text(str(ai_cfg.get("model") or ""))
        ai_remote.set_active(ai_cfg.get("allow_remote") is True)
        ai_key.set_text(str(ai_cfg.get("api_key_env") or ""))
        ai_timeout.set_text(str(ai_cfg.get("timeout_seconds", 5)))
        ai_input_limit.set_text(str(ai_cfg.get("max_input_chars", 4000)))
        ai_output_limit.set_text(str(ai_cfg.get("max_output_tokens", 256)))
        settings_state.reset("ai")
        ai_status.set_text("Modifications annulées ; aucun test réseau lancé")
        update_settings_actions()

    ai_reset.connect("clicked", reset_ai)

    def submit_ai_probe(settings, *, confirmed=False):
        if not page_lifetime.alive:
            return
        ai_probe_version[0] += 1
        version = ai_probe_version[0]
        ai_status.set_text("Test synthétique en cours…")
        def done(result):
            if version == ai_probe_version[0] and page_lifetime.alive:
                host, _ = destination_summary(settings)
                label = (
                    _probe_outcome(result.status).label
                    if type(result) is LLMProbeResult else "Test non concluant"
                )
                ai_status.set_text(f"{label} · {host} · modèle {settings['model']}")
        def failed(_exc):
            if version == ai_probe_version[0] and page_lifetime.alive:
                ai_status.set_text("Test non concluant ; vérifier la configuration")
        # Log one terminal classification from the worker, never a TaskRunner
        # completion callback: closing the page or editing fields must not
        # erase the actual probe result. A queued task never started emits none.
        def run_probe():
            started = monotonic()
            try:
                outcome = probe_llm_connection_result(
                    settings, remote_confirmed=confirmed,
                )
            except LLMSettingsError:
                outcome = _probe_outcome("invalid_config")
            except TimeoutError:
                outcome = _probe_outcome("timeout")
            except (ConnectionError, OSError):
                outcome = _probe_outcome("backend_unavailable")
            except Exception:
                # Never log exception text, class names, headers or URL.
                outcome = _probe_outcome("internal_error")
            # The connection result is a runtime object, not a security
            # boundary. Rebuild both status and label from our fixed taxonomy.
            outcome = _probe_outcome(
                outcome.status if type(outcome) is LLMProbeResult else "inconclusive"
            )
            elapsed = min(60_000, max(0, int((monotonic() - started) * 1000)))
            _host, remote = destination_summary(settings)
            _TRACE_LOG.info(
                "TRACE llm probe operation=llm_connection_probe "
                "status=%s destination=%s duration_ms=%d",
                outcome.status, "remote" if remote else "local", elapsed,
            )
            return outcome

        # TaskRunner makes the focused test button insensitive. Keep a
        # live keyboard focus anchor without stealing unrelated focus.
        if self.get_focus() is ai_test:
            ai_reset.grab_focus()
        try:
            self.runner.submit(
                "Test connexion IA synthétique",
                run_probe,
                done, failed, control=ai_test,
                priority=TaskPriority.USER_ACTION, kind="settings",
            )
        except Exception:
            ai_test.set_sensitive(True)
            ai_status.set_text("Impossible de démarrer le test")

    def test_ai_connection(_button):
        if not page_lifetime.alive:
            return
        try:
            settings = read_ai_settings()
        except LLMSettingsError as exc:
            ai_status.set_text(str(exc))
            return
        if not settings["enabled"]:
            ai_status.set_text("Activer la fonction IA pour tester la connexion")
            return
        host, remote = destination_summary(settings)
        if remote:
            confirmation_version = ai_probe_version[0]
            confirm = Adw.MessageDialog.new(
                self, "Tester la connexion distante ?",
                f"Une requête synthétique sera envoyée à {host} avec le modèle "
                f"{settings['model']}. Ce test peut être facturé. "
                "Aucune notice ou donnée personnelle ne sera envoyée.",
            )
            confirm.add_response("cancel", "Annuler")
            confirm.add_response("accept", "Autoriser ce test")
            confirm.set_default_response("cancel")
            confirm.set_close_response("cancel")
            def confirmed_probe(_dialog, response):
                if not page_lifetime.alive:
                    return
                if response != "accept":
                    return
                # Never send to a stale destination after edits while
                # the confirmation dialog was open.
                if confirmation_version != ai_probe_version[0]:
                    ai_status.set_text("Paramètres modifiés : renouveler la confirmation")
                    return
                try:
                    if read_ai_settings() != settings:
                        ai_status.set_text("Paramètres modifiés : renouveler la confirmation")
                        return
                except LLMSettingsError:
                    ai_status.set_text("Configuration IA invalide")
                    return
                submit_ai_probe(settings, confirmed=True)
            confirm.connect("response", confirmed_probe)
            confirm.present()
            return
        submit_ai_probe(settings)

    ai_test.connect("clicked", test_ai_connection)

    return capture_ai_settings
