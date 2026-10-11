"""Offline evidence and non-regression tests for optional Jeunesse AI (#781)."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import LLMResult
from youth_catalog import YouthBook, YouthAgeRange
from youth_llm import theme_request, rewrite_themes
from youth_page import YouthView, book_key


class Service:
    def __init__(self, result=None, error=None):
        self.result = result or LLMResult(
            ok=True, generated=True, text="Le récit explore l’amitié et l’aventure.",
            provider="local", model="small", remote=False,
        )
        self.error = error
        self.calls = []

    def available(self):
        return True

    def generate(self, request, *, control=None):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result


def test_themes_only_not_source_ages_or_book_identity():
    req = theme_request(("amitié", "aventure", "nature", "quête", "voyage", "humour", "secret"))
    assert req is not None and req.task == "generate" and req.max_output_tokens == 96
    assert "amitié" in req.prompt and "secret" not in req.prompt
    assert "ISBN" not in req.prompt and "alice" not in req.prompt
    assert theme_request(()) is None
    assert len(theme_request(("A" * 300,)).prompt) <= 170


def test_source_age_labels_are_never_included_in_llm_prompt():
    request = theme_request(("science", "Dès 8 ans", "lecture 7+", "étoiles", "huit ans"))
    assert request is not None
    assert "science" in request.prompt and "étoiles" in request.prompt
    assert "8 ans" not in request.prompt and "7+" not in request.prompt
    assert "huit ans" not in request.prompt
    assert theme_request(("3 ans", "livre 6+")) is None


def test_generated_digit_claim_is_rejected_even_without_age_unit():
    service = Service(LLMResult(ok=True, generated=True, text="Accessible dès 8+"))
    assert rewrite_themes(service, ("astronomie",), enabled=True, consent=True) is None


def test_double_opt_in_and_no_theme_never_call_llm():
    service = Service()
    themes = ("écologie",)
    assert rewrite_themes(service, themes) is None
    assert rewrite_themes(service, themes, enabled=True) is None
    assert rewrite_themes(service, themes, consent=True) is None
    assert rewrite_themes(service, (), enabled=True, consent=True) is None
    assert rewrite_themes(None, themes, enabled=True, consent=True) is None
    assert service.calls == []


@pytest.mark.parametrize("generated", [
    "Conseillé dès 8 ans.",
    "C'est un récit pour dix ans.",
    "Lecture pour les 12 à 14 ans.",
    "Niveau : âge=5",
])
def test_generated_age_claim_is_rejected(generated):
    service = Service(LLMResult(ok=True, generated=True, text=generated))
    assert rewrite_themes(service, ("Aventure",), enabled=True, consent=True) is None


def test_service_refusal_errors_and_cancellation_are_graceful():
    cancel = NS(cancelled=lambda: True)
    service = Service()
    assert rewrite_themes(service, ("aventure",), enabled=True, consent=True,
                          control=cancel) is None
    assert not service.calls
    assert rewrite_themes(Service(error=RuntimeError("Bearer VERY_SECRET")), ("aventure",),
                          enabled=True, consent=True) is None
    assert rewrite_themes(Service(LLMResult(ok=False, reason="secret")), ("aventure",),
                          enabled=True, consent=True) is None
    assert rewrite_themes(Service(LLMResult(ok=True, generated=False, text="oui")), ("aventure",),
                          enabled=True, consent=True) is None
    assert rewrite_themes(Service(LLMResult(ok=True, generated=True, text="X" * 400)), ("aventure",),
                          enabled=True, consent=True) is None


def test_generated_wording_is_separate_and_provenance_explicit():
    generated = LLMResult(
        ok=True, generated=True,
        text="  Des thèmes liés à la forêt et l’entraide. ",
        provider="provider/unsafe secret", model="good_model", remote=True,
    )
    wording = rewrite_themes(Service(generated), ("forêt", "entraide"),
                            enabled=True, consent=True)
    assert wording.text == "Des thèmes liés à la forêt et l’entraide."
    assert wording.provider == "non indiqué"
    assert wording.model == "good_model"
    assert wording.remote is True


class Widget:
    def __init__(self):
        self.sensitive = None
        self.label = ""

    def set_sensitive(self, value):
        self.sensitive = bool(value)

    def set_label(self, value):
        self.label = value


class Handle:
    def __init__(self, operation):
        self.operation = operation
        self.control = NS(cancelled=lambda: self.cancelled)
        self.cancelled = False
        self.callback = None

    def cancel(self):
        self.cancelled = True

    def add_done_callback(self, callback):
        self.callback = callback

    def result(self):
        return self.value

    def finish(self):
        self.value = self.operation(self.control)
        self.callback(self)


class Broker:
    def __init__(self):
        self.handles = []

    def submit(self, operation, **_kwargs):
        handle = Handle(operation)
        self.handles.append(handle)
        return handle


def make_view():
    book = YouthBook(record_id="123", source_id="milan", title="Mon livre",
                     age_ranges=(YouthAgeRange(7, 9, "publisher", "éditeur", "7 à 9 ans"),),
                     themes=("nature", "aventure"))
    broker, llm, scheduled, details = Broker(), Service(), [], []
    view = object.__new__(YouthView)
    view.plugin = NS(context=NS(
        service=lambda name: llm if name == "llm" else None,
        broker=lambda name: broker if name == "tasks" else None,
    ))
    view.disposed = False
    view._llm_enabled, view._llm_generation = True, 0
    view._llm_key, view._llm_task, view._llm_wording = None, None, None
    view._llm_button, view._llm_hint = Widget(), Widget()
    view.selected_book = None
    view.rows = {book_key(book): book}
    view.identity_id = lambda: "alice"
    view.master = NS(show_detail=details.append, empty_detail=lambda: None)
    view.host = NS()
    view.update_action_availability = lambda: None
    view.GLib = NS(idle_add=lambda fn: scheduled.append(fn))
    return view, book, broker, llm, scheduled, details


def test_selection_without_click_never_submits_and_ages_remain_source_owned():
    view, book, broker, _llm, _scheduled, details = make_view()
    view.selected(NS(key=book_key(book)))
    assert not broker.handles
    fields = {f.id: f.value for section in details[-1].sections for f in section.fields}
    assert not any(k.startswith("ai-") for k in fields)
    assert any(k.startswith("age-") for k in fields)
    assert view._llm_key == ("alice", book_key(book))


def test_explicit_click_adds_separate_generated_section_without_mutating_book():
    view, book, broker, llm, scheduled, details = make_view()
    original = book.age_ranges
    view.selected(NS(key=book_key(book)))
    view.rewrite_selected_themes()
    assert len(broker.handles) == 1 and llm.calls == []
    broker.handles[0].finish()
    assert view._llm_wording is None
    scheduled.pop()()
    assert view._llm_wording is not None
    latest = details[-1]
    sections = {s.id: s for s in latest.sections}
    assert "ages" in sections and "ai-themes" in sections
    assert "non attestés" in sections["ai-themes"].title
    assert book.age_ranges == original
    assert all("ai-themes" not in s.id for s in sections.values() if s.id == "ages")


def test_selection_change_discards_delayed_response():
    view, book, broker, _llm, scheduled, _ = make_view()
    view.selected(NS(key=book_key(book)))
    view.rewrite_selected_themes()
    broker.handles[0].finish()
    view.selected(None)
    scheduled.pop()()
    assert view._llm_wording is None
    assert broker.handles[0].cancelled
