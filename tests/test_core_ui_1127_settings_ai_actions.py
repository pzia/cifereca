"""Headless behavior of extracted Settings AI callbacks (P0 #1127)."""
from __future__ import annotations

from types import SimpleNamespace

from cifereca.gui.settings_ai_actions import bind_settings_ai_callbacks
from cifereca.gui.settings_lifetime import SettingsPageLifetime


class Widget:
    def __init__(self, text="", active=False):
        self.text, self.active = text, active
        self.handlers = {}

    def connect(self, signal, callback):
        self.handlers.setdefault(signal, []).append(callback)

    def emit(self, signal, *args):
        for handler in self.handlers.get(signal, []):
            handler(*args)

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text
        self.emit("notify::text", self)

    def get_active(self):
        return self.active

    def set_active(self, active):
        self.active = active
        self.emit("notify::active", self)

    def set_sensitive(self, active):
        self.sensitive = active

    def grab_focus(self):
        self.focused = True


class Dialog:
    instances = []

    @classmethod
    def new(cls, *args):
        dialog = cls()
        cls.instances.append(dialog)
        return dialog

    def add_response(self, *args):
        pass

    def set_default_response(self, *args):
        pass

    def set_close_response(self, *args):
        pass

    def connect(self, signal, callback):
        assert signal == "response"
        self.response = callback

    def present(self):
        self.presented = True

    def respond(self, choice):
        self.response(self, choice)


class Runner:
    def __init__(self):
        self.jobs = []

    def submit(self, *args, **kwargs):
        self.jobs.append((args, kwargs))


class Window:
    def __init__(self):
        self.runner = Runner()

    def get_focus(self):
        return None


def _bind(*, remote=False):
    ui = SimpleNamespace(
        enabled=Widget(active=True),
        endpoint=Widget("https://example.org/v1/chat/completions" if remote
                        else "http://127.0.0.1:11434/v1/chat/completions"),
        model=Widget("local-model"),
        remote=Widget(active=remote),
        key_env=Widget("LOCAL_TEST_KEY"),
        timeout=Widget("5"),
        input_limit=Widget("4000"),
        output_limit=Widget("256"),
        status=Widget("Configuré"),
        test_button=Widget(),
        reset_button=Widget(),
        preset_button=Widget(),
    )
    window = Window()
    lifetime = SettingsPageLifetime()
    saved = {
        "enabled": True, "endpoint": ui.endpoint.get_text(),
        "model": "local-model", "allow_remote": remote,
        "api_key_env": "LOCAL_TEST_KEY",
        "timeout_seconds": "5", "max_input_chars": "4000", "max_output_tokens": "256",
    }
    capture = bind_settings_ai_callbacks(
        window, ai_ui=ui, ai_cfg=saved, page_lifetime=lifetime,
        settings_state=SimpleNamespace(reset=lambda _tab: None),
        update_settings_actions=lambda: None,
        Adw=SimpleNamespace(MessageDialog=Dialog),
        TaskPriority=SimpleNamespace(USER_ACTION="user"),
    )
    return window, ui, lifetime, capture


def test_endpoint_edit_revokes_remote_consent_and_stale_confirmation():
    Dialog.instances.clear()
    window, ui, _lifetime, capture = _bind(remote=True)
    ui.test_button.emit("clicked", ui.test_button)
    assert len(Dialog.instances) == 1
    assert window.runner.jobs == []

    ui.endpoint.set_text("http://127.0.0.1:11434/v1/chat/completions")
    assert capture()["allow_remote"] is False
    Dialog.instances[-1].respond("accept")
    assert window.runner.jobs == []
    assert "renouveler la confirmation" in ui.status.text


def test_local_probe_is_lazy_and_late_result_cannot_change_disposed_view():
    Dialog.instances.clear()
    window, ui, lifetime, capture = _bind()
    assert capture()["provider"] == "openai-compatible"
    assert window.runner.jobs == []
    ui.test_button.emit("clicked", ui.test_button)
    assert len(window.runner.jobs) == 1
    assert Dialog.instances == []
    args, kwargs = window.runner.jobs[0]
    assert kwargs["kind"] == "settings"
    assert callable(args[1])  # Work scheduled, not executed by the constructor.
    status = ui.status.text
    lifetime.dispose()
    args[2]("Test réussi")
    args[3](RuntimeError("private-backend-error"))
    assert ui.status.text == status


