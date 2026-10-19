"""#1599 P1: hostile optional LLM output cannot poison public book details.

All cases are offline. Ranking, account, acquisition and GTK are not invoked.
"""
from __future__ import annotations

from cifereca.plugin_api import LLMResult
from recommendation_llm import evidence_only_request, explain_reasons


class UntrustedString(str):
    def __str__(self):
        raise AssertionError("PRIVATE_ACCOUNT_TOKEN")


class Poison:
    def __str__(self):
        raise AssertionError("PRIVATE_VENDOR_EXCEPTION")


class FakeService:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def available(self):
        return True

    def generate(self, request, *, control=None):
        self.calls += 1
        return self.result


class BrokenResult:
    ok = True
    generated = True

    @property
    def text(self):
        raise RuntimeError("PRIVATE_SESSION_ID")


def describe(result):
    service = FakeService(result)
    response = explain_reasons(
        service, ("Sujet bibliographique attesté",),
        enabled=True, consent=True,
    )
    assert service.calls == 1
    return response


def test_prompt_never_serializes_mapping_keys_or_untrusted_str_subclasses():
    assert evidence_only_request({"PRIVATE_BEARER": "do not prompt"}) is None
    assert evidence_only_request("PRIVATE_BEARER") is None
    assert evidence_only_request((UntrustedString("PRIVATE_CREDENTIAL"),)) is None

    request = evidence_only_request([
        UntrustedString("PRIVATE_ID"), "Auteur attesté",
        "Texte\u202ePRIVATE_BIDI", "au-delà de trois positions",
    ])
    assert request is not None
    assert "Auteur attesté" in request.prompt
    assert "PRIVATE" not in request.prompt
    assert "au-delà" not in request.prompt


def test_control_and_bidi_characters_cannot_reach_detail_from_remote_model():
    for text in (
        "Avis\u202einvisible",
        "Avis\u2066invisible",
        "Avis\x00caché",
        "Avis\ud800corrompu",
        UntrustedString("PRIVATE_AUTHORIZATION"),
    ):
        assert describe(LLMResult(ok=True, generated=True, text=text)) is None


def test_unsafe_provider_and_model_values_are_omitted_not_stringified():
    output = describe(LLMResult(
        ok=True, generated=True, text="Auteurs et sujets attestés.",
        provider=Poison(), model=UntrustedString("PRIVATE_API_KEY"),
        remote=True,
    ))
    assert output is not None
    assert output.text == "Auteurs et sujets attestés."
    assert output.provider == "non précisé"
    assert output.model == "non précisé"
    assert output.remote is True


def test_failing_result_accessor_is_safe_unavailable_not_raw_exception():
    assert describe(BrokenResult()) is None
