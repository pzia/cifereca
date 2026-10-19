"""#778: optional LLM service refuses malformed plugin requests before I/O."""
from __future__ import annotations

from io import BytesIO

import pytest

from cifereca.core.llm_service import OptionalLLMService
from cifereca.plugin_api import LLMRequest


class Hostile:
    def __bool__(self):
        raise AssertionError("never invoke plugin __bool__")

    def __eq__(self, _other):
        raise AssertionError("never invoke plugin __eq__")

    def __str__(self):
        raise AssertionError("never stringify plugin data")


class HostileStr(str):
    def strip(self, *_args):
        raise AssertionError("never strip subclass")


@pytest.mark.parametrize("changes", [
    {"prompt": Hostile()},
    {"prompt": HostileStr("PRIVATE_ACSM_URL")},
    {"system": Hostile()},
    {"system": HostileStr("PRIVATE_ACSM_URL")},
    {"task": Hostile()},
    {"task": HostileStr("generate")},
    {"task": ["structured"]},
    {"task": None},
])
def test_non_native_request_fields_fail_closed_before_http(changes):
    calls = []
    def opener(*_args, **_kwargs):
        calls.append(True)
        return BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')

    service = OptionalLLMService({
        "enabled": True, "provider": "openai-compatible",
        "model": "local", "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
    }, opener=opener)
    req = dict(prompt="normal", system="", task="generate")
    req.update(changes)
    result = service.generate(LLMRequest(**req))
    assert not result.ok and result.reason == "invalid_request"
    assert not result.generated and result.text == "" and result.structured == {}
    assert "PRIVATE_ACSM_URL" not in repr(result)
    assert calls == []


@pytest.mark.parametrize("kind", ["raises", "foreign", "bool_subclass", "cancelled"])
def test_invalid_or_cancelled_control_never_sends_private_prompt(kind):
    calls = []
    class Broken:
        def cancelled(self):
            if kind == "raises":
                raise RuntimeError("PRIVATE_ACSM_URL")
            if kind == "foreign":
                return Hostile()
            if kind == "bool_subclass":
                return 1
            return True

    def opener(*_args, **_kwargs):
        calls.append(True)
        return BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')

    service = OptionalLLMService({
        "enabled": True, "model": "local", "provider": "openai-compatible",
        "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
    }, opener=opener)
    response = service.generate(LLMRequest("PRIVATE_ACSM_URL"), control=Broken())
    assert not response.ok
    assert response.reason == ("cancelled" if kind == "cancelled" else "invalid_request")
    assert "PRIVATE_ACSM_URL" not in repr(response)
    assert calls == []


def test_native_fields_still_work_for_generate_and_structured():
    calls = []
    def opener(request, *, timeout):
        calls.append((request.data, timeout))
        return BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')

    service = OptionalLLMService({
        "enabled": True, "provider": "openai-compatible",
        "model": "local", "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
    }, opener=opener)
    assert service.generate(LLMRequest("notice")).ok
    assert service.generate(LLMRequest("notice", task="structured")).structured == {}
    assert len(calls) == 2
