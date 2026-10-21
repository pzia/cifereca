"""Exercise the production local inspector worker/GTK lifecycle without GI."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.library_local_inspection import LocalInspection
from cifereca.gui import library_inspection_view as view
from tests.test_library_audit_broker import scoped
from tests.test_core_ui_1358_maintenance_preview_cancellation import Dialog


@pytest.fixture
def mounted(scoped, monkeypatch):
    service, _, _ = scoped
    destroyed, jobs, errors = [], [], []
    monkeypatch.setattr(Dialog,'destroy',lambda self:destroyed.append(self),raising=False)
    parent = NS(service=service, runner=NS(submit=lambda *args,**kw:jobs.append((args,kw))),
                show_error=lambda *args:errors.append(args))
    alive = [True]
    controller = view.LocalInspectionController(parent=parent,Gtk=NS(),Adw=NS(MessageDialog=Dialog),
        Pango=NS(),GLib=NS(source_remove=lambda _:None),alive=lambda:alive[0])
    presented = []
    monkeypatch.setattr(controller,'present',lambda:presented.append(controller.session))
    return NS(controller=controller,jobs=jobs,errors=errors,alive=alive,presented=presented,destroyed=destroyed)


def test_read_runs_only_on_worker_and_never_exposes_private_result_to_activity(mounted):
    m=mounted
    m.controller.start()
    assert m.controller.session is None and not m.presented
    args,kw=m.jobs[0]
    assert kw['kind']=='maintenance-inspection'
    assert args[0]=='Inspection locale de bibliothèque'
    session=args[1]()
    assert isinstance(session,LocalInspection)
    args[2](session)
    assert m.presented==[session] and m.destroyed and not m.errors
    m.controller.clear()
    assert not session.valid()


@pytest.mark.parametrize('mode',['cancel','detached','superseded','identity'])
def test_delayed_private_session_is_revoked_instead_of_presented(mounted,mode):
    m=mounted
    m.controller.start()
    args,_=m.jobs[0]
    session=args[1]()
    if mode=='cancel': m.controller.clear()
    elif mode=='detached': m.alive[0]=False
    elif mode=='identity': m.controller.parent.service._account_context_epoch+=1
    else: m.controller.start()
    args[2](session)
    assert not m.presented and not session.valid()


def test_loader_cancel_discards_result_and_failures_are_literal(mounted,monkeypatch):
    m=mounted
    m.controller.start()
    dialog=m.controller.dialog
    dialog.handlers['response'](dialog,'cancel')
    args,_=m.jobs[0]
    args[2](args[1]())
    assert not m.presented and not m.errors
    m.controller.start()
    args,_=m.jobs[-1]
    def fail(*args,**kw):raise ValueError('PRIVATE title/path/account')
    monkeypatch.setattr(view,'open_local_inspection',fail)
    with pytest.raises(RuntimeError,match='^Inspection locale indisponible.$') as caught:
        args[1]()
    assert caught.value.__suppress_context__
    args[3](caught.value)
    assert 'PRIVATE' not in repr(m.errors)


@pytest.mark.parametrize(('reason', 'expected'), [
    ('snapshot_time_budget', 'durée maximale'),
    ('snapshot_wal_reader_unavailable', 'WAL'),
    ('snapshot_reader_unavailable', 'lecture sécurisée'),
    ('snapshot_unavailable', 'preuves SQLite locales'),
    ('snapshot_changed', 'a changé pendant la lecture'),
    ('snapshot_identity_migration', 'migration'),
    ('reconciliation_scope_changed', 'configuration a changé'),
    ('reconciliation_identity_not_active', 'n’est plus active'),
])
def test_closed_inspection_failures_have_distinct_safe_explanations(
    mounted, reason, expected,
):
    from cifereca.core.library_audit_broker import _unavailable
    m = mounted
    m.controller.start()
    args, _ = m.jobs[-1]
    result = _unavailable({reason})
    assert not isinstance(result, LocalInspection)
    args[2](result)
    assert len(m.errors) == 1
    message, title = m.errors[-1]
    assert title == 'Inspection locale'
    assert expected in message
    assert 'PRIVATE' not in message
    if reason == 'snapshot_time_budget':
        assert 'contexte modifié' not in message
        assert 'ne prouve pas leur absence' in message
    assert not m.presented and m.controller.session is None
    assert m.controller.dialog is None


def test_actual_broker_timeout_envelope_is_not_misdiagnosed_as_context_change(
    mounted, monkeypatch,
):
    from cifereca.core import library_audit_broker as broker
    from cifereca.core.library_reconciliation import EvidenceSnapshot
    m = mounted
    calls = []
    def timeout(_path, **_kwargs):
        calls.append(1)
        return EvidenceSnapshot(reasons={'snapshot_time_budget'})
    monkeypatch.setattr(broker, 'read_library_snapshot', timeout)
    m.controller.start()
    args, _ = m.jobs[-1]
    result = args[1]()  # Real Core broker, no DB/WAL mutation or network.
    assert result['status'] == 'incomplete_scan'
    assert result['reasons'] == ['snapshot_time_budget']
    assert calls == [1]
    args[2](result)
    assert 'durée maximale' in m.errors[-1][0]
    assert 'changé' not in m.errors[-1][0]
    assert not m.presented


@pytest.mark.parametrize('payload', [
    None,
    'PRIVATE_SOURCE_PATH',
    {'schema': 1, 'status': 'incomplete_scan', 'reasons': ['snapshot_time_budget']},
    {'schema': 1, 'status': 'complete', 'read_only': True,
     'acquisition_authorized': False, 'repair_authorized': False,
     'reasons': ['snapshot_time_budget']},
    {'schema': 1, 'status': 'incomplete_scan', 'read_only': True,
     'acquisition_authorized': False, 'repair_authorized': False,
     'reasons': ['snapshot_time_budget', 'reconciliation_scope_changed']},
    {'schema': 1, 'status': 'incomplete_scan', 'read_only': True,
     'acquisition_authorized': False, 'repair_authorized': False,
     'reasons': ['PRIVATE_SOURCE_PATH']},
    {'schema': 1, 'status': 'incomplete_scan', 'read_only': True,
     'acquisition_authorized': False, 'repair_authorized': False,
     'reasons': ['snapshot_time_budget', 'PRIVATE_SOURCE_PATH']},
])
def test_untrusted_or_mixed_inspection_envelopes_never_publish_private_data(
    mounted, payload,
):
    m = mounted
    m.controller.start()
    args, _ = m.jobs[-1]
    args[2](payload)
    assert len(m.errors) == 1
    msg, title = m.errors[-1]
    assert title == 'Inspection locale'
    assert msg == view._INSPECTION_FALLBACK
    assert 'PRIVATE' not in repr(m.errors)
    assert not m.presented


def test_invalidated_real_private_session_reports_context_and_is_destroyed(mounted):
    m = mounted
    m.controller.start()
    args, _ = m.jobs[-1]
    session = args[1]()
    assert isinstance(session, LocalInspection)
    m.controller.parent.service._account_context_epoch += 1
    args[2](session)
    assert not session.valid()
    assert len(m.errors) == 1
    assert 'configuration a changé' in m.errors[0][0]
    assert not m.presented