def test_probe_logs_one_safe_terminal_event_per_executed_job(caplog, monkeypatch):
    import logging
    from cifereca.gui.llm_settings import LLMProbeResult

    window, ui, lifetime, _capture = _bind()
    secrets = (
        "SECRET_API_BEARER", "ENV_SECRET_NAME",
        "https://remote.example/secret?token=PRIVATE",
        "private-model-name", "Camille Dupont",
    )
    ui.key_env.set_text("ENV_SECRET_NAME")
    ui.model.set_text("private-model-name")
    outcomes = iter(("auth_refused", "success"))
    def probe(settings, *, remote_confirmed):
        assert settings["api_key_env"] == "ENV_SECRET_NAME"
        assert remote_confirmed is False
        status = next(outcomes)
        return LLMProbeResult(status, "Authentification refusée" if status == "auth_refused" else "Test réussi")

    monkeypatch.setattr(
        "cifereca.gui.settings_ai_actions.probe_llm_connection_result", probe,
    )
    with caplog.at_level(logging.INFO, logger="cifereca"):
        ui.test_button.emit("clicked", ui.test_button)
        first = window.runner.jobs[0][0]
        assert "operation=llm_connection_probe" not in caplog.text
        # First worker has really executed, even if its UI callback arrives late.
        result1 = first[1]()
        assert result1.status == "auth_refused"
        ui.model.set_text("new-model-name")
        ui.test_button.emit("clicked", ui.test_button)
        second = window.runner.jobs[1][0]
        result2 = second[1]()
        first[2](result1)  # Stale completion must not overwrite the latest test.
        assert ui.status.text == "Test synthétique en cours…"
        second[2](result2)
        assert "Test réussi" in ui.status.text
        lifetime.dispose()
        assert caplog.text.count("operation=llm_connection_probe") == 2
        assert "status=auth_refused" in caplog.text
        assert "status=success" in caplog.text
        assert "destination=local" in caplog.text
        for value in secrets:
            assert value not in caplog.text


def test_probe_exception_is_classified_without_str_or_repr(caplog, monkeypatch):
    import logging
    class HostileException(RuntimeError):
        def __str__(self):
            return "Bearer PRIVATE_TOKEN username=Camille"
        def __repr__(self):
            raise AssertionError("raw exception repr attempted")

    def raise_hostile(*_a, **_kw):
        raise HostileException("https://private.example/secret/model")

    monkeypatch.setattr(
        "cifereca.gui.settings_ai_actions.probe_llm_connection_result", raise_hostile,
    )
    window, ui, _life, _capture = _bind()
    with caplog.at_level(logging.INFO, logger="cifereca"):
        ui.test_button.emit("clicked", ui.test_button)
        args, _kwargs = window.runner.jobs[0]
        outcome = args[1]()
        assert outcome.status == "internal_error"
        args[2](outcome)
    assert "Test non concluant" in ui.status.text
    assert caplog.text.count("operation=llm_connection_probe") == 1
    assert "status=internal_error" in caplog.text
    assert "PRIVATE_TOKEN" not in caplog.text
    assert "private.example" not in caplog.text
    assert "Camille" not in caplog.text


def test_completed_probe_logs_after_page_disposal_even_when_ui_is_inert(caplog, monkeypatch):
    import logging
    from cifereca.gui.llm_settings import LLMProbeResult
    monkeypatch.setattr(
        "cifereca.gui.settings_ai_actions.probe_llm_connection_result",
        lambda *_a, **_kw: LLMProbeResult("timeout", "Délai de connexion dépassé"),
    )
    window, ui, lifetime, _capture = _bind()
    ui.test_button.emit("clicked", ui.test_button)
    args, _kwargs = window.runner.jobs[0]
    before = ui.status.text
    lifetime.dispose()
    with caplog.at_level(logging.INFO, logger="cifereca"):
        result = args[1]()
        args[2](result)
    assert ui.status.text == before
    assert caplog.text.count("operation=llm_connection_probe") == 1
    assert "status=timeout" in caplog.text


def test_preflight_rejection_and_refused_remote_consent_do_not_claim_executed_probe(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="cifereca"):
        window, ui, _life, _capture = _bind()
        ui.model.set_text("")
        ui.test_button.emit("clicked", ui.test_button)
        assert window.runner.jobs == []
        Dialog.instances.clear()
        remote_window, remote_ui, _life, _capture = _bind(remote=True)
        remote_ui.test_button.emit("clicked", remote_ui.test_button)
        assert len(Dialog.instances) == 1
        Dialog.instances[-1].respond("cancel")
        assert remote_window.runner.jobs == []
    assert "operation=llm_connection_probe" not in caplog.text
