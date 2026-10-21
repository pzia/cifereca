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


def exercise(*, parent, Gtk, Adw, GLib, Pango, device, until, walk, report, done):
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
        db.execute("INSERT INTO placements(placement_id,identity_id,path,provider_id) VALUES(?,?,?,?)",('missing-a','a',str(library/'a'/'missing.epub'),'kobo'))
    cfg=Config.__new__(Config)
    cfg.data={'library':{'directory':str(library)},'identities':[{'id':name,'directory_name':name,'label':name} for name in ('a','b')]}
    identities=[Identity(name,name,directory_name=name) for name in ('a','b')]
    service=NS(config=cfg,identities=identities,current_identity=identities[0],_account_context_epoch=0,storage=NS(path=database))
    def sources():
        return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    before=sources()
    controller=LocalInspectionController(parent=parent,Gtk=Gtk,Adw=Adw,Pango=Pango,GLib=GLib,alive=lambda:not parent._closed)
    widgets={}

    def press(key):
        device.key(key,True);device.key(key,False)

    def choose(dropdown,index,callback):
        device.click_widget(dropdown)
        def keys():
            press('Home')
            for _ in range(index):press('Down')
            press('Return')
            until(lambda:dropdown.get_selected()==index,callback)
        GLib.timeout_add(120,lambda:(keys(),False)[1])

    def opened(session):
        assert isinstance(session,LocalInspection) and session.valid()
        controller.session=session
        controller.present()
        until(lambda:controller.dialog is not None and controller.dialog.get_mapped(),initial)

    def initial():
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
        tmp.cleanup()
        done()

    def failed(_exc):
        raise AssertionError('Synthetic local inspection reader failed')

    parent.runner.submit('Inspection locale synthétique',lambda:open_local_inspection(service,identity_id='a'),opened,failed,kind='maintenance-inspection')
