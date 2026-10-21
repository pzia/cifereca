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
