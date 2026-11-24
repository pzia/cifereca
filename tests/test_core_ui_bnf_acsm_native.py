"""#2055: native confirmations, TaskRunner, Core and HTTPX on synthetic loans.

No real BnF/Adobe session. Tests explicitly require the repository GTK runtime.
"""
import os
from pathlib import Path
import sys

import httpx
import pytest

from tests.test_core_p0_2055_bnf_explicit_stage import connected_core

pytestmark=pytest.mark.skipif(not os.environ.get('DISPLAY'),reason='GTK/Xvfb display required')


def walk(widget):
    yield widget
    child=widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child=child.get_next_sibling()


@pytest.mark.parametrize('width',[1366,1600])
@pytest.mark.parametrize('outcome',['http_refused','profile_required','adept_failed'])
def test_native_bnf_actions_are_distinct_and_explain_safe_failure(connected_core,tmp_path,monkeypatch,width,outcome):
    import gi
    gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
    from gi.repository import Adw,GLib,Gtk
    from cifereca.gui.explicit_loan_acsm_controller import prepare_explicit_loan_acsm
    from cifereca.gui.task_runner import TaskRunner,TaskPriority
    import cifereca.services as facade
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'environnements/openai-chat-debian'))
    from x11_input import X11Input
    service,session,account,loan,state,downloaded=connected_core
    candidates=[]
    original_stage=service.stage_explicit_loan_acsm
    def observed_stage(*args,**kwargs):
        result=original_stage(*args,**kwargs)
        if result.get('status')=='candidate_acsm':candidates.append(Path(result['path']))
        return result
    monkeypatch.setattr(service,'stage_explicit_loan_acsm',observed_stage)
    monkeypatch.setattr(facade,'data_dir',lambda:tmp_path/'data')
    if outcome=='http_refused':
        original=session.client.http._transport.handler
        def refusal(request):
            if request.url.path.startswith('/loans/'):
                downloaded.append(request)
                return httpx.Response(403,content=b'private refusal')
            return original(request)
        session.client.http._transport.handler=refusal
    elif outcome=='profile_required':
        def no_profile(_identity):raise facade.AdeptProfileResolutionError('private profile',code='adept_profile_required')
        monkeypatch.setattr(service,'resolve_adept_profile_for_identity',no_profile)
    else:
        monkeypatch.setattr(service,'resolve_adept_profile_for_identity',lambda _i:('synthetic','identity'))
        monkeypatch.setattr(service,'_originating_adept_profile_id',lambda *_a:None)
        def failed_runtime(_request):raise RuntimeError('SECRET Adobe license/private URL')
        monkeypatch.setattr(service,'execute_acquisition_request',failed_runtime)
    Adw.init()
    window=Gtk.Window(default_width=width-40,default_height=780)
    window.service=service;window._closed=False
    window._identity_context_generation=1;window._account_context_generation=1
    errors=[];attention=[];refresh=[]
    box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
    button=Gtk.Button(label='Télécharger le prêt BnF')
    box.append(button);window.set_child(box)
    def show_error(message,title=''):
        errors.append((message,title));box.append(Gtk.Label(label=message,wrap=True))
    window.show_error=show_error
    window.show_attention=lambda message,**kw:attention.append((message,kw))
    window.refresh_library=lambda *_a:refresh.append(True)
    window._activity_result=lambda *_a,**_k:None
    window._task_started=lambda *_a:False
    window._task_finished=lambda *_a:False
    window.runner=TaskRunner(window)
    button.connect('clicked',lambda _b:prepare_explicit_loan_acsm(window,loan,button,Adw=Adw,TaskPriority=TaskPriority,stage=service.stage_explicit_loan_acsm,importer=service.import_bnf_loan_acsm))
    window.present();device=X11Input()
    def wait(predicate):
        loop=GLib.MainLoop();ticks=[0]
        def tick():
            ticks[0]+=1
            if predicate() or ticks[0]>300:loop.quit();return False
            return True
        GLib.timeout_add(10,tick);loop.run()
        assert predicate(),'native BnF acceptance timed out'
    def dialogs():return [w for w in Gtk.Window.list_toplevels() if isinstance(w,Adw.MessageDialog) and w.get_mapped()]
    def choose(label):
        wait(lambda:bool(dialogs()))
        dialog=dialogs()[0]
        target=next(node for node in walk(dialog) if isinstance(node,Gtk.Button) and node.get_label()==label)
        ok,bounds=target.compute_bounds(dialog)
        assert ok and 0<=bounds.get_x() and bounds.get_x()+bounds.get_width()<=dialog.get_width()
        assert target.grab_focus()
        device.key('space',True);device.key('space',False)
    try:
        wait(lambda:button.get_mapped())
        assert not downloaded
        device.click_widget(button)
        wait(lambda:bool(dialogs()))
        assert not downloaded  # opening confirmation does not download
        choose('Récupérer le jeton')
        if outcome=='http_refused':
            wait(lambda:bool(errors))
            assert 'Code : bnf_access_denied' in errors[0][0]
            assert len(downloaded)==1
            assert not service.storage.acquisitions()
        else:
            wait(lambda:bool(dialogs()) and dialogs()[0].get_heading()=='Importer avec ADEPT ?')
            assert len(downloaded)==1 and not service.storage.acquisitions()
            choose('Importer via ADEPT')
            wait(lambda:bool(errors or attention))
            if outcome=='profile_required':
                assert attention[0][1]['code']=='adept_profile_required'
            else:
                assert 'Code : bnf_import_failed' in errors[0][0]
            assert len(candidates)==1 and candidates[0].is_file()
            assert all(a['provider_id']=='manual' for a in service.storage.acquisitions())
        assert not refresh and not service.storage.entitlements()
        facts=[r['detail'] for r in service.diagnostics.snapshot() if r.get('verified_loan_acsm_action')]
        assert len(facts)==(1 if outcome=='http_refused' else 2)
        assert facts[0]['action']=='stage_loan_token'
        assert all('task_ref' in f for f in facts)
        if len(facts)==2:
            assert facts[1]['action']=='import_acsm'
            assert facts[0]['task_ref']!=facts[1]['task_ref']
        assert 'SECRET' not in str(errors)+str(attention)+str(facts)
    finally:
        window._closed=True;window.runner.close()
        for dialog in dialogs():dialog.destroy()
        window.destroy();device.close()


