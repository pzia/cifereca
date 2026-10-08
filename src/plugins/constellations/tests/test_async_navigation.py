from types import SimpleNamespace as NS
from cifereca.plugin_api import BookRef
from constellations import ConstellationGraph
from constellation_view_model import merge_nodes, node_from_row
from constellations_page import View


class Handle:
    def __init__(self, operation):
        self.operation, self.callback, self.value, self.error = operation, None, None, None
        self.control = NS(cancelled=lambda: False)
        self.cancel_called = False

    def cancel(self):
        # Deliberately model a worker already finishing despite cancellation.
        self.cancel_called = True

    def result(self):
        if self.error:
            raise self.error
        return self.value

    def add_done_callback(self, callback):
        self.callback = callback

    def finish(self):
        try:
            self.value = self.operation(self.control)
        except Exception as error:
            self.error = error
        self.callback(self)


class Broker:
    def __init__(self):
        self.handles = []

    def submit(self, operation, **options):
        handle = Handle(operation)
        self.handles.append(handle)
        return handle


def view_fixture():
    view = object.__new__(View)
    broker, dispatch, messages = Broker(), [], []
    view.generation, view.task, view.disposed = 0, None, False
    view.current_identity = 'alice'
    view.identity_id = lambda: view.current_identity
    view.GLib = NS(idle_add=lambda callback: dispatch.append(callback))
    view.status = NS(set_label=messages.append)
    view.plugin = NS(context=NS(broker=lambda name: broker), pending=None)
    view.render = lambda: None
    return view, broker, dispatch, messages


def test_calculation_and_completion_do_not_run_on_submission():
    view, broker, dispatch, _ = view_fixture()
    computed, rendered = [], []
    view.run_calculation(lambda control: computed.append('worker') or 42, rendered.append, 'test')
    assert computed == [] and rendered == []
    broker.handles[0].finish()
    assert computed == ['worker'] and rendered == []
    dispatch.pop()()
    assert rendered == [42]


def test_superseded_and_identity_stale_completions_are_dropped():
    view, broker, dispatch, _ = view_fixture()
    rendered = []
    view.run_calculation(lambda c: 'old', rendered.append, 'old')
    view.run_calculation(lambda c: 'new', rendered.append, 'new')
    assert broker.handles[0].cancel_called
    broker.handles[0].finish(); dispatch.pop()()
    assert rendered == []
    broker.handles[1].finish()
    view.current_identity = 'bob'
    dispatch.pop()()
    assert rendered == []


def test_disposal_and_error_do_not_replace_visible_state():
    view, broker, dispatch, messages = view_fixture()
    rendered = []
    view.run_calculation(lambda c: 1 / 0, rendered.append, 'failed')
    broker.handles[0].finish(); dispatch.pop()()
    assert not rendered and 'conservés' in messages[-1]
    view.run_calculation(lambda c: 'late', rendered.append, 'late')
    broker.handles[1].finish(); view.disposed = True; dispatch.pop()()
    assert not rendered


def test_promenade_click_only_queues_the_engine(monkeypatch):
    view, broker, dispatch, _ = view_fixture()
    view.nodes = (1, 2);view.start = 1;view.center = 2;view.path = ('previous',)
    view.variant = NS(get_selected=lambda: 1)
    called = []
    monkeypatch.setattr('constellations_page.promenade', lambda *a, **kw: called.append(kw['variant']) or ())
    view.finish_walk()
    assert called == [] and view.path == ('previous',)
    broker.handles[0].finish()
    assert called == ['équilibré'] and view.path == ('previous',)
    dispatch.pop()()
    assert view.path == ()


def test_new_seed_graph_is_built_by_worker(monkeypatch):
    view, broker, dispatch, _ = view_fixture()
    view.nodes = merge_nodes([node_from_row(BookRef('a','A'))])
    view.start, view.path = None, ()
    actual = merge_nodes; called = []
    monkeypatch.setattr('constellations_page.merge_nodes', lambda rows: called.append('graph') or actual(rows))
    selected = [];view.visit = selected.append
    view.seed(BookRef('b','B'))
    assert not called
    broker.handles[0].finish()
    assert called == ['graph'] and not selected
    dispatch.pop()()
    assert selected[0].book.title == 'B'


def test_adjacency_is_reused_without_scanning_all_books(monkeypatch):
    graph = ConstellationGraph([{'book_id': str(i), 'title': str(i), 'authors':['A']} for i in range(40)])
    neighbors = graph.neighbors('book:0')
    monkeypatch.setattr(graph,'edge',lambda *a: (_ for _ in ()).throw(AssertionError('rescan')))
    assert len(neighbors) == 39
    assert graph.neighbors('book:0') is neighbors


def test_cancelled_walk_stops_before_expansion(monkeypatch):
    graph = ConstellationGraph([{'book_id':str(i),'title':str(i),'authors':['A']} for i in range(15)])
    monkeypatch.setattr(graph,'neighbors',lambda *a: (_ for _ in ()).throw(AssertionError('expanded')))
    assert graph.promenade('book:0','book:14',control=NS(cancelled=lambda:True)) is None
