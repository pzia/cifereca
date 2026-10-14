"""P0 #1099 — LLM settings without GTK, real network or secrets."""
from __future__ import annotations
from tests.settings_source import settings_source_text

import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.config import Config
from cifereca.gui.llm_settings import (
    LLMSettingsError, normalize_llm_settings, destination_summary,
    env_key_is_set, probe_llm_connection,
)
from cifereca.gui.settings_context import settings_action_state


BASE = {
    "enabled": True, "provider": "openai-compatible",
    "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
    "model": "installed-model", "allow_remote": False, "api_key_env": "LOCAL_TEST_KEY",
    "timeout_seconds": "5", "max_input_chars": "4000", "max_output_tokens": "256",
}


@pytest.mark.parametrize("overrides", [
    {"endpoint": "https://example.org/v1/chat/completions"},
    {"endpoint": "http://example.org/v1/chat/completions", "allow_remote": True},
    {"endpoint": "https://user:pass@example.org/v1/chat/completions", "allow_remote": True},
    {"endpoint": "https://example.org/v1/chat/completions?token=SECRET", "allow_remote": True},
    {"endpoint": "https://example.org/v1/chat/completions#SECRET", "allow_remote": True},
    {"endpoint": "file:///etc/passwd"},
    {"model": ""}, {"model": "x\nhidden"}, {"model": "x" + chr(0x202e) + "spoof"},
    {"api_key_env": "TOKEN=plaintext"}, {"api_key_env": "bad name"},
    {"timeout_seconds": "20"}, {"timeout_seconds": "nan"}, {"max_input_chars": "16001"},
    {"max_output_tokens": "1025"}, {"provider": "incompatible-backend"},
    {"allow_remote": "yes"}, {"enabled": 1},
])
def test_invalid_ai_settings_are_rejected_before_persistence_or_network(overrides):
    with pytest.raises(LLMSettingsError):
        normalize_llm_settings({**BASE, **overrides})


def test_default_disabled_requires_no_model_or_server():
    settings = normalize_llm_settings({"enabled": False})
    assert settings["enabled"] is False
    assert settings["model"] == ""
    assert settings["endpoint"] == ""
    assert probe_llm_connection(settings) == "Désactivé"
    assert settings_action_state("ai", dirty=True).show_save is True
    assert settings_action_state("ai", dirty=False).save_sensitive is False
    assert settings_action_state("ai", dirty=True).show_reconnect is False


def test_environment_variable_name_is_only_persisted_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_TEST_KEY", "VERY-PRIVATE-CREDENTIAL")
    config = Config(tmp_path / "config.json")
    settings = normalize_llm_settings(BASE)
    assert env_key_is_set(settings) is True
    config.set("llm", settings)
    config.set("experimental_actions", True)
    config.save()
    saved = config.path.read_text(encoding="utf-8")
    assert settings["api_key_env"] == "LOCAL_TEST_KEY"
    assert "VERY-PRIVATE-CREDENTIAL" not in saved
    assert config.get("experimental_actions") is True
    assert config.path.stat().st_mode & 0o777 == 0o600


def test_remote_requires_both_global_approval_and_explicit_test_confirmation():
    settings = normalize_llm_settings({
        **BASE,
        "endpoint": "https://example.org/v1/chat/completions",
        "allow_remote": True,
    })
    calls = []
    def forbidden_factory(cfg):
        calls.append(cfg)
        raise AssertionError("no contact permitted without test confirmation")

    host, remote = destination_summary(settings)
    assert (host, remote) == ("example.org", True)
    assert "/v1/" not in host
    with pytest.raises(LLMSettingsError, match="Confirmer"):
        probe_llm_connection(settings, service_factory=forbidden_factory)
    assert calls == []


def test_explicit_probe_generates_only_synthetic_request():
    settings = normalize_llm_settings(BASE)
    captures = []
    class FakeService:
        def available(self):
            return True

        def generate(self, request):
            captures.append(request)
            return SimpleNamespace(ok=True, text="OK", reason="")

    assert probe_llm_connection(settings, service_factory=lambda _cfg: FakeService()) == "Test réussi"
    assert len(captures) == 1
    assert captures[0].prompt == "Test de connexion technique. Réponds uniquement OK."
    assert captures[0].max_output_tokens == 8
    assert not any(word in captures[0].prompt.lower() for word in ("livre", "emprunt", "diagnostic"))


@pytest.mark.parametrize("reason,expected", [
    ("auth_refused", "Authentification refusée"),
    ("not_found", "Modèle ou endpoint introuvable"),
    ("rate_limited", "Service limité temporairement"),
    ("timeout", "Délai de connexion dépassé"),
    ("backend_unavailable", "Serveur injoignable ou réponse non concluante"),
])
def test_probe_statuses_do_not_echo_backend_errors(reason, expected):
    class FakeService:
        def available(self):
            return True

        def generate(self, request):
            return SimpleNamespace(ok=False, text="", reason=reason)

    assert probe_llm_connection(BASE, service_factory=lambda _cfg: FakeService()) == expected



