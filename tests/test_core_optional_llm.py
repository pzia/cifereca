from __future__ import annotations

import json
from io import BytesIO
from threading import Event
from time import monotonic

import pytest

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
    assert not result.ok and result.reason == "invalid_response"


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


def test_serialized_json_size_is_bounded_before_network_or_slot_acquisition():
    http = FakeHTTP()
    service = OptionalLLMService(
        _config(max_input_chars=16000, timeout_seconds=0.2), opener=http,
    )
    # json.dumps escapes non-BMP characters as surrogate pairs (12 bytes
    # instead of one Python character), so the character budget alone
    # would admit a much larger wire request.
    oversized = "📚" * 8000
    assert len(oversized) <= 16000
    rejected = service.generate(LLMRequest(oversized))
    assert rejected == LLMResult(
        False, reason="request_too_large", provider="openai-compatible",
        model="test-model", remote=False,
    )
    assert http.calls == []
    assert oversized not in repr(rejected)

    accepted = service.generate(LLMRequest("📚" * 100))
    assert accepted.ok
    assert len(http.calls) == 1
    raw = http.calls[0][0].data
    assert len(raw) < 65536
    assert json.loads(raw)["messages"][-1]["content"] == "📚" * 100


def test_structured_large_payload_has_same_wire_cap_and_avoids_secret_leaks():
    http = FakeHTTP()
    service = OptionalLLMService(
        _config(max_input_chars=16000, api_key_env="CIFERECA_TEST_SECRET"), opener=http,
    )
    # Large astral-plane text remains within the configured character cap,
    # but crosses the HTTP payload cap in structured mode too.
    result = service.generate(LLMRequest("💾" * 7000, task="structured"))
    assert result.reason == "request_too_large"
    assert result.generated is False
    assert http.calls == []


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

def test_llm_http_failures_return_only_safe_status_reasons():
    from urllib.error import HTTPError

    for status, expected in [
        (401, "auth_refused"), (403, "auth_refused"),
        (404, "not_found"), (429, "rate_limited"),
        (500, "backend_unavailable"),
    ]:
        def reject(request, *, timeout):
            raise HTTPError("https://example.test/v1/chat/completions", status, "Not available", {}, None)

        response = OptionalLLMService(_config(), opener=reject).generate(
            LLMRequest("connection check"),
        )
        assert response.ok is False
        assert response.reason == expected




@pytest.mark.parametrize("content", [
    '{"state":"ready","state":"unavailable"}',
    '{"score":NaN}', '{"score":Infinity}', '{"score":-Infinity}',
    '{"scores":[{"confidence":1e999}]}',
    '{"result":{"allowed":true,"allowed":false}}',
    '[]', '"not a JSON object"', 'null',
])
def test_structured_llm_rejects_ambiguous_or_nonfinite_json_without_exposing_content(content):
    """Typed provider facts cannot be based on duplicate keys or non-JSON floats."""
    response = json.dumps({"choices": [{"message": {"content": content}}]})
    http = FakeHTTP(response)
    llm = OptionalLLMService(_config(), opener=http)
    result = llm.generate(LLMRequest("classer", task="structured"))
    assert result.ok is False
    assert result.reason == "invalid_response"
    assert result.structured == {}
    assert result.generated is False
    assert content not in repr(result)
    assert len(http.calls) == 1


@pytest.mark.parametrize("response", [
    '{"choices":[{"message":{"content":"ok"}},{"message":{"content":"ignored"}}],"choices":[]}',
    '{"choices":[{"message":{"content":"ok","content":"secret"}}]}',
    '{"choices":[{"message":{"content":"ok"}}],"usage":{"rate":NaN}}',
    '{"choices":[{"message":{"content":"ok"}}],"usage":{"rate":1e999}}',
    '{"choices":[]}',
    '{"choices":[{"message":{"content":42}}]}',
    'null', '[]', '{not json}',
])
def test_llm_rejects_invalid_http_json_envelope_without_prompt_or_response_leak(response):
    http = FakeHTTP(response)
    result = OptionalLLMService(_config(), opener=http).generate(
        LLMRequest("Private loan from Alice"),
    )
    assert result.reason == "invalid_response" and not result.ok
    assert result.text == "" and result.structured == {}
    assert "Private loan" not in repr(result)
    assert response not in repr(result)


def test_llm_rejects_unpaired_surrogate_in_model_text():
    http = FakeHTTP(json.dumps({
        "choices": [{"message": {"content": "private invalid " + chr(0xD800)}}],
    }))
    for task in ("generate", "structured"):
        result = OptionalLLMService(_config(), opener=http).generate(
            LLMRequest("sensitive", task=task),
        )
        assert result.reason == "invalid_response" and not result.ok
        assert result.text == ""
    assert len(http.calls) == 2


