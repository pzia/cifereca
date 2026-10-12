"""Offline #782 evidence-only synthesis and opt-in flow tests."""
from types import SimpleNamespace as NS

from cifereca.plugin_api import LLMResult
from criticism_llm import summary_request, summarize_cached_reviews
from criticism_page import ReviewView


def matched(excerpt="Une réflexion sur le genre du roman.", source="public-a"):
    return NS(article=NS(excerpt=excerpt, source_id=source, provenance=(source,)),
              status="suggested", reasons=("titre et auteur",))


class Service:
    def __init__(self, value=None, error=None):
        self.calls = []
        self.value = value or LLMResult(ok=True, generated=True,
            text="Les avis abordent une interrogation sur le genre.",
            provider="local-1", model="small", remote=False)
        self.error = error

    def available(self):
        return True

    def generate(self, request, *, control=None):
        self.calls.append((request, control))
        if self.error:
            raise self.error
        return self.value


def test_request_is_bounded_cached_excerpts_with_source_not_book_profile_or_full_body():
    rows = (matched("X" * 2000), matched("Y" * 2000, "public-b"),
            matched("Z" * 2000, "public-a"), matched("K" * 2000))
    request, sources = summary_request(rows)
    assert request.task == "generate" and request.max_output_tokens == 128
    assert sources == ("public-a", "public-b")
    assert "K" * 100 not in request.prompt
    assert len(request.prompt) < 1050
    assert "livre_cible" not in request.prompt
    assert summary_request((matched("", "empty"),)) == (None, ())


def test_summary_needs_config_and_explicit_click_and_never_modifies_matches():
    service = Service()
    original = (matched(),)
    for enabled, consent in [(False, False), (True, False), (False, True)]:
        assert summarize_cached_reviews(service, original, enabled=enabled, consent=consent) is None
    assert not service.calls
    result = summarize_cached_reviews(service, original, enabled=True, consent=True)
    assert result.text and result.sources == ("public-a",)
    assert len(service.calls) == 1
    assert original[0].status == "suggested"


def test_failure_cancel_and_invalid_answer_keep_reviews_without_new_claims():
    canceled = NS(cancelled=lambda: True)
    llm = Service()
    assert summarize_cached_reviews(llm, (matched(),), enabled=True, consent=True, control=canceled) is None
    assert not llm.calls
    for service in (
        Service(error=RuntimeError("token password")),
        Service(value=LLMResult(ok=False, reason="private")),
        Service(value=LLMResult(ok=True, generated=False, text="not-generated")),
        Service(value=LLMResult(ok=True, generated=True, text="Z" * 600)),
    ):
        assert summarize_cached_reviews(service, (matched(),), enabled=True, consent=True) is None


class Widget:
    def __init__(self):
        self.text = ""
        self.enabled = False

    def set_label(self, value):
        self.text = str(value)

    def set_sensitive(self, value):
        self.enabled = bool(value)


class Handle:
    def __init__(self, operation):
        self.operation, self.callback = operation, None
        self.cancelled = False
        self.control = NS(cancelled=lambda: self.cancelled)

    def result(self):
        return self.value

    def add_done_callback(self, cb):
        self.callback = cb

    def cancel(self):
        self.cancelled = True

    def finish(self):
        self.value = self.operation(self.control)
        self.callback(self)


class Tasks:
    def __init__(self):
        self.handles = []

    def submit(self, operation, **_):
        handle = Handle(operation)
        self.handles.append(handle)
        return handle


def view_with_matches():
    tasks, service, idle = Tasks(), Service(), []
    view = object.__new__(ReviewView)
    view._llm_enabled, view._llm_generation = True, 0
    view._llm_task, view._llm_matches = None, (matched(),)
    view._llm_button, view._llm_result = Widget(), Widget()
    view.disposed = False
    view.plugin = NS(context=NS(
        service=lambda x: service if x == "llm" else None,
        broker=lambda x: tasks if x == "tasks" else None))
    view.GLib = NS(idle_add=lambda fn: idle.append(fn))
    return view, tasks, service, idle


def test_click_runs_in_worker_only_and_marks_generated_with_origin():
    view, tasks, service, idle = view_with_matches()
    assert not tasks.handles
    view.summarize()
    assert len(tasks.handles) == 1 and not service.calls
    tasks.handles[0].finish()
    assert "Les avis" not in view._llm_result.text
    idle.pop()()
    assert "non attestée" in view._llm_result.text
    assert "public-a" in view._llm_result.text
    assert "Ne confirme aucun rattachement" in view._llm_result.text


def test_stale_summary_never_applies_after_book_or_filter_change():
    view, tasks, _service, idle = view_with_matches()
    view.summarize()
    tasks.handles[0].finish()
    view._reset_llm()
    idle.pop()()
    assert "Les avis" not in view._llm_result.text
    assert tasks.handles[0].cancelled
