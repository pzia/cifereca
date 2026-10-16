"""#1599: a recommendation LLM prompt may contain only public detail reasons.

The test uses a synthetic, explicitly clicked LLM task, no network or GTK.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import LLMResult
from recommendations import UxRecommendationsView, _public_ranked_values


class Poison:
    def __str__(self):
        raise AssertionError("PRIVATE_TOKEN /home/PRIVATE_USER")


class FakeService:
    def __init__(self):
        self.requests = []

    def available(self):
        return True

    def generate(self, request, *, control=None):
        self.requests.append(request)
        return LLMResult(
            ok=True, generated=True,
            text="Raisons communes attestées.", provider="local",
            model="test-model", remote=False,
        )


class Task:
    def __init__(self, operation):
        self.operation = operation
        self.control = NS(cancelled=lambda: False)
        self.callback = None

    def add_done_callback(self, callback):
        self.callback = callback

    def finish(self):
        self.value = self.operation(self.control)
        self.callback(self)

    def result(self):
        return self.value

    def cancel(self):
        pass


class Broker:
    def __init__(self):
        self.tasks = []

    def submit(self, operation, **kwargs):
        task = Task(operation)
        self.tasks.append(task)
        return task


class Label:
    def __init__(self):
        self.text = ""

    def set_label(self, text):
        self.text = text


def fixture(reasons):
    service = FakeService()
    broker = Broker()
    idle = []
    view = object.__new__(UxRecommendationsView)
    view._llm_enabled = True
    view._llm_handle = None
    view._llm_selection_key = "edition-1"
    view._llm_selected_row = NS(key="edition-1")
    view._llm_generation = 0
    view._llm_explanation = None
    view._llm_button = NS(set_sensitive=lambda *_: None)
    view._llm_hint = Label()
    view._busy = False
    view._generation = 1
    view._ranked_by_key = {"edition-1": NS(reasons=reasons, score=85)}
    view.plugin = NS(context=NS(
        service=lambda name: service if name == "llm" else None,
        broker=lambda name: broker if name == "tasks" else None,
    ))
    view.GLib = NS(idle_add=lambda callback: idle.append(callback))
    selected = []
    view._selected_row = selected.append
    return view, service, broker, idle, selected


@pytest.mark.parametrize("poisoned", [
    {"PRIVATE_BEARER_KEY": "metadata"},
    Poison(),
    {"PRIVATE_ACCOUNT": Poison()},
])
def test_non_public_reason_container_never_reaches_llm(poisoned):
    view, service, broker, _, _ = fixture(poisoned)
    assert _public_ranked_values(poisoned) == ()
    view._explain_selected()
    assert service.requests == []
    assert broker.tasks == []
    assert "publique" in view._llm_hint.text


def test_poisoned_values_are_filtered_and_just_visible_reasons_are_sent():
    reasons = (
        "Auteur commun",
        Poison(),
        "PRIVATE\\nACCOUNT",
        "Thème proche",
        "PRIVATE\\u202eSECRET",
        "Troisième motif",
        "Quatrième motif hors budget",
    )
    view, service, broker, idle, selected = fixture(reasons)
    visible = _public_ranked_values(reasons, limit=3)
    assert visible == ("Auteur commun", "Thème proche")
    view._explain_selected()
    assert len(broker.tasks) == 1 and not service.requests
    broker.tasks[0].finish()
    assert len(service.requests) == 1
    prompt = service.requests[0].prompt
    assert "Auteur commun" in prompt and "Thème proche" in prompt
    assert "PRIVATE" not in prompt
    assert "Troisième motif" not in prompt
    assert "Quatrième motif" not in prompt
    assert len(idle) == 1
    assert idle.pop()() is False
    assert selected and view._llm_explanation is not None


def test_a_non_public_str_subclass_does_not_get_split_or_sent():
    class Untrusted(str):
        pass

    view, service, broker, _, _ = fixture(Untrusted("PRIVATE_ACCOUNT"))
    view._explain_selected()
    assert not broker.tasks and not service.requests


def test_valid_reasons_keep_normal_explicit_generation_behavior():
    view, service, broker, idle, _ = fixture(("Série commune", "Auteur proche"))
    view._explain_selected()
    broker.tasks[0].finish()
    assert len(service.requests) == 1
    assert "Série commune" in service.requests[0].prompt
    assert "Auteur proche" in service.requests[0].prompt
    idle.pop()()
    assert view._llm_explanation.text == "Raisons communes attestées."
