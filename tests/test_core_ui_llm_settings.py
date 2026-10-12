"""P0 #1099 — LLM settings without GTK, real network or secrets."""
from __future__ import annotations

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


def test_coreui_tab_saves_llm_and_only_claims_restart_not_hot_reload():
    source = (
        Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    ).read_text(encoding="utf-8")
    ast.parse(source)
    assert '("ai", "Intelligence artificielle")' in source
    assert 'draft.set("llm", new_ai_settings)' in source
    assert "redémarrage nécessaire" in source
    assert 'settings_revisions.get("ai", 0) != saved_settings_revisions.get("ai", 0)' in source
    assert "ai_endpoint.connect(\"notify::text\", endpoint_changed)" in source
    assert 'ai_remote.set_active(False)' in source
    assert 'Adw.MessageDialog.new(' in source
    assert "submit_ai_probe(settings, confirmed=True)" in source
    assert "probe_llm_connection(settings, remote_confirmed=confirmed)" in source
    assert "self.runner.submit(" in source
    assert "Aucune notice ou donnée personnelle ne sera envoyée" in source
    assert "confirmation_version != ai_probe_version[0]" in source
    assert "ai_cfg.update(new_ai_settings)" in source
