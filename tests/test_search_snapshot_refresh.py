"""#2114: actual SQL/search/result callbacks, with headless GTK-shaped views."""
import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import time
from unittest.mock import Mock

import pytest
from cifereca.gui.search_snapshot_refresh import invalidate_search_snapshot
from cifereca.gui.query_model import QueryState
from tests.test_search_results_view import _host, Row

ROOT = Path(__file__).resolve().parents[1] / 'src/cifereca/gui'


class Selection:
    def __init__(self, page): self.page, self.index = page, 0xFFFFFFFF
    def get_selected_item(self):
        return self.page.rows[self.index] if self.index < len(self.page.rows) else None
    def set_selected(self, index): self.index = index


class Page:
    def __init__(self, window):
        self.window, self.rows = window, []
        self.store = NS(get_n_items=lambda: len(self.rows), get_item=lambda i: self.rows[i], splice=self.splice)
        self.selection = Selection(self)
        self.count_label = NS(set_label=Mock())
        self.list_stack = NS(set_visible_child_name=Mock())
        self._update_count = Mock()
        self._replacing_rows = False
        self._narrow_detail_visible = False
    def splice(self, index, count, rows): self.rows[index:index+count] = rows


def _window(service=None):
    host = _host()
    if service is not None: host.service = service
    host._closed = False
    host._identity_context_generation = 1
    host._search_generation = 10
    host.state = QueryState(text='Fictional', field='title', providers=('numilog',))
    host.search_controls.state = lambda: host.state
    host.tasks = []
    host.runner = NS(submit=lambda title, work, done, failed=None, **kw:
                     host.tasks.append(NS(work=work, done=done, failed=failed, kw=kw)))
    host.search_page = Page(host)
    tree = ast.parse((ROOT / 'master_detail.py').read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'show_rows')
    # AST replay must include pure helpers referenced by the production method;
    # importing master_detail would require GTK in this headless contract.
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == '_same_book_row_presentation')
    fields = next(n for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == '_BOOK_ROW_VISIBLE_FIELDS'
                          for t in n.targets))
    env = {'BookListItem': Row, '_BOOK_ROW_VISIBLE_FIELDS': ast.literal_eval(fields.value),
           'time': time}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[helper, method], type_ignores=[])),
                 '<actual-show-rows>', 'exec'), env)
    host.search_page.show_rows = env['show_rows'].__get__(host.search_page)
    return host


def _row(key='source:numilog:purchase', **kw):
    return {'key': key, 'title': 'Fictional', 'identity_id': 'alice', 'provider_id': 'numilog',
            'item_id': 'purchase', 'source_refs': [{'provider_id': 'numilog', 'item_id': 'purchase', 'identity_id': 'alice'}], **kw}


def test_same_key_visual_refresh_and_progressive_append_are_both_preserved():
    host = _window()
    page = host.search_page
    old = Row('same', 'old', '', '', '', None, action_label='Télécharger')
    page.show_rows([old]); page.selection.set_selected(0)
    unchanged = Row('same', 'old', '', '', '', None, action_label='Télécharger')
    page.show_rows([unchanged])
    assert page.rows[0] is old  # unchanged presentation still uses append-fast
    new = Row('same', 'new', '', '', '', None, action_label='Ouvrir')
    page.show_rows([new])
    assert page.rows[0] is new and page.selection.get_selected_item() is new
    # The dirty snapshot forces a refresh even for otherwise identical content.
    host._search_local_snapshot_dirty = True
    refreshed = Row('same', 'new', '', '', '', None, action_label='Ouvrir')
    page.show_rows([refreshed])
    assert page.rows[0] is refreshed and page.selection.get_selected_item() is refreshed
    # Other pages ignore Search's dirty flag, but still refresh changed content.
    other = Page(host); other.show_rows = page.show_rows.__func__.__get__(other)
    other.show_rows([old]); other.show_rows([unchanged])
    assert other.rows[0] is old
    other.show_rows([new])
    assert other.rows[0] is new
    extra = Row('other', 'additional', '', '', '', None)
    other.show_rows([new, extra])
    assert other.rows == [new, extra]


