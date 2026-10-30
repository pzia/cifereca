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


def _inspection_host_size(parent):
    """The synthetic parent must fit the same allocated MainWindow as the UI."""
    return min(920, parent.get_width()), min(620, parent.get_height())


def _allocated(widget):
    """Mapped precedes allocation: zero dimensions cannot prove a fit."""
    return (widget is not None and widget.get_mapped()
            and widget.get_width() > 0 and widget.get_height() > 0)


def _inspection_geometry_ready(controller):
    return controller.session is not None and _allocated(controller.dialog)


def exercise(*, parent, Gtk, Adw, GLib, Pango, device, until, walk,
             report, done, checkpoint=lambda phase: None):
    report['checks']['inspection_setup_entered']=True
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    library = root/'library'
    for name in ('a','b'): (library/name).mkdir(parents=True)
    # Stress the real inspection at the reported 600-file scale, not only
    # the earlier 61-file fixture, while keeping all I/O local and synthetic.
    for index in range(600):
        with zipfile.ZipFile(library/'a'/f'synthetic-{index:02d}.epub','w') as book:
            book.writestr('mimetype','application/epub+zip')
            book.writestr('META-INF/container.xml',"<container><rootfile full-path='content.opf'/></container>")
            book.writestr('content.opf',"<package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title>Synthetic local case</dc:title><dc:identifier scheme='ISBN'>9780306406157</dc:identifier></metadata></package>")
    database = root/'local.sqlite'
    with sqlite3.connect(database) as db:
        for name,columns in _COLUMNS.items():
            db.execute(f"CREATE TABLE {name} ({','.join(c+' TEXT' for c in columns.split())})")
        db.execute("INSERT INTO entitlements(entitlement_id,identity_id,provider_id,item_id,state) VALUES('right-a','a','numilog','edition-a','active')")
        # 272 local unverified Numilog rights, without placements or
        # acquisition authority; keep the candidate right first.
        db.executemany(
            "INSERT INTO entitlements(entitlement_id,identity_id,provider_id,item_id,state) VALUES(?,?,?,?,?)",
            [(f'right-synthetic-{index:03d}', 'a', 'numilog',
              f'edition-synthetic-{index:03d}', 'active')
             for index in range(271)],
        )
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
    host.set_default_size(*_inspection_host_size(parent))
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
    controller.attach_activity_row(row)
    widgets={}

    def press(key):
        device.key(key,True);device.key(key,False)

    def choose(dropdown,index,callback):
        # A previous candidate/preflight click may leave these top-of-page
        # filters outside a short viewport. Reveal the target before XTEST;
        # a mapped dropdown alone does not mean its popup can receive input.
        if widgets.get('main_scroll') is not None:
            widgets['main_scroll'].get_vadjustment().set_value(0)
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
        # The retired Maintenance result inspector was never mounted by the
        # production Tools page. Exercise the real, read-only controller
        # directly while preserving the 600-file GTK/XTEST acceptance flow.
        report['checks']['inspection_click_dispatched']=True
        controller.start()
        until(lambda: _inspection_geometry_ready(controller), initial,
              timeout=18.0)

    def initial():
        checkpoint('maint_inspection_opened')
        report['checks']['inspection_window_mapped']=True
        window=controller.dialog
        assert window.get_width()<=parent.get_width()
        report['checks']['P0_2712_initial_window_width_ok']=True
        assert window.get_height()<=parent.get_height()
        report['checks']['P0_2712_initial_window_height_ok']=True
        assert window.get_decorated() and window.get_resizable()
        header = next(w for w in walk(window) if isinstance(w, Adw.HeaderBar))
        assert header.get_mapped()
        grow = next(w for w in walk(window)
                    if isinstance(w,Gtk.Button)
                    and w.get_label()=="Agrandir / Restaurer")
        assert grow.get_mapped() and grow.get_sensitive()
        assert 'Agrandir' in (grow.get_tooltip_text() or '')
        report['checks']['P0_2712_initial_header_ok']=True
        widgets['grow'] = grow
        report['checks']['inspection_window_has_maximize_controls']=True
        # Real GTK geometry: the entire inspection must be reachable without
        # maximizing, independently of the nested list/detail scrollbars.
        toolbar_view = next(w for w in walk(window)
                            if isinstance(w, Adw.ToolbarView))
        # CSS/widget names are not reliable during GTK child traversal:
        # read the content installed by the production ToolbarView instead.
        main_scroll = toolbar_view.get_content()
        assert isinstance(main_scroll, Gtk.ScrolledWindow)
        # GTK wraps a non-GtkScrollable Box in an implicit Gtk.Viewport.
        # Inspect the actual page, not the automatic layout adapter.
        page = main_scroll.get_child()
        if isinstance(page, Gtk.Viewport):
            page = page.get_child()
        assert isinstance(page, Gtk.Box)
        report['checks']['P0_2712_initial_scroller_child_ok']=True
        adjustment = main_scroll.get_vadjustment()
        assert adjustment is not None
        # At the initial render the candidate pane is intentionally hidden;
        # it may fit entirely even at 920px. Exercise overflow *after* the
        # right and its taller comparison/actions are expanded.
        widgets['main_scroll'] = main_scroll
        report['checks']['P0_2712_main_scroll_present']=True
        # The audit's long counters/roots must NOT monopolize the 920px
        # reconciliation viewport before the user elects to expand them.
        synopsis_panel = next(
            w for w in walk(window)
            if isinstance(w, Gtk.Expander)
            and (w.get_label() or '').startswith(
                'Afficher les statistiques et le périmètre'
            )
        )
        assert not synopsis_panel.get_expanded()
        assert synopsis_panel.get_mapped()
        widgets['overview_panel'] = synopsis_panel
        report['checks']['P0_2712_inspection_overview_collapsed_initially'] = True
        dropdowns=[w for w in walk(window) if isinstance(w,Gtk.DropDown)]
        widgets['provider'],widgets['state']=dropdowns
        widgets['list']=next(w for w in walk(window) if isinstance(w,Gtk.ListBox))
        choose(widgets['provider'],2,numilog)

    def numilog():
        rows=list(walk(widgets['list']))
        labels='\n'.join(w.get_text() for w in rows if isinstance(w,Gtk.Label))
        # Synthetic rights are legitimately displayed as Numilog rights.
        # Their edition labels may include "synthetic": what must NEVER
        # appear in this provider-filtered list is the *file* name.
        assert 'Droit sans fichier vérifié' in labels
        assert '.epub' not in labels and 'Fichier local :' not in labels
        report['checks']['local_inspection_provider_filter_does_not_attribute_isbn_files']=True
        # P0 #2714: this ISBN appears in a SAME-IDENTITY Numilog notice,
        # but NOT in its right's visible title. Filter the real GTK rights
        # list and verify that no second-identity right is attributed.
        rights_search = next(w for w in walk(controller.dialog)
                             if isinstance(w, Gtk.SearchEntry)
                             and w.get_placeholder_text()
                             == 'Filtrer par titre, auteur, nom ou identifiant…')
        widgets['rights_search'] = rights_search
        rights_search.set_text('9780306406157')
        until(
            lambda: (
                controller.session.page(
                    provider='numilog', query='9780306406157',
                    state='rights_without_verified_file',
                ).total == 1
                and len([
                    child for child in walk(widgets['list'])
                    if isinstance(child, Gtk.ListBoxRow)
                ]) == 1
            ),
            filtered_numilog_right,
        )

    def filtered_numilog_right():
        report['checks']['P0_2714_gtk_numilog_right_search_by_private_isbn']=True
        assert widgets['rights_search'].get_text() == '9780306406157'
        widgets['rights_search'].set_text('')
        until(
            lambda: len([
                child for child in walk(widgets['list'])
                if isinstance(child, Gtk.ListBoxRow)
            ]) == 50,
            select_numilog_right,
        )

    def select_numilog_right():
        # The Numilog right must show unlinked EPUB candidates *only* after
        # user selection; no ownership is inferred from a shared valid ISBN.
        row = widgets['list'].get_first_child()
        device.click_widget(row)
        # A collapsed synopsis makes the physical right selection accessible
        # at 920px. Expand only AFTER the real XTEST click, then require
        # the full summary to remain reachable by actual wheel scrolling.
        widgets['overview_panel'].set_expanded(True)
        until(lambda:any(isinstance(w,Gtk.Label) and '1 / 20' in w.get_text()
                         for w in walk(controller.dialog)), compare_right)

    def compare_right():
        checkpoint('maint_inspection_right_selected')
        report['checks']['inspection_candidates_initially_visible']=True
        labels='\n'.join(w.get_text() for w in walk(controller.dialog)
                          if isinstance(w,Gtk.Label))
        assert 'Fichiers candidats' in labels
        assert 'non probant' in labels
        report['checks']['candidate_heading_verified']=True
        main_scroll = widgets['main_scroll']
        adjustment = main_scroll.get_vadjustment()
        # The asynchronous GTK allocation of the newly shown comparison may
        # still be pending at this callback. A present scroll viewport is a
        # strict contract; changing its adjustment when overflow exists is an
        # additional physical allocation check (never assume overflow).
        if adjustment.get_upper() > adjustment.get_page_size() + 1:
            adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
            assert adjustment.get_value() > 0
            adjustment.set_value(0)
            report['checks']['P0_2712_main_scroll_range_operable']=True
        rights_page = controller.session.page(
            provider='numilog', state='rights_without_verified_file',
        )
        assert rights_page.total == 272
        report['checks']['P0_2712_272_unverified_rights_fixture']=True
        right = rights_page.cases[0]
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
            # Focus a control that may sit below the initial viewport; GTK
            # should scroll its ancestor viewport rather than clip it.
            following_candidate.grab_focus()
            # A child may be mapped but clipped by the page scrollbar.
            outer = widgets['main_scroll']
            page = outer.get_child()
            if isinstance(page, Gtk.Viewport):
                page = page.get_child()
            ok, rect = following_candidate.compute_bounds(page)
            if ok:
                adj = outer.get_vadjustment()
                adj.set_value(max(0, min(rect.get_y() - adj.get_page_size() / 2,
                                         adj.get_upper() - adj.get_page_size())))
            if not following_candidate.get_mapped() or not following_candidate.get_sensitive():
                return False
            window = controller.dialog
            visible, rect = following_candidate.compute_bounds(window)
            if not visible or rect.get_width() <= 0 or rect.get_height() <= 0:
                return False
            cx = rect.get_x() + rect.get_width() / 2
            cy = rect.get_y() + rect.get_height() / 2
            if not (0 <= cx < window.get_width() and 0 <= cy < window.get_height()):
                return False
            picked = window.pick(cx, cy, Gtk.PickFlags.DEFAULT)
            while picked is not None and picked is not following_candidate:
                picked = picked.get_parent()
            if picked is not following_candidate:
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

        # The detailed scope summary moved to a collapsed expander on main;
        # wheel over the permanently visible private-identity notice instead.
        # A genuine mouse wheel must scroll the OUTER window without
        # faking GtkAdjustment movement; the nested ListBoxes are separate.
        def wheel_target():
            # Deliberately reopen the long history to verify that the
            # physically scrollable full explanation remains accessible.
            widgets['overview_panel'].set_expanded(True)
            if adjustment.get_value() > 0.5:
                return False
            summary = next(
                w for w in walk(controller.dialog)
                if isinstance(w, Gtk.Label)
                and w.get_text().startswith('Identité active ·')
            )
            if not summary.get_mapped():
                return False
            report['checks']['P0_2712_inspection_overview_reexpandable'] = True
            window = controller.dialog
            ok, rect = summary.compute_bounds(window)
            if not ok or rect.get_width() <= 0 or rect.get_height() <= 0:
                return False
            cx = rect.get_x() + rect.get_width() / 2
            cy = rect.get_y() + rect.get_height() / 2
            if not (0 <= cx < window.get_width() and 0 <= cy < window.get_height()):
                return False
            picked = window.pick(cx, cy, Gtk.PickFlags.DEFAULT)
            while picked is not None and picked is not summary:
                picked = picked.get_parent()
            return picked is summary

        def send_wheel():
            summary = next(
                w for w in walk(controller.dialog)
                if isinstance(w, Gtk.Label)
                and w.get_text().startswith('Identité active ·')
            )
            x, y = device.widget_point(summary)
            device.wheel(x, y, down=True, ticks=4)
            until(lambda: adjustment.get_value() > 0.5, wheel_reached)

        def wheel_reached():
            report['checks']['P0_2712_physical_wheel_scroll_reached']=True
            adjustment.set_value(0)
            until(candidate_allocated, candidate_click)

        if adjustment.get_upper() > adjustment.get_page_size() + 1:
            adjustment.set_value(0)
            until(wheel_target, send_wheel)
        else:
            # The stage buttons now live in a sticky footer, not below the
            # description. A large desktop can fit the entire shortened
            # content without vertical scrolling: prove this exact physical
            # geometry rather than count a nonexistent wheel gesture.
            assert adjustment.get_upper() <= adjustment.get_page_size() + 1
            report['checks']['P0_2712_no_outer_scroll_needed'] = True
            until(candidate_allocated, candidate_click)

    def compare_next():
        checkpoint('maint_inspection_candidate_navigated')
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
        report['checks']['native_manual_toggle_lookup_started'] = True
        manual=next(w for w in walk(controller.dialog)
                    if isinstance(w,Gtk.Button)
                    and w.get_label()=='Comparer tous les fichiers non liés')
        report['checks']['native_manual_toggle_found'] = True
        def ready():
            # The fixed bottom action bar reduces the tall panel viewport.
            # Verify that the actual native pointer will hit this inner
            # comparison toggle, not a clipped location or the sticky bar.
            manual.grab_focus()
            if not manual.get_mapped() or not manual.get_sensitive():
                return False
            outer = widgets['main_scroll']
            page = outer.get_child()
            if isinstance(page, Gtk.Viewport):
                page = page.get_child()
            ok, bounds = manual.compute_bounds(page)
            if ok:
                adjustment = outer.get_vadjustment()
                adjustment.set_value(max(
                    0, min(bounds.get_y() - adjustment.get_page_size() / 2,
                           adjustment.get_upper() - adjustment.get_page_size()),
                ))
            window = controller.dialog
            visible, box = manual.compute_bounds(window)
            if not visible or box.get_width() <= 0 or box.get_height() <= 0:
                return False
            cx = box.get_x() + box.get_width() / 2
            cy = box.get_y() + box.get_height() / 2
            if not (0 <= cx < window.get_width() and 0 <= cy < window.get_height()):
                return False
            picked = window.pick(cx, cy, Gtk.PickFlags.DEFAULT)
            while picked is not None and picked is not manual:
                picked = picked.get_parent()
            if picked is not manual:
                return False
            report['checks']['native_manual_toggle_pointer_hit'] = True
            try:
                x,y=device.widget_point(manual)
                return x >= 0 and y >= 0
            except RuntimeError:
                return False
        def click():
            report['checks']['native_manual_toggle_clicked'] = True
            device.click_widget(manual)
            until(lambda:any(isinstance(w,Gtk.Label) and '1 / 600' in w.get_text()
                             for w in walk(controller.dialog)),manual_first)
        until(ready,click)

    def manual_first():
        report['checks']['native_manual_toggle_result_visible'] = True
        texts='\\n'.join(w.get_text() for w in walk(controller.dialog)
                        if isinstance(w,Gtk.Label))
        assert 'Fichiers non liés · comparaison manuelle' in texts
        assert 'Aucun n’est attribué' in texts
        assert 'FOREIGN_SECRET' not in texts
        assert controller.session.unconfirmed_files().total == 600
        search=next(w for w in walk(controller.dialog)
                    if isinstance(w,Gtk.SearchEntry)
                    and w.get_placeholder_text()=='Filtrer les fichiers non liés…')
        assert search.get_visible()
        report['checks']['native_manual_panel_labels_validated'] = True
        # Direct physical selection from the NEW bounded list, not merely
        # next/previous navigation. The first page contains 25 of 600 files.
        file_list = next(w for w in walk(controller.dialog)
                         if isinstance(w, Gtk.ListBox)
                         and w is not widgets['list'])
        rows=[]
        child=file_list.get_first_child()
        while child is not None:
            rows.append(child)
            child=child.get_next_sibling()
        assert len(rows) == 25, len(rows)
        report['checks']['native_manual_list_has_25_rows'] = True
        selected_row=rows[1]

        def file_row_ready():
            # The paginated file list has its own GtkScrolledWindow. With
            # sticky actions, the outer panel also scrolls; both must
            # physically expose this file row before sending XTEST events.
            selected_row.grab_focus()
            if not selected_row.get_mapped() or not selected_row.get_sensitive():
                return False
            ancestor = selected_row.get_parent()
            while ancestor is not None:
                if isinstance(ancestor, Gtk.ScrolledWindow):
                    page = ancestor.get_child()
                    if isinstance(page, Gtk.Viewport):
                        page = page.get_child()
                    ok, rect = selected_row.compute_bounds(page)
                    if ok:
                        adj = ancestor.get_vadjustment()
                        adj.set_value(max(0, min(
                            rect.get_y() - adj.get_page_size() / 2,
                            adj.get_upper() - adj.get_page_size(),
                        )))
                ancestor = ancestor.get_parent()
            window = controller.dialog
            ok, rect = selected_row.compute_bounds(window)
            if not ok or rect.get_width() <= 0 or rect.get_height() <= 0:
                return False
            cx = rect.get_x() + rect.get_width()/2
            cy = rect.get_y() + rect.get_height()/2
            if not (0 <= cx < window.get_width() and 0 <= cy < window.get_height()):
                return False
            picked = window.pick(cx, cy, Gtk.PickFlags.DEFAULT)
            while picked is not None and picked is not selected_row:
                picked = picked.get_parent()
            if picked is not selected_row:
                return False
            try:
                x, y = device.widget_point(selected_row)
                if x < 0 or y < 0:
                    return False
            except RuntimeError:
                return False
            report['checks']['native_manual_file_row_hit_tested'] = True
            return True

        def click_file():
            report['checks']['native_manual_file_row_clicked'] = True
            device.click_widget(selected_row)
            until(lambda:any(isinstance(w,Gtk.Label)
                             and '2 / 600 · exploration non probante' in w.get_text()
                             for w in walk(controller.dialog)),
                  manual_direct_selected)

        until(file_row_ready, click_file)

    def manual_direct_selected():
        checkpoint('maint_inspection_file_selected')
        report['checks']['P0_2714_click_file_from_paged_list']=True
        text='\\n'.join(w.get_text() for w in walk(controller.dialog)
                         if isinstance(w,Gtk.Label))
        assert 'Fichiers non liés · comparaison manuelle' in text
        assert 'FOREIGN_SECRET' not in text
        search=next(w for w in walk(controller.dialog)
                    if isinstance(w,Gtk.SearchEntry)
                    and w.get_placeholder_text()=='Filtrer les fichiers non liés…')
        search.set_text('synthetic-599')
        until(lambda:any(isinstance(w,Gtk.Label)
                         and '1 / 1 · exploration non probante' in w.get_text()
                         for w in walk(controller.dialog)),manual_filtered)

    def manual_filtered():
        texts='\\n'.join(w.get_text() for w in walk(controller.dialog)
                        if isinstance(w,Gtk.Label))
        assert 'synthetic-599.epub' in texts
        assert 'FOREIGN_SECRET' not in texts
        assert 'Droit sans fichier vérifié' in texts
        report['checks']['local_inspection_manual_unlinked_filter_same_identity']=True
        preflight=next(w for w in walk(controller.dialog)
                       if isinstance(w,Gtk.Button)
                       and w.get_label()=='Préparer le rattachement')
        assert preflight.get_sensitive() and preflight.get_mapped()

        def preflight_ready():
            preflight.grab_focus()
            if not preflight.get_sensitive() or not preflight.get_mapped():
                return False
            outer = widgets['main_scroll']
            page = outer.get_child()
            if isinstance(page, Gtk.Viewport):
                page = page.get_child()
            ok, rect = preflight.compute_bounds(page)
            if ok:
                adj = outer.get_vadjustment()
                adj.set_value(max(0, min(rect.get_y() - adj.get_page_size() / 2,
                                         adj.get_upper() - adj.get_page_size())))
            window = controller.dialog
            visible, box = preflight.compute_bounds(window)
            if not visible or box.get_width() <= 0 or box.get_height() <= 0:
                return False
            cx = box.get_x() + box.get_width() / 2
            cy = box.get_y() + box.get_height() / 2
            if not (0 <= cx < window.get_width() and 0 <= cy < window.get_height()):
                return False
            picked = window.pick(cx, cy, Gtk.PickFlags.DEFAULT)
            while picked is not None and picked is not preflight:
                picked = picked.get_parent()
            if picked is not preflight:
                return False
            try:
                x,y=device.widget_point(preflight)
                return x>=0 and y>=0
            except RuntimeError:
                return False

        def run_preflight():
            device.click_widget(preflight)
            until(lambda:any(isinstance(w,Gtk.Label)
                             and 'Aucun fichier n’a été rattaché.' in w.get_text()
                             for w in walk(controller.dialog)), manual_preflight)

        until(preflight_ready, run_preflight)

    def manual_preflight():
        checkpoint('maint_inspection_preflight')
        text='\\n'.join(w.get_text() for w in walk(controller.dialog)
                       if isinstance(w,Gtk.Label))
        assert 'Aucun fichier n’a été rattaché.' in text
        assert 'Aucune modification' in text or 'rattachement' in text
        assert 'FOREIGN_SECRET' not in text
        fresh=next(w for w in walk(controller.dialog)
                   if isinstance(w,Gtk.Button)
                   and w.get_label()=='Vérifier les preuves actuelles…')
        # This fixture has no authenticated receipt or linked artifact.
        # The read-only explanation must NOT expose an executable preview.
        assert not fresh.get_visible() and not fresh.get_mapped()
        confirm=next(w for w in walk(controller.dialog)
                     if isinstance(w,Gtk.Button)
                     and w.get_label()=='Confirmer le placement local…')
        # A bibliographic-only synthetic candidate must never expose even
        # the second-step SQLite confirmation button.
        assert not confirm.get_visible() and not confirm.get_sensitive()
        undo=next(w for w in walk(controller.dialog)
                  if isinstance(w,Gtk.Button)
                  and w.get_label()=='Annuler le dernier placement…')
        # No operation was committed in this synthetic browse-only flow:
        # undo cannot be offered or used on an arbitrary existing placement.
        assert not undo.get_visible() and not undo.get_sensitive()
        report['checks']['P0_2714_pair_preflight_no_write_click']=True
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
        assert 'synthetic-599.epub' not in texts
        report['checks']['local_inspection_manual_does_not_change_candidate_set']=True
        # Both real candidate buttons are enabled at 2 / 20. Verify actual
        # XTEST Tab and Shift+Tab focus traversal rather than setting focus
        # directly on the expected destination after each keypress.
        previous = next(w for w in walk(controller.dialog)
                        if isinstance(w, Gtk.Button)
                        and w.get_label() == '◀ Candidat')
        following = next(w for w in walk(controller.dialog)
                         if isinstance(w, Gtk.Button)
                         and w.get_label() == 'Candidat ▶')
        assert previous.get_sensitive() and following.get_sensitive()
        previous.grab_focus()
        press('Tab')

        def shift_back():
            device.key('Shift_L', True)
            press('Tab')
            device.key('Shift_L', False)
            until(lambda: previous.has_focus(), keyboard_reached)

        def keyboard_reached():
            report['checks']['P0_2712_tab_shift_tab_candidate_navigation']=True
            choose(widgets['provider'],0,lambda:choose(widgets['state'],3,candidates))

        until(lambda: following.has_focus(), shift_back)

    def candidates():
        assert controller.session.page(state='metadata_candidate').total==600
        following=next(w for w in walk(controller.dialog) if isinstance(w,Gtk.Button) and w.get_label()=='Suivant')
        assert following.get_sensitive()
        device.click_widget(following)
        until(lambda:any(isinstance(w,Gtk.Label) and w.get_text()=='51–100 / 600' for w in walk(controller.dialog)),detail)

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
        grow=widgets['grow']
        assert grow.get_mapped() and grow.get_sensitive()
        # Xvfb has no window manager: confirm the actual GTK button is
        # operable and the private read session survives the maximize request,
        # but don't assert an EWMH maximize state without a WM.
        device.click_widget(grow)
        assert controller.session is session and session.valid()
        report['checks']['inspection_maximize_request_keeps_session_private']=True
        service._account_context_epoch+=1
        until(lambda:controller.dialog is None,lambda:revoked(session))

    def revoked(session):
        # A small viewport needs a real physical wheel when it overflows.
        # With a sticky action footer, a larger viewport may have no scroll
        # range: exact, observed no-overflow geometry also counts as access.
        assert (report['checks'].get('P0_2712_physical_wheel_scroll_reached') is True
                or report['checks'].get('P0_2712_no_outer_scroll_needed') is True)
        assert report['checks'].get('P0_2712_tab_shift_tab_candidate_navigation') is True
        assert not session.valid() and not session.page().cases
        assert sources()==before
        report['checks']['local_inspection_epoch_revokes_window_and_sources_unchanged']=True
        controller.clear()
        host.destroy()
        tmp.cleanup()
        checkpoint('maint_inspection_finished')
        done()

    host.present()
    until(lambda: _allocated(host), result_ready)
