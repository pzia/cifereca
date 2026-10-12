"""Public destination disclosure for user-approved Diagnostics LLM requests."""
from __future__ import annotations

from cifereca.core.llm_service import OptionalLLMService
from cifereca.plugin_api import LLMService


def test_local_model_disclosure_only_host_model_provider_and_location():
    llm = OptionalLLMService({
        "enabled": True, "provider": "openai-compatible", "model": "local-qa",
        "endpoint": "http://localhost:11434/v1/chat/completions",
        "api_key_env": "VERY_PRIVATE_NAME",
    })
    assert isinstance(llm, LLMService)
    assert llm.available()
    disclosure = llm.destination()
    assert disclosure == {
        "available": True, "remote": False, "provider": "openai-compatible",
        "model": "local-qa", "host": "localhost",
    }
    assert "VERY_PRIVATE_NAME" not in str(disclosure)
    assert "/v1" not in str(disclosure)


def test_remote_destination_requires_core_approved_https():
    llm = OptionalLLMService({
        "enabled": True, "model": "remote-qa",
        "endpoint": "https://model.example/v1/chat/completions",
        "allow_remote": True, "api_key_env": "PRIVATE_KEY",
    })
    assert llm.destination() == {
        "available": True, "remote": True, "provider": "openai-compatible",
        "model": "remote-qa", "host": "model.example",
    }
    unauthorized = OptionalLLMService({
        "enabled": True, "model": "remote-qa",
        "endpoint": "https://model.example/v1/chat/completions",
        "allow_remote": False,
    })
    assert unauthorized.destination()["available"] is False
    assert unauthorized.destination()["host"] == ""
