"""Core functional GTK acceptance with a real offline reader and synthetic data.

This component recipe does not claim the plugin discovery/entry path. The
production controller worker lifecycle is covered separately in Core tests.
"""
from pathlib import Path
import hashlib
import json
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import zipfile

from cifereca.config import Config
from cifereca.identities.models import Identity
from cifereca.core.library_local_inspection import LocalInspection, open_local_inspection
from cifereca.core.library_reconciliation import _COLUMNS
from cifereca.gui.library_inspection_view import LocalInspectionController
from cifereca.gui.maintenance_result_view import MaintenanceResultView
from cifereca.plugin_api import MaintenanceResult


def exercise(*, parent, Gtk, Adw, GLib, Pango, device, until, walk, report, done):
    report['checks']['inspection_setup_entered']=True
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    library = root/'library'
    for name in ('a','b'): (library/name).mkdir(parents=True)
    for index in range(61):
        with zipfile.ZipFile(library/'a'/f'synthetic-{index:02d}.epub','w') as book:
            book.writestr('mimetype','application/epub+zip')
            book.writestr('META-INF/container.xml',"<container><rootfile full-path='content.opf'/></container>")
            book.writestr('content.opf',"<package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title>Synthetic local case</dc:title><dc:identifier scheme='ISBN'>9780306406157</dc:identifier></metadata></package>")
    database = root/'local.sqlite'
    with sqlite3.connect(database) as db:
        for name,columns in _COLUMNS.items():
            db.execute(f"CREATE TABLE {name} ({','.join(c+' TEXT' for c in columns.split())})")
        db.execute("INSERT INTO entitlements(entitlement_id,identity_id,provider_id,item_id,state) VALUES('right-a','a','numilog','edition-a','active')")
        db.execute("INSERT INTO entitlements(entitlement_id,identity_id,provider_id,account_id,state) VALUES('right-b','b','kobo','FOREIGN_SECRET','active')")
        db.execute("INSERT INTO catalog_entries(catalog_entry_id,identity_id,provider_id,metadata_json) VALUES(?,?,?,?)",('notice-a','a','kobo',json.dumps({'identifiers':[{'scheme':'ISBN','value':'9780306406157'}]})))
        db.execute("INSERT INTO catalog_entries(catalog_entry_id,identity_id,provider_id,item_id,metadata_json) VALUES(?,?,?,?,?)",
                   ('notice-numilog-a','a','numilog','edition-a',
                    json.dumps({'title':'Synthetic edition','identifiers':[{'scheme':'ISBN','value':'9780306406157'}]})))
        db.execute("INSERT INTO placements(placement_id,identity_id,path,provider_id) VALUES(?,?,?,?)",('missing-a','a',str(library/'a'/'missing.epub'),'kobo'))
    cfg=Config.__new__(Config)
    cfg.data={'library':{'directory':str(library)},'identities':[{'id':name,'directory_name':name,'label':name} for name in ('a','b')]}
    identities=[Identity(name,name,directory_name=name) for name in ('a','b')]
    service=NS(config=cfg,identities=identities,current_identity=identities[0],_account_context_epoch=0,storage=NS(path=database))
    def sources():
        return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    before=sources()
    # A real GTK host with the synthetic Core scope lets the production result
    # action and controller execute unchanged; plugin discovery is not stubbed
    # into a claimed full entry E2E.
    report['checks']['inspection_fixture_created']=True
    host=Adw.Window(transient_for=parent,modal=True,title='Inspection locale synthétique')
    host.set_default_size(920,620)
    host.service=service
    host.runner=parent.runner
    def show_error(*_args):
        raise AssertionError('Synthetic local inspection became unavailable')
    host.show_error=show_error
    group=Adw.PreferencesGroup()
    row=Adw.ActionRow(title='Résultat synthétique')
    group.add(row)
    host.set_content(group)
    controller=LocalInspectionController(parent=host,Gtk=Gtk,Adw=Adw,Pango=Pango,GLib=GLib,alive=lambda:not parent._closed and host.get_visible())
    result_view=MaintenanceResultView(parent=host,Gtk=Gtk,Adw=Adw,Pango=Pango,row=row,title='Réconciliation synthétique',alive=lambda:host.get_visible(),inspector=controller)
    widgets={}

    def press(key):
        device.key(key,True);device.key(key,False)

    def choose(dropdown,index,callback):
        # A mapped window does not prove that the native popup owns input.
        # Await GTK state instead of an estimated animation duration.
        def popup_open():
            return any(isinstance(w,Gtk.Popover) and w.get_visible() and w.get_mapped()
                       for w in walk(dropdown))

        def allocated():
            if not dropdown.get_mapped() or not dropdown.get_sensitive() or dropdown.get_width() <= 0:
                return False
            try:
                x,y=device.widget_point(dropdown)
                return x>=0 and y>=0
            except RuntimeError:
                return False

        def open_popup():
            device.click_widget(dropdown)
            until(popup_open,keys)

        def keys():
            press('Home')
            for _ in range(index):press('Down')
            press('Return')
            until(lambda:dropdown.get_selected()==index and not popup_open(),callback)

        until(allocated,open_popup)

    def result_ready():
        report['checks']['inspection_host_mapped']=True
        result_view.publish(MaintenanceResult(True,summary='Lecture seule ; détails privés accessibles localement.'))
        until(lambda:result_view.button.get_mapped(),open_result)

    def open_result():
        report['checks']['inspection_result_button_ready']=True
        device.click_widget(result_view.button)
        until(lambda:result_view.dialog is not None and result_view.dialog.get_mapped(),inspect_action)

    def inspect_action():
        report['checks']['inspection_result_dialog_mapped']=True
        response=next(w for w in walk(result_view.dialog) if isinstance(w,Gtk.Button) and 'Examiner les cas' in str(w.get_label()))
        def allocated():
            if not response.get_mapped():return False
            try:
                x,y=device.widget_point(response)
                return x>=0 and y>=0
            except RuntimeError:
                return False
        def click():
            report['checks']['inspection_click_dispatched']=True
            device.click_widget(response)
            until(lambda:controller.session is not None and controller.dialog is not None and controller.dialog.get_mapped(),initial)
        until(allocated,click)

    def initial():
        report['checks']['inspection_window_mapped']=True
        window=controller.dialog
        assert window.get_width()<=parent.get_width()
        dropdowns=[w for w in walk(window) if isinstance(w,Gtk.DropDown)]
        widgets['provider'],widgets['state']=dropdowns
        widgets['list']=next(w for w in walk(window) if isinstance(w,Gtk.ListBox))
        choose(widgets['provider'],2,numilog)

    def numilog():
        rows=list(walk(widgets['list']))
        labels='\n'.join(w.get_text() for w in rows if isinstance(w,Gtk.Label))
        assert 'Droit sans fichier vérifié' in labels and 'synthetic-' not in labels
        report['checks']['local_inspection_provider_filter_does_not_attribute_isbn_files']=True
        # The Numilog right must show unlinked EPUB candidates *only* after
        # user selection; no ownership is inferred from a shared valid ISBN.
        row = widgets['list'].get_first_child()
        device.click_widget(row)
        until(lambda:any(isinstance(w,Gtk.Label) and '1 / 20' in w.get_text()
                         for w in walk(controller.dialog)), compare_right)

    def compare_right():
        report['checks']['inspection_candidates_initially_visible']=True
        labels='\n'.join(w.get_text() for w in walk(controller.dialog)
                          if isinstance(w,Gtk.Label))
        assert 'Fichiers candidats' in labels
        assert 'non probant' in labels
        report['checks']['candidate_heading_verified']=True
        right = controller.session.page(
            provider='numilog', state='rights_without_verified_file',
        ).cases[0]
        candidate_cases = controller.session.candidate_files(right.key)
        assert len(candidate_cases) == 20
        report['checks']['candidate_count_verified']=True
        assert candidate_cases[0].label in labels
        assert 'Droit sans fichier vérifié' in labels
        report['checks']['candidate_label_verified']=True
        following_candidate=next(w for w in walk(controller.dialog)
                                 if isinstance(w,Gtk.Button)
                                 and w.get_label()=='Candidat ▶')
        report['checks']['candidate_button_found']=True

        def candidate_allocated():
            if not following_candidate.get_mapped() or not following_candidate.get_sensitive():
                return False
            try:
                x, y = device.widget_point(following_candidate)
                return x >= 0 and y >= 0
            except RuntimeError:
                return False

        def candidate_click():
            report['checks']['candidate_button_click_ready']=True
            device.click_widget(following_candidate)
            report['checks']['candidate_button_clicked']=True
            until(lambda:any(isinstance(w,Gtk.Label) and '2 / 20' in w.get_text()
                             for w in walk(controller.dialog)), compare_next)

        until(candidate_allocated, candidate_click)

    def compare_next():
        report['checks']['inspection_candidate_navigation_reached']=True
        labels='\n'.join(w.get_text() for w in walk(controller.dialog)
                          if isinstance(w,Gtk.Label))
        right = controller.session.page(
            provider='numilog', state='rights_without_verified_file',
        ).cases[0]
        candidate_cases = controller.session.candidate_files(right.key)
        assert candidate_cases[1].label in labels
        assert 'FOREIGN_SECRET' not in labels
        report['checks']['local_inspection_right_candidate_side_by_side_no_attestation']=True
        manual=next(w for w in walk(controller.dialog)
                    if isinstance(w,Gtk.Button)
                    and w.get_label()=='Comparer tous les fichiers non liés')
        def ready():
            if not manual.get_mapped() or not manual.get_sensitive():
                return False
            try:
                x,y=device.widget_point(manual)
                return x>=0 and y>=0
            except RuntimeError:
                return False
        def click():
            device.click_widget(manual)
            until(lambda:any(isinstance(w,Gtk.Label) and '1 / 61' in w.get_text()
                             for w in walk(controller.dialog)),manual_first)
        until(ready,click)

    def manual_first():
        texts='\\n'.join(w.get_text() for w in walk(controller.dialog)
                        if isinstance(w,Gtk.Label))
        assert 'Fichiers non liés · comparaison manuelle' in texts
        assert 'Aucun n’est attribué à ce droit' in texts
        assert 'FOREIGN_SECRET' not in texts
        assert controller.session.page(state='local_file_without_attested_right').total == 61
        search=next(w for w in walk(controller.dialog)
                    if isinstance(w,Gtk.SearchEntry)
                    and w.get_placeholder_text()=='Filtrer les fichiers non liés…')
        assert search.get_visible()
        search.set_text('synthetic-59')
        until(lambda:any(isinstance(w,Gtk.Label)
                         and '1 / 1 · exploration non probante' in w.get_text()
                         for w in walk(controller.dialog)),manual_filtered)

    def manual_filtered():
        texts='\\n'.join(w.get_text() for w in walk(controller.dialog)
                        if isinstance(w,Gtk.Label))
        assert 'synthetic-59.epub' in texts
        assert 'FOREIGN_SECRET' not in texts
        assert 'Droit sans fichier vérifié' in texts
        report['checks']['local_inspection_manual_unlinked_filter_same_identity']=True
        switch=next(w for w in walk(controller.dialog)
                    if isinstance(w,Gtk.Button) and w.get_label()=='Voir les candidats exacts')
        def ready():
            if not switch.get_mapped():return False
            try:
                x,y=device.widget_point(switch)
                return x>=0 and y>=0
            except RuntimeError:
                return False
        def click():
            device.click_widget(switch)
            until(lambda:any(isinstance(w,Gtk.Label) and '2 / 20' in w.get_text()
                             for w in walk(controller.dialog)),manual_restored)
        until(ready,click)

    def manual_restored():
        texts='\\n'.join(w.get_text() for w in walk(controller.dialog)
                        if isinstance(w,Gtk.Label))
        assert 'Fichiers candidats' in texts
        assert 'synthetic-59.epub' not in texts
        report['checks']['local_inspection_manual_does_not_change_candidate_set']=True
        choose(widgets['provider'],0,lambda:choose(widgets['state'],3,candidates))

    def candidates():
        assert controller.session.page(state='metadata_candidate').total==61
        following=next(w for w in walk(controller.dialog) if isinstance(w,Gtk.Button) and w.get_label()=='Suivant')
        assert following.get_sensitive()
        device.click_widget(following)
        until(lambda:any(isinstance(w,Gtk.Label) and w.get_text()=='51–61 / 61' for w in walk(controller.dialog)),detail)

    def detail():
        row=widgets['list'].get_first_child()
        device.click_widget(row)
        until(lambda:any(isinstance(w,Gtk.Label) and 'Identifiant notice (isbn13)' in w.get_text() for w in walk(controller.dialog)),verified)

    def verified():
        texts='\n'.join(w.get_text() for w in walk(controller.dialog) if isinstance(w,Gtk.Label))
        assert 'candidat uniquement, aucun droit prouvé' in texts
        assert 'FOREIGN_SECRET' not in texts
        report['checks']['local_inspection_state_pagination_and_private_proof']=True
        session=controller.session
        service._account_context_epoch+=1
        until(lambda:controller.dialog is None,lambda:revoked(session))

    def revoked(session):
        assert not session.valid() and not session.page().cases
        assert sources()==before
        report['checks']['local_inspection_epoch_revokes_window_and_sources_unchanged']=True
        result_view.clear()
        host.destroy()
        tmp.cleanup()
        done()

    host.present()
    until(lambda:host.get_mapped(),result_ready)
