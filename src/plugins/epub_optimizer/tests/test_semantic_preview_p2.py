"""P2 #784: opt-in semantic previews with no EPUB read/write or implicit LLM."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import LLMResult
from cifereca_epub_optimizer.semantic_preview import (
    MAX_INPUT, MAX_OUTPUT, generate_preview, modes, preview_request,
)
from cifereca_epub_optimizer.ui import OptimizerPage


PASSAGE = (
    "Les explorateurs traversèrent la forêt, puis observèrent le ciel. "
    "Ils décidèrent de rentrer avant le coucher du soleil."
)


class Service:
    def __init__(self, *, result=None, failure=None, available=True):
        self.result_value = result if result is not None else LLMResult(
            ok=True, generated=True,
            text="Les explorateurs ont parcouru la forêt et regardé le ciel.",
            model="small", provider="local-provider", remote=False,
        )
        self.failure = failure
        self.is_available = available
        self.calls = []

    def available(self):
        return self.is_available

    def generate(self, request, *, control=None):
        self.calls.append((request, control))
        if self.failure:
            raise self.failure
        return self.result_value


@pytest.mark.parametrize("mode", ["simplify", "summary", "glossary"])
def test_all_three_operations_are_readonly_and_explicit(mode):
    request = preview_request(PASSAGE, mode)
    assert request.task == "generate" and request.max_output_tokens == 384
    data = json.loads(request.prompt)
    assert data["user_pasted_excerpt"] == PASSAGE
    assert not hasattr(request, "source_path")
    assert mode in dict(modes())


@pytest.mark.parametrize("passage,mode", [
    ("short", "summary"), ("x" * (MAX_INPUT + 1), "summary"),
    (PASSAGE, "unknown"), (PASSAGE + "\x00", "summary"),
    (PASSAGE + "\x01", "summary"), (None, "summary"),
])
def test_invalid_or_oversized_excerpt_not_silently_truncated(passage, mode):
    assert preview_request(passage, mode) is None


def test_double_opt_in_is_required_before_any_call():
    llm = Service()
    assert generate_preview(llm, PASSAGE, "simplify") is None
    assert generate_preview(llm, PASSAGE, "simplify", enabled=True) is None
    assert generate_preview(llm, PASSAGE, "simplify", consent=True) is None
    assert generate_preview(None, PASSAGE, "simplify", enabled=True, consent=True) is None
    assert not llm.calls


def test_generated_response_is_bounded_marked_and_sourced():
    llm = Service(result=LLMResult(
        ok=True, generated=True, text=" Une version  plus claire. ",
        provider="provider-secret space", model="small_12", remote=True,
    ))
    result = generate_preview(llm, PASSAGE, "simplify", enabled=True, consent=True)
    assert result.text == "Une version plus claire."
    assert result.provider == "non précisé"
    assert result.model == "small_12"
    assert result.remote
    assert result.input_chars == len(PASSAGE)
    assert len(llm.calls) == 1


def test_errors_refusals_and_cancellation_fail_closed():
    cancelled = NS(cancelled=lambda: True)
    service = Service()
    assert generate_preview(service, PASSAGE, "summary", enabled=True,
                            consent=True, control=cancelled) is None
    assert not service.calls
    for candidate in (
        Service(failure=RuntimeError("token=value SECRET")),
        Service(available=False),
        Service(result=LLMResult(ok=False, reason="bearer SECRET")),
        Service(result=LLMResult(ok=True, generated=False, text="not-generated")),
        Service(result=LLMResult(ok=True, generated=True, text="X" * (MAX_OUTPUT + 1))),
    ):
        assert generate_preview(candidate, PASSAGE, "summary",
                                enabled=True, consent=True) is None


class Widget:
    def __init__(self, *, value="", active=True):
        self.value, self.active = value, active
        self.sensitive, self.label = False, ""

    def set_sensitive(self, value):
        self.sensitive = bool(value)

    def set_label(self, value):
        self.label = str(value)

    def get_active(self):
        return self.active

    def get_active_id(self):
        return self.value


class Buffer:
    def __init__(self, text):
        self.text = text

    def get_start_iter(self):
        return 0

    def get_end_iter(self):
        return len(self.text)

    def get_text(self, _start, _end, _include_hidden):
        return self.text


class Handle:
    def __init__(self, func):
        self.func, self.cb, self.was_cancelled = func, None, False
        self.control = NS(cancelled=lambda: self.was_cancelled)

    def cancel(self):
        self.was_cancelled = True

    def add_done_callback(self, cb):
        self.cb = cb

    def result(self):
        return self.output

    def finish(self):
        self.output = self.func(self.control)
        self.cb(self)


class Tasks:
    def __init__(self):
        self.handles = []

    def submit(self, work, **_kwargs):
        handle = Handle(work)
        self.handles.append(handle)
        return handle


def partial_page(service=None):
    service, tasks, idle = service or Service(), Tasks(), []
    page = object.__new__(OptimizerPage)
    page._closed, page._semantic_enabled = False, True
    page._semantic_handle, page._semantic_generation = None, 0
    page._semantic_source_buffer = Buffer(PASSAGE)
    page._semantic_mode = Widget(value="simplify")
    page._semantic_consent = Widget(active=True)
    page._semantic_button, page._semantic_preview_label = Widget(), Widget()
    page._task_handle = None
    page._tasks = tasks
    page.context = NS(service=lambda key: service if key == "llm" else None)
    return page, service, tasks, idle


def test_preview_ui_does_not_submit_until_click(monkeypatch):
    page, service, tasks, idle = partial_page()
    from types import ModuleType
    gi = ModuleType("gi")
    repository = ModuleType("gi.repository")
    repository.GLib = NS(idle_add=lambda fn: idle.append(fn))
    gi.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    page._refresh_semantic_button()
    assert page._semantic_button.sensitive
    assert not service.calls and not tasks.handles
    page._preview_semantic(None)
    assert len(tasks.handles) == 1 and service.calls == []
    tasks.handles[0].finish()
    assert not page._semantic_preview_label.label.startswith("PRÉVISUALISATION")
    idle.pop()()
    assert "PRÉVISUALISATION IA" in page._semantic_preview_label.label
    assert "Aucune modification" in page._semantic_preview_label.label


def test_edit_cancels_and_stale_preview_cannot_reappear(monkeypatch):
    page, _service, tasks, idle = partial_page()
    from types import ModuleType
    gi = ModuleType("gi")
    repository = ModuleType("gi.repository")
    repository.GLib = NS(idle_add=lambda fn: idle.append(fn))
    gi.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    page._preview_semantic(None)
    tasks.handles[0].finish()
    page._semantic_source_buffer.text = PASSAGE + " Et ils revinrent."
    page._invalidate_semantic_preview()
    idle.pop()()
    assert tasks.handles[0].was_cancelled
    assert "PRÉVISUALISATION IA" not in page._semantic_preview_label.label


def test_no_fs_access_even_when_an_epub_path_is_present(tmp_path: Path):
    secret = tmp_path / "private.epub"
    secret.write_bytes(b"PRIVATE EPub CONTENT that should never be read")
    service = Service()
    result = generate_preview(service, PASSAGE, "glossary", enabled=True, consent=True)
    assert result is not None
    assert service.calls[0][0].prompt.find(str(secret)) == -1
    assert secret.read_bytes() == b"PRIVATE EPub CONTENT that should never be read"
