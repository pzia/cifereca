"""Source-level consent callbacks and modern Sync renderer, without provider I/O."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS, ModuleType
import sys

import pytest

from cifereca.gui.sync_action_scope import capture_sync_action_scope, sync_action_scope_current
from cifereca.gui.sync_view import sync_source_presentation
from tests.test_core_ui_1347_sync_action_scope import Widget, _fixture, _source


def renderer():
    path = Path(__file__).resolve().parents[1] / 'src/cifereca/gui/source_sync_consent_controls.py'
    function = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef))
    tree = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), function,
    ], type_ignores=[]))
    env = dict(Gtk=NS(Switch=Widget, Align=NS(CENTER=1)), Adw=NS(ActionRow=Widget),
               sync_action_scope_current=sync_action_scope_current,
               sync_action_row=lambda row: row,
               add_sync_action=lambda row, button: row.add_suffix(button))
    exec(compile(tree, str(path), 'exec'), env)
    return env['append_source_sync_consent_controls']


def rig(*, configured=False, enabled=False, supported=True, valid=True, source_enabled=True):
    window, broker, _calls, _build, _page = _fixture(sources=[])
    source = _source(identity_id='alice', source_id='unknown-source', provider_id='fixture-vendor', enabled=source_enabled)
    state = dict(supported=supported, configured=configured, enabled=enabled,
                 revision='revision-one' if configured else None, valid=valid)
    reads, writes, errors = [], [], []
    broker.source_sync_consent = lambda sid: reads.append(sid) or dict(state)
    def update(sid, *, enabled, expected_revision):
        writes.append((sid, enabled, expected_revision))
        return dict(state, configured=True, enabled=enabled, revision='revision-two')
    broker.set_source_sync_consent = update
    window.show_error = lambda *args: errors.append(args)
    window._refresh_sync_page = lambda: setattr(window, '_sync_preview_generation', window._sync_preview_generation + 1)
    scope = capture_sync_action_scope(window, page_token=window._sync_page_token,
                                     preview_generation=window._sync_preview_generation)
    group = Widget()
    return renderer(), window, broker, source, scope, group, state, reads, writes, errors


def test_rendering_unconfigured_source_never_migrates_or_mutates_consent():
    fn, w, b, s, scope, group, state, reads, writes, errors = rig()
    fn(w, b, s, scope, group)
    row, = group.children
    assert row.title == 'Téléchargement automatique de ce compte'
    assert [x.label for x in row.suffixes] == ['Activer', 'Désactiver']
    assert reads == [s.source_id] and writes == [] and w.runner.tasks == []
    row.suffixes[0].click()
    assert writes == [(s.source_id, True, None)]
    assert errors == []


def test_explicit_off_is_available_without_v2_support():
    fn, w, b, s, scope, group, state, reads, writes, errors = rig(supported=False)
    fn(w, b, s, scope, group)
    on, off = group.children[0].suffixes
    assert not on.sensitive and off.sensitive
    off.click()
    assert writes == [(s.source_id, False, None)]


@pytest.mark.parametrize('prior', [False, True])
def test_configured_toggle_uses_exact_source_revision(prior):
    fn, w, b, s, scope, group, state, reads, writes, errors = rig(configured=True, enabled=prior)
    fn(w, b, s, scope, group)
    toggle, = group.children[0].suffixes
    assert toggle.active is prior and writes == []
    toggle.toggled(not prior)
    assert writes == [(s.source_id, not prior, 'revision-one')]


@pytest.mark.parametrize('mutation', ['profile', 'page', 'closed'])
def test_stale_widget_never_changes_source_permission(mutation):
    fn, w, b, s, scope, group, state, reads, writes, errors = rig(configured=True)
    fn(w, b, s, scope, group)
    if mutation == 'profile':
        w.service.current_identity = NS(identity_id='bob')
    elif mutation == 'page':
        w._refresh_sync_page()
    else:
        w._closed = True
    group.children[0].suffixes[0].toggled(True)
    assert writes == []


def test_failed_update_reports_conflict_and_does_not_retry():
    fn, w, b, s, scope, group, state, reads, writes, errors = rig(configured=True)
    def fail(sid, **kwargs):
        writes.append((sid, kwargs))
        raise ValueError('source_sync_consent_changed')
    b.set_source_sync_consent = fail
    fn(w, b, s, scope, group)
    group.children[0].suffixes[0].toggled(True)
    assert len(writes) == len(errors) == 1 and len(reads) == 1
    assert 'réessayez' in errors[0][0]


@pytest.mark.parametrize('corrupt', ['extra', 'bool', 'revision'])
def test_malformed_response_exposes_no_mutation(corrupt):
    fn, w, b, s, scope, group, state, reads, writes, errors = rig(configured=True)
    if corrupt == 'extra': state['extra'] = 'bad'
    elif corrupt == 'bool': state['enabled'] = 1
    else: state['revision'] = None
    fn(w, b, s, scope, group)
    assert group.children[0].suffixes == [] and writes == []


def test_disabled_source_can_revoke_but_cannot_enable():
    fn, w, b, s, scope, group, state, reads, writes, errors = rig(source_enabled=False)
    fn(w, b, s, scope, group)
    assert not group.children[0].suffixes[0].sensitive
    group.children[0].suffixes[1].click()
    assert writes == [(s.source_id, False, None)]


def test_foreign_source_is_never_rendered_or_read():
    fn, w, b, s, scope, group, state, reads, writes, errors = rig()
    s.identity_id = 'bob'
    fn(w, b, s, scope, group)
    assert group.children == [] and reads == [] and writes == []


def test_callback_keeps_original_source_route_even_if_view_object_changes():
    fn, w, b, s, scope, group, state, reads, writes, errors = rig()
    fn(w, b, s, scope, group)
    original = s.source_id
    s.source_id = 'another-source'
    group.children[0].suffixes[0].click()
    assert writes == [(original, True, None)]


@pytest.mark.parametrize('provider', ['fixture-vendor', 'kobo', 'numilog', 'bnf-pret', 'paris'])
def test_modern_page_uses_same_controls_and_never_reads_legacy_tools(monkeypatch, provider):
    fn = renderer()
    module = ModuleType('cifereca.gui.source_sync_consent_controls')
    module.append_source_sync_consent_controls = fn
    monkeypatch.setitem(sys.modules, module.__name__, module)
    w, b, calls, build, page = _fixture(sources=[])
    source = _source(identity_id='alice', source_id='exact-source', provider_id=provider)
    b.sources = lambda identity: [source]
    state = dict(supported=True, configured=True, enabled=True, revision='revision-one', valid=True)
    b.source_sync_consent = lambda sid: dict(state)
    b.set_source_sync_consent = lambda *args, **kwargs: pytest.fail('render must not mutate')
    for name in ('canonical_acquisition_choices', 'preview_local_materialization', 'audit_local_reconciliation',
                 'local_materialization_schedule', 'bnf_auto_download_enabled', 'numilog_materialization_schedule'):
        setattr(b, name, lambda *_a, **_kw: pytest.fail('modern page must not enter legacy policy/tools'))
    w._refresh_sync_page()
    group, = w._sync_sources_box.children
    assert len(group.children) == 2
    header, consent = group.children
    assert consent.title == 'Téléchargement automatique de ce compte'
    assert header.suffixes[1].label.startswith('Synchroniser ')
    assert 'ne télécharge aucun ebook' not in header.subtitle
    assert 'aucun fichier EPUB/ACSM téléchargé' not in header.subtitle
    assert w.runner.tasks == []


def test_canonical_presentation_is_provider_neutral():
    for provider in ('fixture-vendor', 'kobo', 'numilog', 'bnf-pret', 'paris'):
        source = _source(provider_id=provider)
        shown = sync_source_presentation(source, canonical=True)
        assert shown.action_label == 'Synchroniser ' + source.label
        assert 'fichiers disponibles' in shown.summary
        assert 'ne télécharge aucun ebook' not in shown.summary
