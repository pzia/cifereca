"""Offline #783 optional semantic exploration, never a confirmed graph edge."""
from types import SimpleNamespace as NS

from cifereca.plugin_api import LLMResult
from semantic_llm import pick_unlinked_pair, semantic_request, propose_semantic_link
from constellations_page import View


class Graph:
    def __init__(self, subjects):
        self.books = {k: NS(subjects=v) for k, v in subjects.items()}
        self.edges = set()

    def edge(self, a, b):
        return True if (a, b) in self.edges else None


def pair():
    graph = Graph({"A": ("poésie", "voyages"), "B": ("géographie", "imaginaire"),
                   "C": ("histoire", "culture")})
    nodes = [NS(key=k, graph=graph, book=NS(title="PRIVATE_TITLE_" + k))
             for k in graph.books]
    return nodes, graph


class Service:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result or LLMResult(ok=True, generated=True,
            text="Hypothèse : l'imaginaire du voyage peut évoquer les territoires.",
            provider="local", model="compact", remote=False)
        self.error = error

    def available(self):
        return True

    def generate(self, request, *, control=None):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result


def test_prompt_bounded_to_attested_subjects_no_titles_identity_or_rights():
    nodes, graph = pair()
    request = semantic_request(nodes[0], nodes[1])
    assert request is not None and request.max_output_tokens == 96
    assert "poésie" in request.prompt and "géographie" in request.prompt
    assert "PRIVATE_TITLE" not in request.prompt
    assert "identity" not in request.prompt
    assert graph.edge(nodes[0].key, nodes[1].key) is None


def test_no_implicit_llm_and_no_edge_mutation():
    nodes, graph = pair()
    svc = Service()
    assert propose_semantic_link(svc, nodes[0], nodes[1]) is None
    assert propose_semantic_link(svc, nodes[0], nodes[1], enabled=True) is None
    assert propose_semantic_link(svc, nodes[0], nodes[1], consent=True) is None
    assert not svc.calls
    result = propose_semantic_link(svc, nodes[0], nodes[1], enabled=True, consent=True)
    assert result is not None and result.text.startswith("Hypothèse :")
    assert not graph.edges
    assert len(svc.calls) == 1


def test_only_unlinked_candidate_and_explicit_subjects():
    nodes, graph = pair()
    graph.edges.add(("A", "B"))
    assert pick_unlinked_pair(nodes, nodes[0]) is nodes[2]
    graph.edges.add(("A", "C"))
    assert pick_unlinked_pair(nodes, nodes[0]) is None
    graph.books["A"].subjects = ()
    assert semantic_request(nodes[0], nodes[1]) is None


def test_unknown_service_and_untrusted_result_cannot_create_hypothesis():
    nodes, graph = pair()
    canceled = NS(cancelled=lambda: True)
    assert propose_semantic_link(Service(), nodes[0], nodes[1],
                                 enabled=True, consent=True, control=canceled) is None
    cases = (
        Service(error=RuntimeError("Authorization secret")),
        Service(result=LLMResult(ok=False, reason="PRIVATE")),
        Service(result=LLMResult(ok=True, generated=False, text="Hypothèse : fausse.")),
        Service(result=LLMResult(ok=True, generated=True, text="Affirmation certifiée")),
        Service(result=LLMResult(ok=True, generated=True, text="Hypothèse : " + "x" * 330)),
    )
    for svc in cases:
        assert propose_semantic_link(svc, nodes[0], nodes[1],
                                     enabled=True, consent=True) is None
    assert not graph.edges


class Widget:
    def __init__(self):
        self.enabled, self.text = False, ""

    def set_sensitive(self, value):
        self.enabled = bool(value)

    def set_label(self, value):
        self.text = str(value)


class Handle:
    def __init__(self, func):
        self.func, self.callback = func, None
        self.cancelled = False
        self.control = NS(cancelled=lambda: self.cancelled)

    def add_done_callback(self, callback):
        self.callback = callback

    def cancel(self):
        self.cancelled = True

    def result(self):
        return self.value

    def finish(self):
        self.value = self.func(self.control)
        self.callback(self)


class Tasks:
    def __init__(self):
        self.handles = []

    def submit(self, fn, **kwargs):
        handle = Handle(fn)
        self.handles.append(handle)
        return handle


def view_fixture():
    nodes, graph = pair()
    svc, tasks, idle = Service(), Tasks(), []
    view = object.__new__(View)
    view.center, view.nodes = nodes[0], tuple(nodes)
    view._semantic_candidate = nodes[1]
    view._semantic_generation = 0
    view._semantic_task = None
    view._semantic_enabled = True
    view._semantic_button, view._semantic_note = Widget(), Widget()
    view.disposed = False
    view.identity_id = lambda: "alice"
    view.plugin = NS(context=NS(
        service=lambda name: svc if name == "llm" else None,
        broker=lambda name: tasks if name == "tasks" else None))
    view.GLib = NS(idle_add=lambda callback: idle.append(callback))
    return view, nodes, graph, svc, tasks, idle


def test_click_only_calls_worker_and_labels_hypothesis_as_outside_graph():
    view, nodes, graph, svc, tasks, idle = view_fixture()
    assert not svc.calls
    view.propose_semantic()
    assert len(tasks.handles) == 1 and not svc.calls
    tasks.handles[0].finish()
    assert not view._semantic_note.text.startswith("Piste sémantique")
    idle.pop()()
    assert "NON attestée, hors graphe" in view._semantic_note.text
    assert nodes[1].book.title in view._semantic_note.text
    assert not graph.edges


def test_change_of_center_discards_delayed_hypothesis():
    view, nodes, _graph, _svc, tasks, idle = view_fixture()
    view.propose_semantic()
    tasks.handles[0].finish()
    view._reset_semantic()
    view.center = nodes[2]
    idle.pop()()
    assert "NON attestée" not in view._semantic_note.text
    assert tasks.handles[0].cancelled
