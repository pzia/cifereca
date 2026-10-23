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


def exercise(*, parent, Gtk, Adw, GLib, Pango, device, until, walk, report, checkpoint, done):
    report['checks']['native_declaration_started'] = True
    checkpoint('maint_declaration_start')
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
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
    account = Account(display_name='Synthetic Numilog', unique_id='synthetic-account',
                      provider_id='numilog', credential_id='synthetic-credential',
                      provider_label='Numilog', identity_id='default')
    service.accounts = [account]
    service.storage.provider_connection_put({
        'identity_id': 'default', 'provider_id': 'numilog',
        'credential_id': account.credential_id, 'label': 'Synthetic',
    })
    service.storage.entitlement_put({
        'entitlement_id': 'synthetic-right', 'identity_id': 'default',
        'provider_id': 'numilog', 'credential_id': account.credential_id,
        'account_id': account.unique_id, 'item_id': 'synthetic-item',
        'state': 'active', 'entitlement_type': 'owned',
    })
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
        # The default list is paginated and starts with local files; the
        # synthetic Numilog right is deliberately not in the first 50 rows.
        # Switch via the actual GTK shortcut before physically selecting it.
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
        until(lambda: any(isinstance(w, Gtk.Label)
                          and 'Fichiers non liés · comparaison manuelle' in w.get_text()
                          for w in walk(controller.dialog)), file_view_visible)

    def file_view_visible():
        report['checks']['native_declaration_manual_files_visible'] = True
        # Distinguish an absent Core right/file context from GTK selection
        # reset. The result is boolean only; never publish account or path.
        files = session.unconfirmed_files(limit=10).cases
        assert len(files) == 1
        report['checks']['native_declaration_one_file_available'] = True
        assert session.declaration_context(right.key, files[0].key) is not None
        report['checks']['native_declaration_core_pair_admissible'] = True
        until(lambda: button('Déclarer ce lien…').get_sensitive(), expand)

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
        assert {table: rows(table) for table in unchanged} == unchanged
        assert hashlib.sha256(book.read_bytes()).hexdigest() == digest
        report['checks']['P0_2797_declaration_native_confirm_separate_trust'] = True
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
        controller.clear()
        host.destroy()
        service.close()
        tmp.cleanup()
        done()

    host.present()
    until(host.get_mapped, start)
