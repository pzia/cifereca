"""Native GTK/XTEST declaration consent with real offline Core and SQLite."""
from pathlib import Path
import hashlib
import json
import tempfile
import zipfile

from cifereca.config import Config
from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from cifereca.diagnostics import Diagnostics
from cifereca.gui.library_inspection_view import LocalInspectionController
from cifereca.models import Account
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _fixture(root, provider_id, *, attested=False):
    """One real SQLite/file scope, with existing evidence only in attested mode."""
    library = root / 'library'
    directory = library / 'declared'
    directory.mkdir(parents=True)
    book = directory / 'synthetic-declaration.epub'
    with zipfile.ZipFile(book, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml',
                        "<container><rootfile full-path='content.opf'/></container>")
        archive.writestr('content.opf',
                        "<package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
                        '<dc:title>Synthetic declaration</dc:title></metadata>'
                        "<manifest><item id='c' href='cover.png' properties='cover-image'/>"
                        '</manifest></package>')
        archive.writestr('cover.png', b'not needed')
    digest = hashlib.sha256(book.read_bytes()).hexdigest()
    cfg = Config(root / 'config.json')
    cfg.set('library.directory', str(library))
    cfg.set('identities', [{'id': 'default', 'directory_name': 'declared',
                            'label': 'Synthetic declaration', 'preferences': {}}])
    cfg.set('providers.accounts', [])
    service = LibraryService(cfg, Storage(root / 'local.sqlite'), Diagnostics())
    account = Account(display_name='Synthetic declaration source', unique_id='synthetic-account',
                      provider_id=provider_id, credential_id='synthetic-credential',
                      provider_label='Synthetic source', identity_id='default')
    service.accounts = [account]
    service.storage.provider_connection_put({
        'identity_id': 'default', 'provider_id': provider_id,
        'credential_id': account.credential_id, 'label': 'Synthetic',
    })
    if attested:
        # Historical acquisition before a manual move. A missing OLD path
        # is not an absent book: the exact same bytes are now at book.
        service.storage.backup_put({
            'identity_id': 'default', 'provider_id': 'kobo',
            'credential_id': account.credential_id, 'account_id': account.unique_id,
            'item_id': 'synthetic-item', 'revision_id': 'synthetic-revision',
            'title': 'Synthetic declaration', 'format': 'epub',
            'local_path': str(directory / 'kobo' / 'historical.epub'),
            'status': 'reconcile_required', 'size': book.stat().st_size,
            'sha256': digest, 'entitlement_type': 'owned',
        })
    else:
        service.storage.entitlement_put({
            'entitlement_id': 'synthetic-right', 'identity_id': 'default',
            'provider_id': provider_id, 'credential_id': account.credential_id,
            'account_id': account.unique_id, 'item_id': 'synthetic-item',
            'state': 'active', 'entitlement_type': 'owned',
        })
    return service, book, directory, digest


