"""P2 #780: explicit bounded LLM wording, deterministic rank and offline tests."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.plugin_api import LLMResult
from recommendation_llm import evidence_only_request, explain_reasons
from recommendations import UxRecommendationsView


class FakeLLM:
    def __init__(self, *, result=None, error=None):
        self.result = result or LLMResult(
            ok=True, generated=True, text="Thèmes communs, sans changer le score.",
            provider="local", model="small-1", remote=False,
        )
        self.error = error
        self.calls = []

    def available(self):
        return True

    def generate(self, request, *, control=None):
        self.calls.append((request, control))
        if self.error:
            raise self.error
        return self.result


def test_prompt_is_only_three_bounded_attested_reasons_not_library_or_identity():
    request = evidence_only_request([
        "Auteur déjà présent", "Sujets proches : astronomie", "Série proche",
        "Texte hors budget",
    ])
    assert request.task == "generate"
    assert request.max_output_tokens == 96
    assert "Auteur déjà présent" in request.prompt
    assert "Texte hors budget" not in request.prompt
    assert "titre" not in request.prompt.lower()
    assert "identité" not in request.prompt.lower()
    assert evidence_only_request(()) is None
    assert len(evidence_only_request(["x" * 1000]).prompt) < 250


def test_disabled_or_no_consent_never_calls_even_if_service_exists():
    service = FakeLLM()
    assert explain_reasons(service, ("thème proche",)) is None
    assert explain_reasons(service, ("thème proche",), enabled=True) is None
    assert explain_reasons(service, ("thème proche",), consent=True) is None
    assert explain_reasons(None, ("thème proche",), enabled=True, consent=True) is None
    assert not service.calls


def test_generated_words_are_marked_as_unverified_with_model_provenance():
    service = FakeLLM(result=LLMResult(
        ok=True, generated=True,
        text="  Sujet  proche  :  botanique.  ",
        provider="provider_1", model="model-2", remote=True,
    ))
    result = explain_reasons(service, ("Sujet : botanique",), enabled=True, consent=True)
    assert result.text == "Sujet proche : botanique."
    assert result.provider == "provider_1"
    assert result.model == "model-2"
    assert result.remote is True
    assert result.elapsed_ms >= 0
    assert len(service.calls) == 1
    assert service.calls[0][0].task == "generate"


def test_failures_do_not_block_ranking_or_leak_vendor_error():
    for service in (
        FakeLLM(error=RuntimeError("secret bearer 123")),
        FakeLLM(result=LLMResult(ok=False, reason="api key")),
        FakeLLM(result=LLMResult(ok=True, text="Unverified", generated=False)),
        FakeLLM(result=LLMResult(ok=True, text="A" * 600, generated=True)),
        FakeLLM(result=LLMResult(ok=True, text="<x>", generated=True,
                                 provider="token password", model="unsafe token")),
    ):
        output = explain_reasons(service, ("Auteur : x",), enabled=True, consent=True)
        if output is not None:
            assert output.provider == "non précisé"
            assert output.model == "non précisé"
        assert "secret" not in repr(output)
    canceled = NS(cancelled=lambda: True)
    service = FakeLLM()
    assert explain_reasons(service, ("thème",), enabled=True, consent=True, control=canceled) is None
    assert service.calls == []


class Button:
    def __init__(self):
        self.sensitive = True

    def set_sensitive(self, value):
        self.sensitive = bool(value)


class Label:
    def __init__(self):
        self.label = ""

    def set_label(self, value):
        self.label = str(value)


class Handle:
    def __init__(self, op):
        self.op, self.callback, self.cancelled = op, None, False
        self.control = NS(cancelled=lambda: self.cancelled)

    def cancel(self):
        self.cancelled = True

    def add_done_callback(self, callback):
        self.callback = callback

    def result(self):
        return self.value

    def finish(self):
        self.value = self.op(self.control)
        self.callback(self)


class Broker:
    def __init__(self):
        self.handles = []

    def submit(self, operation, **_kwargs):
        handle = Handle(operation)
        self.handles.append(handle)
        return handle


def view_fixture(service=None):
    service = service or FakeLLM()
    broker, idle, reselected = Broker(), [], []
    view = object.__new__(UxRecommendationsView)
    view._llm_enabled = True
    view._llm_selection_key = "id-book"
    view._llm_selected_row = NS(key="id-book")
    view._llm_explanation = None
    view._llm_handle = None
    view._llm_generation = 0
    view._llm_button = Button()
    view._llm_hint = Label()
    view._busy, view._generation = False, 9
    candidate = NS(reasons=("Auteur : x",), score=72)
    view._ranked_by_key = {"id-book": candidate}
    view.plugin = NS(context=NS(service=lambda key: service if key == "llm" else None,
                                broker=lambda key: broker if key == "tasks" else None))
    view.GLib = NS(idle_add=lambda callback: idle.append(callback))
    view._selected_row = reselected.append
    return view, service, broker, idle, reselected, candidate


def test_explicit_ui_click_only_submits_then_applies_without_changing_rank():
    view, service, broker, idle, selected, candidate = view_fixture()
    view._explain_selected()
    assert len(broker.handles) == 1 and service.calls == []
    assert candidate.score == 72
    broker.handles[0].finish()
    assert view._llm_explanation is None
    idle.pop()()
    assert view._llm_explanation is not None
    assert selected[0].key == "id-book"
    assert view._llm_button.sensitive
    assert candidate.score == 72


def test_stale_response_after_selection_change_is_ignored():
    view, _, broker, idle, selected, _ = view_fixture()
    view._explain_selected()
    handle = broker.handles[0]
    # Task can finish and queue GTK before the user changes selection.
    handle.finish()
    view._reset_llm_selection("other-book")
    idle.pop()()
    assert view._llm_explanation is None
    assert not selected
    assert view._llm_selection_key == "other-book"
    assert handle.cancelled


def test_absent_service_reports_not_configured_without_submit():
    view, _, broker, _idle, _selected, _ = view_fixture()
    view.plugin.context = NS(service=lambda _: None, broker=lambda _: broker)
    view._explain_selected()
    assert not broker.handles
    assert "non configuré" in view._llm_hint.label
