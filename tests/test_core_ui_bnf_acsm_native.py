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
    from cifereca.gui.bnf_acsm_action_controller import prepare_bnf_acsm
    from cifereca.gui.task_runner import TaskRunner,TaskPriority
    import cifereca.services as facade
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'environnements/openai-chat-debian'))
    from x11_input import X11Input
    service,session,account,loan,state,downloaded=connected_core
    candidates=[]
    original_stage=service.stage_bnf_loan_acsm
    def observed_stage(*args,**kwargs):
        result=original_stage(*args,**kwargs)
        if result.get('status')=='candidate_acsm':candidates.append(Path(result['path']))
        return result
    monkeypatch.setattr(service,'stage_bnf_loan_acsm',observed_stage)
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
    button.connect('clicked',lambda _b:prepare_bnf_acsm(window,loan,button,Adw=Adw,TaskPriority=TaskPriority))
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
        facts=[r['detail'] for r in service.diagnostics.snapshot() if r.get('verified_bnf_loan_action')]
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