def exercise(*, parent, Gtk, Adw, GLib, Pango, device, until, walk, report,
             checkpoint, done, attested=False):
    # Keep the existing declaration path, then exercise attested placement
    # through the SAME production controller and separate native consent.
    provider_id = 'kobo' if attested else {
        920: 'test.vendor-a', 1024: 'test.vendor-b',
    }.get(report.get('width'), 'numilog')
    report['checks']['native_declaration_started'] = True
    checkpoint('maint_declaration_start')
    tmp = tempfile.TemporaryDirectory()
    service, book, directory, digest = _fixture(
        Path(tmp.name), provider_id, attested=attested,
    )
    # Quiescent fixture snapshot; WAL replay has its own Core tests. The
    # subsequent preview/commit runs with real reusable writer connections.
    service.storage.close()
    audit = audit_library(service.storage.path, {'default': (directory,)})
    report['checks']['native_declaration_audit_done'] = True
    checkpoint('maint_declaration_audit_done')
    assert audit.redacted()['status'] == 'complete', audit.redacted().get('reasons')
    session = LocalInspection(audit, lambda: True, 'default', roots=(directory,), service=service)
    right, = session.page(state='rights_without_verified_file').cases
    host = Adw.Window(transient_for=parent, modal=True, title='Synthetic declaration host')
    host.set_default_size(920, 620)
    host.service, host.runner = service, parent.runner
    host.set_content(Gtk.Label(label='Synthetic declaration host'))
    controller = LocalInspectionController(parent=host, Gtk=Gtk, Adw=Adw,
                                          Pango=Pango, GLib=GLib,
                                          alive=lambda: host.get_visible() and not parent._closed)

    def rows(table):
        with service.storage._lock, service.storage._connect() as db:
            return db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()

    unchanged = {table: rows(table) for table in (
        'entitlements', 'acquisition_ledger', 'placements', 'artifacts', 'backup_items')}

    def declarations():
        return [json.loads(value) for key, value in rows('metadata')
                if key.startswith('core.library.user-declaration.v1.')]

    def button(label, window=None):
        return next(w for w in walk(window or controller.dialog)
                    if isinstance(w, Gtk.Button) and w.get_label() == label)

    def guided_step():
        return next(
            w.get_text() for w in walk(controller.dialog)
            if isinstance(w, Gtk.Label)
            and w.get_name() == 'reconciliation-guided-step'
        )

    def click(widget, callback):
        def ready():
            if not widget.get_mapped() or not widget.get_sensitive():
                return False
            widget.grab_focus()
            ancestor = widget.get_parent()
            while ancestor is not None:
                if isinstance(ancestor, Gtk.ScrolledWindow):
                    page = ancestor.get_child()
                    if isinstance(page, Gtk.Viewport):
                        page = page.get_child()
                    ok, box = widget.compute_bounds(page)
                    if ok:
                        adj = ancestor.get_vadjustment()
                        adj.set_value(max(0, min(box.get_y() - adj.get_page_size() / 2,
                                                 adj.get_upper() - adj.get_page_size())))
                ancestor = ancestor.get_parent()
            window = widget.get_root()
            ok, box = widget.compute_bounds(window)
            if not ok or box.get_width() <= 0 or box.get_height() <= 0:
                return False
            x, y = box.get_x() + box.get_width()/2, box.get_y() + box.get_height()/2
            if not (0 <= x < window.get_width() and 0 <= y < window.get_height()):
                return False
            picked = window.pick(x, y, Gtk.PickFlags.DEFAULT)
            while picked is not None and picked is not widget:
                picked = picked.get_parent()
            return picked is widget

        until(ready, lambda: (device.click_widget(widget), callback()))

    def message():
        return next((w for w in Gtk.Window.list_toplevels()
                     if isinstance(w, Adw.MessageDialog) and w.get_visible()), None)

    def start():
        controller.session = session
        controller.present()
        until(lambda: controller.dialog.get_mapped(), select_right)

    def select_right():
        report['checks']['native_declaration_dialog_open'] = True
        checkpoint('maint_declaration_dialog_open')
        # P1 #2842 opens on unverified rights, not on an unrelated 50-item
        # inventory page. Keep exercising the explicit shortcut as well.
        assert guided_step().startswith('1/4')
        report['checks']['P1_2842_native_default_right_step'] = True
        click(button('Droits non vérifiés'), lambda: until(right_visible, click_right))

    def right_visible():
        return any(
            isinstance(w, Gtk.ListBoxRow)
            and getattr(w, '_local_case_key', None) == right.key
            for w in walk(controller.dialog)
        )

    def click_right():
        row = next(w for w in walk(controller.dialog)
                   if isinstance(w, Gtk.ListBoxRow)
                   and getattr(w, '_local_case_key', None) == right.key)
        report['checks']['native_declaration_right_row_found'] = True
        def chosen():
            report['checks']['native_declaration_right_row_clicked'] = True
            # Pointer dispatch alone does not establish GTK selection.
            # Wait for the actual selected row before checking Core eligibility.
            until(lambda: row.get_parent().get_selected_row() is row,
                  lambda: right_selected(row))
        click(row, chosen)

    def right_selected(row):
        report['checks']['native_declaration_right_is_selected'] = True
        if attested:
            until(lambda: button('Préparer le rattachement').get_visible(),
                  choose_attested_file)
            return
        until(lambda: any(isinstance(w, Gtk.Label)
                          and 'Fichiers non liés · comparaison manuelle' in w.get_text()
                          for w in walk(controller.dialog)), file_view_visible)

    def choose_attested_file():
        if any(isinstance(w, Gtk.Label)
               and 'Fichiers non liés · comparaison manuelle' in w.get_text()
               for w in walk(controller.dialog)):
            file_view_visible()
            return
        click(button('Comparer tous les fichiers non liés'),
              lambda: until(lambda: any(
                  isinstance(w, Gtk.Label)
                  and 'Fichiers non liés · comparaison manuelle' in w.get_text()
                  for w in walk(controller.dialog)), file_view_visible))

    def file_view_visible():
        report['checks']['native_declaration_manual_files_visible'] = True
        assert guided_step().startswith('3/4')
        assert 'lecture seule' in guided_step()
        report['checks']['P1_2842_native_pair_step_no_auto_write'] = True
        # Distinguish an absent Core right/file context from GTK selection
        # reset. The result is boolean only; never publish account or path.
        files = session.unconfirmed_files(limit=10).cases
        assert len(files) == 1
        report['checks']['native_declaration_one_file_available'] = True
        assert session.declaration_context(right.key, files[0].key) is not None
        report['checks']['native_declaration_core_pair_admissible'] = True
        if attested:
            row = next(w for w in walk(controller.dialog)
                       if isinstance(w, Gtk.ListBoxRow)
                       and getattr(w, '_manual_file_key', None) == files[0].key)
            click(row, lambda: until(
                lambda: row.get_parent().get_selected_row() is row,
                attested_prepare))
            return
        until(lambda: button('Déclarer ce lien…').get_sensitive(), expand)

    cancelled_placement = [False]

    def attested_prepare():
        assert service.storage.placement_for_path(book) is None
        assert {table: rows(table) for table in unchanged} == unchanged
        click(button('Préparer le rattachement'), lambda: until(
            lambda: button('Vérifier les preuves actuelles…').get_visible(),
            attested_revalidate))

    def attested_revalidate():
        assert guided_step().startswith('3/4')
        assert 'lecture seule' in guided_step()
        assert {table: rows(table) for table in unchanged} == unchanged
        click(button('Vérifier les preuves actuelles…'), lambda: until(
            lambda: button('Confirmer le placement local…').get_visible()
            and button('Confirmer le placement local…').get_sensitive(),
            attested_preview))

    def attested_preview():
        assert guided_step().startswith('4/4')
        assert {table: rows(table) for table in unchanged} == unchanged
        assert hashlib.sha256(book.read_bytes()).hexdigest() == digest
        report['checks']['P0_2842_native_attested_preview_no_write'] = True
        click(button('Confirmer le placement local…'), lambda: until(
            lambda: message() is not None, attested_consent))

    def attested_consent():
        assert {table: rows(table) for table in unchanged} == unchanged
        if not cancelled_placement[0]:
            cancelled_placement[0] = True
            click(button('Annuler', message()), lambda: until(
                lambda: message() is None, attested_cancelled))
            return
        click(button('Confirmer le placement', message()), lambda: until(
            lambda: service.storage.placement_for_path(book) is not None
            and guided_step().startswith('Placement enregistré'),
            attested_committed))

    def attested_cancelled():
        assert {table: rows(table) for table in unchanged} == unchanged
        report['checks']['P0_2842_native_attested_cancel_no_write'] = True
        attested_prepare()

    def attested_committed():
        placement = service.storage.placement_for_path(book)
        assert placement['identity_id'] == 'default'
        assert placement['provider_id'] == 'kobo'
        assert placement['source_item_id'] == 'synthetic-item'
        assert placement['artifact_id'] == 'sha256:' + digest
        assert placement['state'] == 'present'
        assert rows('entitlements') == unchanged['entitlements']
        assert rows('artifacts') == unchanged['artifacts']
        assert hashlib.sha256(book.read_bytes()).hexdigest() == digest
        assert not declarations()
        # CoreUI #3275: an attested placement is NOT a manually declared
        # right/file correspondence. The native detail must not reuse the
        # manual-consent success label for this independent path.
        visible_labels = '\\n'.join(
            w.get_text() for w in walk(controller.dialog)
            if isinstance(w, Gtk.Label) and w.get_mapped()
        )
        assert 'Correspondance enregistrée comme déclaration utilisateur' not in visible_labels
        assert guided_step().startswith('Placement enregistré')
        report['checks']['P1_3275_native_attested_not_mislabelled_manual'] = True
        report['checks']['P0_2842_native_attested_confirm_persists_placement'] = True
        until(lambda: button('Annuler le dernier placement…').get_visible()
              and button('Annuler le dernier placement…').get_sensitive(),
              attested_undo)

    def attested_undo():
        click(button('Annuler le dernier placement…'), lambda: until(
            lambda: message() is not None, attested_undo_consent))

    def attested_undo_consent():
        assert service.storage.placement_for_path(book) is not None
        click(button('Confirmer l’annulation', message()), lambda: until(
            lambda: service.storage.placement_for_path(book) is None
            and message() is not None
            and 'Annulation du placement confirmée' in message().get_body(),
            attested_finished))

    def attested_finished():
        assert {table: rows(table) for table in unchanged} == unchanged
        assert hashlib.sha256(book.read_bytes()).hexdigest() == digest
        report['checks']['P0_2842_native_attested_undo_restores_references'] = True
        click(button('Fermer', message()), lambda: until(
            lambda: message() is None, close_fixture))

    def expand():
        report['checks']['native_declaration_right_selected'] = True
        checkpoint('maint_declaration_right_selected')
        expander = next(w for w in walk(controller.dialog)
                        if isinstance(w, Gtk.Expander)
                        and w.get_label() == 'Association déclarée · sans preuve fournisseur')
        click(expander, lambda: until(expander.get_expanded, preview_cancel))

    def preview_cancel():
        report['checks']['native_declaration_expander_open'] = True
        checkpoint('maint_declaration_expander_open')
        click(button('Déclarer ce lien…'), lambda: until(lambda: message() is not None, cancel))

    def cancel():
        report['checks']['native_declaration_preview_dialog'] = True
        checkpoint('maint_declaration_preview_dialog')
        dialog = message()
        assert str(book) in dialog.get_body()
        assert 'synthetic-account' in dialog.get_body()
        assert provider_id in dialog.get_body()
        assert not declarations()
        report['checks']['P0_2797_declaration_native_preview_no_write'] = True
        click(button('Annuler', dialog), lambda: until(lambda: message() is None, cancelled))

    def cancelled():
        assert not declarations()
        report['checks']['P0_2797_declaration_native_cancel_no_write'] = True
        click(button('Déclarer ce lien…'), lambda: until(lambda: message() is not None, confirm))

    def confirm():
        click(button('Confirmer la déclaration', message()),
              lambda: until(lambda: len(declarations()) == 1, declared))

    def declared():
        report['checks']['native_declaration_committed'] = True
        checkpoint('maint_declaration_committed')
        assert declarations()[0]['origin'] == 'user_declaration'
        assert declarations()[0]['state'] == 'active'
        assert declarations()[0]['scope']['provider_id'] == provider_id
        assert {table: rows(table) for table in unchanged} == unchanged
        assert hashlib.sha256(book.read_bytes()).hexdigest() == digest
        report['checks']['P0_2797_declaration_native_confirm_separate_trust'] = True
        # The actual mapped GTK label, not only the SQLite declaration, must
        # show the lower trust level and the absence of a supplier receipt.
        def declared_label_visible():
            return any(
                isinstance(w, Gtk.Label) and w.get_mapped()
                and 'Correspondance enregistrée comme déclaration utilisateur' in w.get_text()
                and 'Aucun droit ou lien attesté créé' in w.get_text()
                and 'Déclaration révocable' in w.get_text()
                for w in walk(controller.dialog)
            )
        until(declared_label_visible, declared_label_checked)

    def declared_label_checked():
        report['checks']['P1_3275_native_declared_without_supplier_receipt'] = True
        until(lambda: button('Révoquer la déclaration…').get_visible(), revoke)

    def revoke():
        click(button('Révoquer la déclaration…'),
              lambda: until(lambda: message() is not None, revoke_confirm))

    def revoke_confirm():
        assert declarations()[0]['state'] == 'active'
        click(button('Révoquer la déclaration', message()),
              lambda: until(lambda: declarations()[0]['state'] == 'revoked', finish))

    def finish():
        report['checks']['native_declaration_revoked'] = True
        checkpoint('maint_declaration_revoked')
        assert {table: rows(table) for table in unchanged} == unchanged
        assert hashlib.sha256(book.read_bytes()).hexdigest() == digest
        assert declarations()[0]['revoked_at'] is not None
        report['checks']['P0_2797_declaration_native_revoke_keeps_history'] = True
        if provider_id in ('test.vendor-a', 'test.vendor-b'):
            report['checks']['P0_3171_native_opaque_provider_consent'] = True
        close_fixture()

    def close_fixture():
        controller.clear()
        host.destroy()
        service.close()
        tmp.cleanup()
        if attested:
            done()
        else:
            exercise(parent=parent, Gtk=Gtk, Adw=Adw, GLib=GLib, Pango=Pango,
                     device=device, until=until, walk=walk, report=report,
                     checkpoint=checkpoint, done=done, attested=True)

    host.present()
    until(host.get_mapped, start)
