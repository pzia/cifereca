"""GTK callback contract with real Core/SQLite; native GTK is covered by E2E."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.library_declaration_actions import DeclarationActions
from tests.test_library_declared_link_inspection import selected
from tests.test_library_declared_links import rig, records, rows


class Widget:
    def __init__(self, **options):
        self.label = options.get('label', '')
        self.visible, self.sensitive, self.expanded = True, True, False
        self.children, self.signals = [], {}

    def set_visible(self, value): self.visible = value
    def set_sensitive(self, value): self.sensitive = value
    def set_expanded(self, value): self.expanded = value
    def set_wrap(self, value): pass
    def set_selectable(self, value): pass
    def set_tooltip_text(self, value): pass
    def set_text(self, value): self.label = value
    def set_child(self, value): self.children = [value]
    def append(self, value): self.children.append(value)
    def connect(self, signal, callback): self.signals[signal] = callback


class Dialog(Widget):
    dialogs = []

    @classmethod
    def new(cls, parent, title, body):
        result = cls(label=title)
        result.body = body
        cls.dialogs.append(result)
        return result

    def set_body_use_markup(self, value): self.markup = value
    def add_response(self, name, label): pass
    def set_default_response(self, response): self.default = response
    def set_close_response(self, response): self.close_response = response
    def present(self): pass
    def respond(self, response): self.signals['response'](self, response)


class Runner:
    deferred = False

    def __init__(self): self.queue = []
    def submit(self, title, work, completed, failed, **options):
        self.queue.append((work, completed, failed))
        if not self.deferred:
            self.run()

    def run(self):
        work, completed, failed = self.queue.pop(0)
        try:
            result = work()
        except Exception as error:
            failed(error)
        else:
            completed(result)


@pytest.fixture
def ui(selected):
    session, right, file, _, _ = selected
    Dialog.dialogs = []
    pair = [right, file]
    runner = Runner()
    controls = DeclarationActions(
        Gtk=NS(Expander=Widget, Box=Widget, Label=Widget, Button=Widget,
               Orientation=NS(VERTICAL='vertical')),
        Adw=NS(MessageDialog=Dialog), window=object(), runner=runner,
        session=session, selection=lambda: tuple(pair), alive=lambda: True,
    )
    return controls, pair, runner


def test_private_diff_and_explicit_consent_then_distinct_revocation(ui, selected):
    controls, _, _ = ui
    service, rig = selected[3], selected[4]
    assert controls.declare.sensitive and not controls.revoke.visible
    controls.preview(False)
    dialog = Dialog.dialogs[-1]
    assert dialog.default == dialog.close_response == 'cancel'
    assert dialog.markup is False
    for value in (str(rig[1]), rig[4]['account_id'], rig[4]['item_id'], rig[4]['provider_id']):
        assert value in dialog.body
    assert 'déclaration utilisateur' in dialog.body
    assert rig[4]['observation'].sha256 not in dialog.body
    assert records(service) == []
    dialog.respond('cancel')
    assert records(service) == []
    controls.preview(False)
    Dialog.dialogs[-1].respond('apply')
    assert len(records(service)) == 1
    assert controls.revoke.visible and 'déclaration utilisateur' in controls.status.label
    assert not rows(service, 'placements')
    controls.preview(True)
    assert 'révoquer uniquement' in Dialog.dialogs[-1].body
    assert records(service)[0]['state'] == 'active'
    Dialog.dialogs[-1].respond('apply')
    assert records(service)[0]['state'] == 'revoked'
    assert 'historique conservé' in controls.status.label


def test_changed_selection_or_closed_session_cannot_confirm_dialog(ui, selected):
    controls, pair, _ = ui
    controls.preview(False)
    dialog = Dialog.dialogs[-1]
    pair[1] = None
    controls.reset()
    dialog.respond('apply')
    assert records(selected[3]) == []
    pair[1] = selected[2]
    controls.reset()
    controls.preview(False)
    selected[0].close()
    Dialog.dialogs[-1].respond('apply')
    assert records(selected[3]) == []


def test_no_dialog_or_write_from_late_preview_after_selection_change(ui, selected):
    controls, pair, runner = ui
    runner.deferred = True
    controls.preview(False)
    assert not Dialog.dialogs
    pair[1] = None
    controls.reset()
    runner.run()
    assert not Dialog.dialogs and records(selected[3]) == []


def test_existing_declaration_is_labelled_without_automatic_new_confirmation(ui, selected):
    controls, _, _ = ui
    controls.preview(False)
    Dialog.dialogs[-1].respond('apply')
    count = len(Dialog.dialogs)
    controls.preview(False)
    assert len(Dialog.dialogs) == count
    assert 'déjà déclarée' in controls.status.label
    assert 'Fichier local revérifié' in controls.status.label
    assert 'n’atteste ni achat' in controls.status.label
    assert controls.revoke.visible
    service, rig = selected[3], selected[4]
    service.storage.entitlement_put({**rig[3], 'state': 'expired'})
    controls.preview(False)
    assert len(Dialog.dialogs) == count
    assert 'nouvelle vérification' in controls.status.label
    assert 'Aucune présence locale confirmée' in controls.status.label
    assert controls.revoke.visible


def test_failed_preview_does_not_show_raw_exception_or_confirmation(ui, selected):
    controls, _, _ = ui
    selected[4][1].unlink()
    controls.preview(False)
    assert not Dialog.dialogs
    assert 'fichier a changé' in controls.status.label
    assert 'Relancez l’inspection' in controls.status.label
    assert 'Aucune écriture' in controls.status.label
    assert 'PRIVATE' not in controls.status.label
    assert records(selected[3]) == []