@pytest.mark.parametrize('count', [1, 2, 4])
def test_successive_acquisitions_coalesce_without_rendering_old_snapshot(count):
    host = _window()
    host.service.search_known_catalog = Mock(return_value=[_row('latest')])
    invalidate_search_snapshot(host)
    stale = host.tasks[0].work()
    for _ in range(count): invalidate_search_snapshot(host)
    assert len(host.tasks) == 1
    host.tasks[0].done(stale)
    assert not host._search_local_objects and len(host.tasks) == 2
    host.tasks[1].done(host.tasks[1].work())
    assert list(host._search_local_objects) == ['latest']
    assert host._search_snapshot_pending is None
    assert host.service.search_known_catalog.call_count == 2


@pytest.mark.parametrize('mutation', ['identity', 'identity_generation', 'search', 'query', 'closed', 'online'])
def test_stale_completion_never_overwrites_current_view(mutation):
    host = _window()
    host.service.search_known_catalog = Mock(return_value=[_row()])
    invalidate_search_snapshot(host)
    rows = host.tasks[0].work()
    if mutation == 'identity': host.service.current_identity = NS(identity_id='bob')
    if mutation == 'identity_generation': host._identity_context_generation += 1
    if mutation == 'search': host._search_generation += 1
    if mutation == 'query': host.state = replace(host.state, text='Other')
    if mutation == 'closed': host._closed = True
    if mutation == 'online': host._search_mode = 'global'
    host.tasks[0].done(rows)
    assert host._search_local_objects == {} and host.search_page.rows == []


def test_queued_obsolete_read_stays_owned_by_admitted_identity_without_gtk_publish():
    host = _window()
    host.service.search_known_catalog = Mock(return_value=[_row('admitted-A')])
    invalidate_search_snapshot(host)
    host._identity_context_generation += 1
    result = host.tasks[0].work()
    assert result == [_row('admitted-A')]
    host.service.search_known_catalog.assert_called_once_with(
        'Fictional', 'all', field='title', identity_id='alice',
    )
    host.tasks[0].done(result)
    assert not host.search_page.rows
    host.toast.assert_not_called()
    assert host._search_snapshot_pending is None


def test_delayed_sql_read_pins_query_owner_service_across_aba_and_window_close():
    host = _window()
    admitted = host.service
    admitted.search_known_catalog = Mock(return_value=[_row('A')])
    invalidate_search_snapshot(host)
    host.service.current_identity = NS(identity_id='bob')
    host._identity_context_generation += 1
    host.service.current_identity = NS(identity_id='alice')
    host._identity_context_generation += 1
    host.state = replace(host.state, text='Other')
    host.service = NS(
        current_identity=NS(identity_id='alice'),
        search_known_catalog=Mock(side_effect=AssertionError('wrong service')),
    )
    host._closed = True
    result = host.tasks[0].work()
    assert result == [_row('A')]
    admitted.search_known_catalog.assert_called_once_with(
        'Fictional', 'all', field='title', identity_id='alice',
    )
    host.service.search_known_catalog.assert_not_called()
    host.tasks[0].done(result)
    assert not host.search_page.rows
    assert host._search_snapshot_pending is None


def test_delayed_sql_read_never_rebinds_a_identity_to_b_before_worker_start():
    host = _window()
    lookups = []
    host.service.search_known_catalog = Mock(
        side_effect=lambda query, scope, *, field, identity_id:
            lookups.append((identity_id, query)) or [_row(identity_id)],
    )
    invalidate_search_snapshot(host)
    host.service.current_identity = NS(identity_id='bob')
    host._identity_context_generation += 1
    first = host.tasks[0].work()
    host.tasks[0].done(first)
    assert not host.search_page.rows
    assert lookups == [('alice', 'Fictional')]
    invalidate_search_snapshot(host)
    second = host.tasks[1].work()
    host.tasks[1].done(second)
    assert lookups == [
        ('alice', 'Fictional'), ('bob', 'Fictional'),
    ]
    assert host._search_snapshot_pending is None


def test_online_invalidation_only_marks_dirty_until_next_explicit_render():
    host = _window()
    host._search_mode = 'global'
    host._remote_search_results = [object()]
    before = host._search_generation
    invalidate_search_snapshot(host)
    assert host.tasks == [] and host._search_mode == 'global'
    assert len(host._remote_search_results) == 1 and host._search_generation == before
    assert host._search_local_snapshot_dirty is True