@pytest.mark.parametrize("reason,expected_code", [
    ("cancelled", "cancelled"),
    ("timeout", "timeout"),
    ("auth_refused", "auth_refused"),
    ("not_found", "not_found"),
    ("rate_limited", "rate_limited"),
    ("invalid_request", "invalid_request"),
    ("backend_unavailable", "backend_unavailable"),
    ("busy", "busy"),
    ("unavailable", "invalid_config"),
    ("request_too_large", "invalid_request"),
    ("invalid_response", "inconclusive"),
    ("SENSITIVE_REASON_not_for_logs", "inconclusive"),
])
def test_typed_probe_maps_only_known_codes_and_fixed_labels(reason, expected_code):
    from cifereca.gui.llm_settings import (
        probe_llm_connection_result, probe_llm_connection,
    )
    class FakeService:
        def available(self):
            return True

        def generate(self, request):
            return SimpleNamespace(
                ok=False, text="PRIVATE_RESPONSE_NOT_FOR_LOGS", reason=reason,
            )
    factory = lambda _cfg: FakeService()
    typed = probe_llm_connection_result(BASE, service_factory=factory)
    assert typed.status == expected_code
    assert "PRIVATE_RESPONSE" not in typed.label
    assert "SENSITIVE_REASON" not in typed.label
    assert typed.label == probe_llm_connection(BASE, service_factory=factory)


def test_typed_probe_success_only_if_response_is_non_empty():
    from cifereca.gui.llm_settings import probe_llm_connection_result
    class FakeService:
        def __init__(self, text):
            self.text = text

        def available(self):
            return True

        def generate(self, request):
            return SimpleNamespace(ok=True, text=self.text, reason="")

    positive = probe_llm_connection_result(
        BASE, service_factory=lambda _cfg: FakeService("OK"),
    )
    empty = probe_llm_connection_result(
        BASE, service_factory=lambda _cfg: FakeService(" "),
    )
    assert (positive.status, positive.label) == ("success", "Test réussi")
    assert empty.status == "inconclusive"


def test_typed_probe_never_contacts_unavailable_or_unapproved_destination():
    from cifereca.gui.llm_settings import probe_llm_connection_result
    calls = []
    def no_network(cfg):
        calls.append(True)
        return SimpleNamespace(available=lambda: False)

    unavailable = probe_llm_connection_result(BASE, service_factory=no_network)
    assert unavailable.status == "invalid_config"
    assert calls == [True]

    remote = {
        **BASE, "endpoint": "https://remote.example/private/api",
        "allow_remote": True,
    }
    with pytest.raises(LLMSettingsError, match="Confirmer"):
        probe_llm_connection_result(remote, service_factory=no_network)
    assert calls == [True]  # No POST, no factory construction.


def test_coreui_tab_saves_llm_and_only_claims_restart_not_hot_reload():
    source = settings_source_text()
    ast.parse(source)
    shell_source = (
        Path(__file__).resolve().parents[1] / "src/cifereca/gui/settings_shell.py"
    ).read_text(encoding="utf-8")
    ast.parse(shell_source)
    assert '("ai", "Intelligence artificielle")' in shell_source
    assert "build_settings_shell(" in source
    draft_source = (Path(__file__).resolve().parents[1] / "src/cifereca/gui/settings_draft.py").read_text()
    assert "draft.set('llm', new_ai_settings)" in draft_source
    assert "redémarrage nécessaire" in source
    assert 'ai_dirty=settings_state.dirty("ai")' in source
    assert "if ai_dirty:" in draft_source
    ai_source = (
        Path(__file__).resolve().parents[1] / "src/cifereca/gui/settings_ai_actions.py"
    ).read_text(encoding="utf-8")
    ast.parse(ai_source)
    assert "bind_settings_ai_callbacks(" in source
    assert "ai_endpoint.connect(\"notify::text\", endpoint_changed)" in ai_source
    assert 'ai_remote.set_active(False)' in ai_source
    assert 'Adw.MessageDialog.new(' in ai_source
    assert "submit_ai_probe(settings, confirmed=True)" in ai_source
    assert "probe_llm_connection_result(" in ai_source
    assert "self.runner.submit(" in ai_source
    assert "Aucune notice ou donnée personnelle ne sera envoyée" in ai_source
    assert "confirmation_version != ai_probe_version[0]" in ai_source
    assert "ai_cfg.update(new_ai_settings)" in source

def test_saved_llm_settings_take_effect_only_after_real_core_restart(tmp_path):
    """The plugin service is resolved from the persisted configuration on open."""
    from cifereca.core import CiferecaCore

    cfg_path = tmp_path / "config.json"
    db_path = tmp_path / "state.sqlite"
    settings = normalize_llm_settings({**BASE, "api_key_env": ""})
    options = dict(
        config_path=cfg_path, database_path=db_path,
        no_plugins=True, activate_plugins=False,
    )

    with CiferecaCore.open(**options) as initial:
        assert initial.plugins.service("llm") is None
        initial.config.set("llm", settings)
        initial.config.save()
        # Settings say "restart required" for a reason.
        assert initial.plugins.service("llm") is None

    with CiferecaCore.open(**options) as restarted:
        llm = restarted.plugins.service("llm")
        assert llm is not None
        assert llm.available()
        assert set(llm.capabilities()) == {"generate", "structured"}
        assert restarted.config.get("llm.model") == "installed-model"

        # Disabling also requires a restart: no hot-reload is claimed.
        restarted.config.set("llm.enabled", False)
        restarted.config.save()
        assert restarted.plugins.service("llm") is llm

    with CiferecaCore.open(**options) as disabled_again:
        assert disabled_again.plugins.service("llm") is None