@pytest.mark.parametrize("selection_changed", [False, True])
def test_native_advisory_click_is_read_only_and_scope_bound(selection_changed):
    """Real GTK widgets and task runner; synthetic HTTPX, no live account."""
    from threading import Event
    from types import SimpleNamespace as NS

    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk

    from cifereca.gui.borrower_information_action import add_borrower_information_action
    from cifereca.gui.task_runner import TaskRunner
    from test_p0_2055_borrow_evidence_readonly import EvidenceReadOnlyTests, UNAVAILABLE, RID

    entered, release = Event(), Event()

    def after_detail():
        entered.set()
        if not release.wait(5):
            raise AssertionError("synthetic GTK task was not released")

    session, account, item, calls = EvidenceReadOnlyTests().make(
        detail=UNAVAILABLE, after_detail=after_detail)
    identity = NS(identity_id=account.identity_id)
    connection = {
        "identity_id": account.identity_id, "provider_id": account.provider_id,
        "credential_id": account.credential_id, "enabled": True,
    }
    service = NS(
        _source_context_epoch=0, _account_context_epoch=0, current_identity=identity,
        accounts=[account],
        _sessions={(account.provider_id, account.credential_id): session},
        _session_key=lambda p, c: (p, c),
        _session_for_source=lambda p, c: session,
        _session_for_account=lambda a: session if a is account else None,
        identity_by_id=lambda i: identity if i == identity.identity_id else None,
        _provider_enabled_for_identity=lambda p, i: True,
        storage=NS(provider_connections=lambda **kw: [connection],
                   identity_for_connection=lambda p, c: identity.identity_id),
    )
    Adw.init()
    window = Gtk.Window(default_width=720, default_height=400)
    window.service = service
    window._closed = False
    window._identity_context_generation = 1
    window._account_context_generation = 1
    window._selected_search_id = item.id
    window._search_detail_request_generation = 1
    finished = []
    window._task_started = lambda *args: False
    window._task_finished = lambda *args: finished.append(args) or False
    window.action_button = lambda label, icon: Gtk.Button(label=label)
    window.runner = TaskRunner(window)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    window.set_child(box)
    panel = NS(window=window, _append_content=box.append)

    def wait(predicate):
        loop = GLib.MainLoop()
        ticks = 0

        def tick():
            nonlocal ticks
            ticks += 1
            if predicate() or ticks >= 300:
                loop.quit()
                return False
            return True

        GLib.timeout_add(10, tick)
        loop.run()
        assert predicate(), "native advisory acceptance timed out"

    try:
        assert add_borrower_information_action(panel, item)
        row = next(w for w in walk(box) if isinstance(w, Adw.ActionRow))
        button = next(w for w in walk(row)
                      if isinstance(w, Gtk.Button) and w.get_label() == "Consulter")
        initial = row.get_subtitle()
        window.present()
        wait(button.get_mapped)
        assert calls == []
        assert "non vérifiées" in initial
        button.emit("clicked")
        wait(entered.is_set)
        assert not button.get_sensitive()
        button.emit("clicked")  # even a retained signal cannot submit twice
        if selection_changed:
            window._search_detail_request_generation += 1
        release.set()
        wait(lambda: bool(finished) and button.get_sensitive())
        assert calls == [("GET", "/my_profile/activity"), ("GET", "/resources/" + RID)]
        if selection_changed:
            assert row.get_subtitle() == "Informations non vérifiées."
        else:
            assert "Indisponibilité observée" in row.get_subtitle()
            assert "Permission : inconnue" in row.get_subtitle()
            assert account.unique_id not in row.get_subtitle()
        assert session._borrow_post_attempts == set()
    finally:
        release.set()
        window._closed = True
        window.runner.close()
        window.destroy()
        session.close()
