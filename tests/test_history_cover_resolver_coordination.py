"""Exercise resolver coordination with deterministic worker/main-loop queues."""
import ast
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
import threading


def _resolver_types():
    path = Path(__file__).parents[1] / 'src/cifereca/gui/history_cover_resolver.py'
    tree = ast.parse(path.read_text())
    declarations = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    queue = []
    idle = []
    class Executor:
        def __init__(self, **kwargs):
            self.closed = False
        def submit(self, fn, *args):
            future = Future(); queue.append((future, fn, args)); return future
        def shutdown(self, **kwargs):
            self.closed = True
    namespace = {'__name__': 'resolver_contract', 'threading': threading,
                 'ThreadPoolExecutor': Executor, 'GLib': SimpleNamespace(idle_add=lambda fn, *args: idle.append((fn, args)))}
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, *declarations], type_ignores=[])), str(path), 'exec'), namespace)
    return namespace['HistoryCoverResolver'], queue, idle


def test_duplicates_share_lookup_cancelled_observer_is_not_delivered_and_cache_is_reused():
    resolver_type, workers, idle = _resolver_types()
    calls = []
    deliveries = []
    diagnostics = []
    def lookup(*args):
        calls.append(args)
        return 'https://example.invalid/cover.png', 'matched'
    resolver = resolver_type(SimpleNamespace(resolve_history_thumbnail=lookup),
                             SimpleNamespace(add=lambda *args, **kw: diagnostics.append((args, kw))))
    args = ('paris', 'book', 'Title', 'Author', '2020', None)
    first = resolver.request(*args, lambda url: deliveries.append(('first', url)))
    resolver.request(*args, lambda url: deliveries.append(('second', url)))
    assert len(workers) == 1
    assert resolver.stats()['pending'] == 1
    first.cancel()
    future, fn, arguments = workers.pop()
    future.set_result(fn(*arguments))
    assert len(calls) == 1 and deliveries == []
    assert len(idle) == 1
    fn, arguments = idle.pop(); fn(*arguments)
    assert deliveries == [('second', 'https://example.invalid/cover.png')]
    resolver.request(*args, lambda url: deliveries.append(('cached', url)))
    assert not workers and len(idle) == 1
    fn, arguments = idle.pop(); fn(*arguments)
    assert deliveries[-1] == ('cached', 'https://example.invalid/cover.png')
    stats = resolver.stats()
    assert stats['matched'] == 1 and stats['completed'] == 1
    assert stats['memory_hit'] == 1 and stats['pending'] == 0
    assert len(diagnostics) == 1
    resolver.close()
    assert resolver.pool.closed
