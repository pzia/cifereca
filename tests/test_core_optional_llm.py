from __future__ import annotations

import json
from io import BytesIO
from threading import Event
from time import monotonic

from cifereca.config import Config
from cifereca.core.llm_service import OptionalLLMService, optional_llm_service
from cifereca.plugin_api import LLMRequest, LLMResult, LLMService
from cifereca.plugins.manager import PluginManager


def _config(**changes):
    return {
        "enabled": True,
        "provider": "openai-compatible",
        "model": "test-model",
        "endpoint": "http://127.0.0.1:12345/v1/chat/completions",
        "timeout_seconds": 0.3,
        **changes,
    }


class FakeHTTP:
    def __init__(self, result='{"choices":[{"message":{"content":"résultat"}}]}'):
        self.content = result.encode("utf-8")
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        return BytesIO(self.content)


def test_default_is_absent_and_never_connects(tmp_path):
    config = Config(tmp_path / "config.json")
    assert config.get("llm.enabled") is False
    assert optional_llm_service(config) is None
    manager = PluginManager()
    assert manager.service("llm") is None


def test_local_opt_in_generates_without_leaking_prompt_to_diagnostics():
    http = FakeHTTP()
    service = OptionalLLMService(_config(), opener=http)
    assert isinstance(service, LLMService)
    assert service.available()
    assert service.capabilities() == ("generate", "structured")
    response = service.generate(LLMRequest("Une notice privée", system="Répondre en français"))
    assert response == LLMResult(
        True, text="résultat", provider="openai-compatible",
        model="test-model", generated=True, remote=False,
    )
    request, timeout = http.calls[0]
    assert request.full_url == "http://127.0.0.1:12345/v1/chat/completions"
    assert json.loads(request.data)["messages"][1]["content"] == "Une notice privée"
    assert timeout <= 0.3
    assert "Une notice privée" not in repr(response)


def test_remote_needs_two_explicit_opt_ins_and_https():
    for changes in (
        {"endpoint": "https://remote.example/v1/chat/completions"},
        {"endpoint": "http://remote.example/v1/chat/completions", "allow_remote": True},
        {"endpoint": "https://user:secret@remote.example/v1/chat/completions", "allow_remote": True},
        {"endpoint": "https://remote.example/v1/chat/completions?token=secret", "allow_remote": True},
        {"endpoint": "file:///tmp/data", "allow_remote": True},
        {"endpoint": "https://remote.example/v1/chat/completions#fragment", "allow_remote": True},
    ):
        http = FakeHTTP()
        service = OptionalLLMService(_config(**changes), opener=http)
        assert not service.available()
        assert service.generate(LLMRequest("CONFIDENTIEL")).reason == "unavailable"
        assert http.calls == []
    http = FakeHTTP()
    service = OptionalLLMService(_config(
        endpoint="https://remote.example/v1/chat/completions",
        allow_remote=True,
    ), opener=http)
    assert service.available()
    assert service.generate(LLMRequest("ok")).remote is True
    assert len(http.calls) == 1


def test_structured_json_is_an_inferred_result_not_a_verified_fact():
    http = FakeHTTP('{"choices":[{"message":{"content":"{\\"category\\":\\"fiction\\"}"}}]}')
    service = OptionalLLMService(_config(), opener=http)
    result = service.generate(LLMRequest("Classe ce document", task="structured"))
    assert result.ok and result.generated
    assert result.structured == {"category": "fiction"}
    assert json.loads(http.calls[0][0].data)["response_format"] == {"type": "json_object"}
    assert result.model == "test-model"


def test_bad_json_and_failed_backend_have_safe_error_without_secret():
    class Explodes:
        def __call__(self, request, *, timeout):
            raise RuntimeError("token-super-secret /private/path")

    service = OptionalLLMService(_config(), opener=Explodes())
    result = service.generate(LLMRequest("données personnelles"))
    assert result.reason == "backend_unavailable"
    assert "token-super-secret" not in repr(result)
    assert "données personnelles" not in repr(result)
    http = FakeHTTP('{"choices":[{"message":{"content":"unstructured"}}]}')
    result = OptionalLLMService(_config(), opener=http).generate(
        LLMRequest("Classe", task="structured"),
    )
    assert not result.ok and result.reason == "backend_unavailable"


def test_size_limits_are_checked_before_any_http_request():
    http = FakeHTTP()
    service = OptionalLLMService(_config(max_input_chars=10, max_output_tokens=5), opener=http)
    assert service.generate(LLMRequest("X" * 11)).reason == "invalid_request"
    assert service.generate(LLMRequest("ok", system="x" * 1001)).reason == "invalid_request"
    assert service.generate(LLMRequest("ok", task="embeddings")).reason == "invalid_request"
    assert service.generate(LLMRequest("ok", max_output_tokens=0)).reason == "invalid_request"
    assert http.calls == []
    good = service.generate(LLMRequest("ok", max_output_tokens=500))
    assert good.ok
    assert json.loads(http.calls[0][0].data)["max_tokens"] == 5


def test_cancelled_request_is_not_sent_and_inflight_cancellation_returns_promptly():
    class Cancelled:
        def cancelled(self):
            return True

    http = FakeHTTP()
    service = OptionalLLMService(_config(), opener=http)
    assert service.generate(LLMRequest("private"), control=Cancelled()).reason == "cancelled"
    assert not http.calls

    entered, release = Event(), Event()

    class SlowHTTP:
        def __call__(self, request, *, timeout):
            entered.set()
            release.wait(2)
            return BytesIO(b'{"choices":[{"message":{"content":"late"}}]}')

    class CancelAfterStart:
        def cancelled(self):
            return entered.is_set()

    service = OptionalLLMService(_config(), opener=SlowHTTP())
    start = monotonic()
    try:
        response = service.generate(LLMRequest("private"), control=CancelAfterStart())
        assert response.reason == "cancelled"
        assert monotonic() - start < 1.0
    finally:
        release.set()


def test_backend_timeout_is_bounded_and_secrets_absent():
    release = Event()

    class SlowHTTP:
        def __call__(self, request, *, timeout):
            release.wait(2)
            return BytesIO(b'{"choices":[{"message":{"content":"late"}}]}')

    service = OptionalLLMService(_config(timeout_seconds=0.2), opener=SlowHTTP())
    start = monotonic()
    try:
        response = service.generate(LLMRequest("private token"))
        assert response.reason == "timeout"
        assert monotonic() - start < 0.8
        assert "private token" not in repr(response)
    finally:
        release.set()


def test_core_reserved_service_is_accessible_through_normal_plugin_resolution(tmp_path):
    config = Config(tmp_path / "config.json")
    config.set("llm.enabled", True)
    config.set("llm.endpoint", "http://localhost:11434/v1/chat/completions")
    config.set("llm.model", "local-llm")
    service = optional_llm_service(config)
    assert service is not None
    manager = PluginManager(core_services={"llm": service})
    try:
        assert manager.service("llm") is service
        assert service.available()
    finally:
        manager.close()