def test_late_facets_apply_without_changing_captured_sql_query():
    host = _window()
    host.service.search_known_catalog = Mock(return_value=[_row(provider_id='numilog'), _row('other', provider_id='paris')])
    invalidate_search_snapshot(host)
    host.state = replace(host.state, providers=('paris',))
    host.tasks[0].done(host.tasks[0].work())
    assert [r.key for r in host.search_page.rows] == ['other']
    host.service.search_known_catalog.assert_called_once_with('Fictional', 'all', field='title', identity_id='alice')


def _direct_callback_host(monkeypatch):
    from cifereca.gui import search_snapshot_refresh
    from cifereca.task_queue import TaskPriority
    tree = ast.parse((ROOT / 'main_window.py').read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'download_direct_item')
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    env = {'__package__': 'cifereca.gui', '__name__': 'cifereca.gui.main_window', 'TaskPriority': TaskPriority}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, method], type_ignores=[])), '<actual-download-callback>', 'exec'), env)
    host = _window()
    host._activity_result = Mock()
    host.refresh_library = Mock(); host.refresh_recent = Mock()
    invalidate = Mock()
    monkeypatch.setattr(search_snapshot_refresh, 'invalidate_search_snapshot', invalidate)
    host.download_direct_item = env['download_direct_item'].__get__(host)
    return host, invalidate


@pytest.mark.parametrize('state', ['complete', 'no_path', 'identity', 'generation', 'closed'])
def test_actual_direct_completion_invalidates_only_success_in_original_context(monkeypatch, state):
    host, invalidate = _direct_callback_host(monkeypatch)
    host.download_direct_item(NS(source_provider='numilog'))
    result = NS(path=Path('/synthetic/book.epub') if state != 'no_path' else None,
                acquisition=NS(action='direct'))
    if state == 'identity': host.service.current_identity = NS(identity_id='bob')
    if state == 'generation': host._identity_context_generation += 1
    if state == 'closed': host._closed = True
    host.tasks[0].done(result)
    if state == 'complete':
        invalidate.assert_called_once_with(host)
        host.refresh_library.assert_called_once_with(False)
    else:
        invalidate.assert_not_called(); host.refresh_library.assert_not_called()


def test_actual_direct_worker_rejects_context_changed_while_queued(monkeypatch):
    from cifereca.plugin_api import Cancelled
    host, invalidate = _direct_callback_host(monkeypatch)
    host.service.obtain_search_item = Mock()
    host.download_direct_item(NS(source_provider='numilog'))
    host._identity_context_generation += 1
    with pytest.raises(Cancelled): host.tasks[0].work()
    host.service.obtain_search_item.assert_not_called()
    invalidate.assert_not_called()


def test_actual_owned_download_callback_invalidates_after_indexed_path(monkeypatch):
    from cifereca.core.selected_source_offer import SourceOfferTarget
    from cifereca.gui import search_snapshot_refresh
    from cifereca.gui.local_book_action_controller import _add_owned_download
    from cifereca.task_queue import TaskPriority
    host = _window()
    host._activity_result = Mock(); host.refresh_library = Mock(); host.refresh_recent = Mock()
    target = SourceOfferTarget(
        provider_id='synthetic-books', identity_id='alice',
        credential_id='credential', account_id='account',
        item_id='book', branch='purchases', session=object(),
    )
    host.service.owned_library_download_target = Mock(return_value=target)
    from dataclasses import replace
    host.service.capture_admitted_owned_library_offer = Mock(
        return_value=replace(target, selection_epoch=None, source_epoch=0),
    )
    host.service.obtain_admitted_owned_library_offer = Mock(return_value=NS(path=Path('/synthetic/book.epub')))
    handlers = {}
    button = NS(set_tooltip_text=Mock(), connect=lambda signal, cb: handlers.update({signal: cb}))
    host.action_button = lambda *a: button
    invalidate = Mock(); monkeypatch.setattr(search_snapshot_refresh, 'invalidate_search_snapshot', invalidate)
    _add_owned_download(host, NS(add_actions=Mock()), {}, TaskPriority)
    handlers['clicked'](button)
    host.tasks[0].done(host.tasks[0].work())
    invalidate.assert_called_once_with(host)
    host.refresh_library.assert_called_once_with(False)


def test_online_request_before_first_batch_is_not_cancelled_by_acquisition():
    host = _window()
    host._online_pending_key = (host.state, 'alice')
    host._online_pending_generation = host._search_generation
    generation = host._search_generation
    invalidate_search_snapshot(host)
    assert host.tasks == [] and host._search_generation == generation
    assert host._search_local_snapshot_dirty is True