def test_structured_llm_valid_nested_unicode_json_survives_unchanged():
    nested = {"categories": [{"theme": "forêt", "confidence": 0.75}],
              "generated": True, "empty": None}
    content = json.dumps(nested, ensure_ascii=False)
    http = FakeHTTP(json.dumps({"choices": [{"message": {"content": content}}]}))
    result = OptionalLLMService(_config(), opener=http).generate(
        LLMRequest("classifier", task="structured"),
    )
    assert result.ok and result.generated
    assert result.structured == nested
    assert result.text == content
    assert result.provider == "openai-compatible" and not result.remote



@pytest.mark.parametrize("model", [
    "test-model\nBearer SECRET_AT_NEWLINE",
    "test-model\rOverwrite-model",
    "model\tPRIVATE_VALUE",
    "model\u202ePRIVATE_RTL",
    "model\u2066PRIVATE_ISOLATE",
    "model\u200ePRIVATE_MARK",
    "model\ud800PRIVATE_SURROGATE",
    "x" * 129,
])
def test_direct_llm_service_rejects_spoofed_model_before_consent_or_http(model):
    """#778: direct config and plugin clients must not bypass GTK validation."""
    http = FakeHTTP()
    service = OptionalLLMService(_config(model=model), opener=http)
    assert not service.available()
    assert service.capabilities() == ()
    assert service.destination()["model"] == ""
    result = service.generate(LLMRequest("Livre privé"))
    assert result.reason == "unavailable"
    assert result.model == ""
    assert http.calls == []
    for secret in ("SECRET_AT_NEWLINE", "PRIVATE_VALUE", "PRIVATE_RTL",
                   "PRIVATE_ISOLATE", "PRIVATE_MARK", "PRIVATE_SURROGATE"):
        assert secret not in repr(service.destination())
        assert secret not in repr(result)


@pytest.mark.parametrize("provider", [
    "Bearer PRIVATE_PROVIDER", "custom\nPRIVATE_PROVIDER", "unsupported-vendor",
])
def test_direct_llm_service_masks_unsupported_provider_in_diagnostics(provider):
    http = FakeHTTP()
    service = OptionalLLMService(_config(provider=provider), opener=http)
    assert not service.available()
    assert service.destination()["provider"] == "unsupported"
    result = service.generate(LLMRequest("Confidentiel"))
    assert result.provider == "unsupported"
    assert "PRIVATE_PROVIDER" not in repr(result)
    assert not http.calls


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf")])
def test_llm_nonfinite_timeout_is_rejected_without_spawn_or_network(timeout):
    http = FakeHTTP()
    service = OptionalLLMService(_config(timeout_seconds=timeout), opener=http)
    assert not service.available()
    assert service.generate(LLMRequest("Contenu privé")).reason == "unavailable"
    assert http.calls == []


def test_valid_model_namespace_remains_supported_without_hidden_truncation():
    http = FakeHTTP()
    model = "org/models/deepseek-v3:8b+instruct"
    service = OptionalLLMService(_config(model=model, provider=None), opener=http)
    assert service.available()  # None retains the prior default provider.
    assert service.destination()["model"] == model
    assert service.generate(LLMRequest("ok")).ok
    assert json.loads(http.calls[0][0].data)["model"] == model



@pytest.mark.parametrize("suffix", [
    "\u202ePRIVATE_BIDI",
    "\u2066PRIVATE_ISOLATE",
    "\u200fPRIVATE_MARK",
    "\ud800PRIVATE_SURROGATE",
    "\x85PRIVATE_C1",
])
def test_direct_llm_endpoint_rejects_hidden_controls_before_http(suffix):
    http = FakeHTTP()
    service = OptionalLLMService(
        _config(endpoint="http://127.0.0.1:12345/v1/chat/completions" + suffix),
        opener=http,
    )
    assert service.available() is False
    assert service.destination()["host"] == ""
    assert service.generate(LLMRequest("Livre privé")).reason == "unavailable"
    assert not http.calls


@pytest.mark.parametrize("failure,expected", [
    (TimeoutError("PRIVATE_TIMEOUT_ENDPOINT"), "timeout"),
    (__import__("urllib.error", fromlist=["URLError"]).URLError(
        TimeoutError("PRIVATE_TIMEOUT_ENDPOINT")
    ), "timeout"),
    (__import__("urllib.error", fromlist=["URLError"]).URLError(
        RuntimeError("PRIVATE_NETWORK_ENDPOINT")
    ), "backend_unavailable"),
])
def test_urllib_network_timeout_and_deadline_agree_on_safe_public_reason(failure, expected):
    """#1346 CI: a socket/read timeout must not race into backend_unavailable.

    This is the same URL-opening path exercised by llm-settings-1600 GTK.
    The injected errors are synthetic; none of their private text is exposed.
    """
    def opener(_request, *, timeout):
        assert 0.2 <= timeout <= 0.3
        raise failure

    service = OptionalLLMService(
        _config(timeout_seconds=0.2), opener=opener,
    )
    result = service.generate(LLMRequest("PRIVATE_USER_MESSAGE"))
    assert not result.ok and result.reason == expected
    assert result.generated is False
    assert "PRIVATE" not in repr(result)
    assert result.text == ""
