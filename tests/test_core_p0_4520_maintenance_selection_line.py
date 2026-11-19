"""#4520: the GTK receipt list remains private, explicit and non-mutating."""
from types import SimpleNamespace as NS

from cifereca.gui import derived_variant_selection_line as gui
from tests.test_core_p0_4520_recovery_operator import _setup
from tests.test_core_ui_1358_maintenance_preview_cancellation import Button, Container


class Group(Container):
    def remove(self, row):
        self.children.remove(row)


class Row(Container):
    def set_use_markup(self, value):
        self.markup = value


def _mount(service):
    jobs = []
    host = NS(service=service, _closed=False, _route_display_token=object(),
              action_button=lambda label, _icon: Button(label),
              runner=NS(submit=lambda *args, **kw: jobs.append((args, kw))))
    box = Container()
    gui.add_derived_variant_selection_line(host, box, Adw=NS(PreferencesGroup=Group, ActionRow=Row))
    return host, jobs, box.children[0]


def _complete(jobs):
    args, _kw = jobs.pop(0)
    args[2](args[1]())


def test_private_explicit_review_never_writes(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    try:
        before = service.storage.metadata_get(reservation.key)
        host, jobs, group = _mount(service)
        assert not jobs
        start, = group.children[0].children
        start.click()
        _complete(jobs)
        header, receipt = group.children
        assert receipt.markup is False
        assert "non vérifiés" in receipt.subtitle
        assert reservation.key not in receipt.subtitle
        verify, = receipt.children
        verify.click()
        _complete(jobs)
        assert "Lien physique vérifié" in receipt.subtitle
        assert "aucune reprise effectuée" in receipt.subtitle
        assert service.storage.metadata_get(reservation.key) == before
        assert len(service.storage.placements()) == 1
        assert not jobs
    finally:
        service.storage.close()


def test_route_change_drops_page_and_stale_choice(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    try:
        host, jobs, group = _mount(service)
        start, = group.children[0].children
        start.click()
        _complete(jobs)
        verify, = group.children[1].children
        host._route_display_token = object()
        verify.click()
        assert len(group.children) == 1 and not jobs
        start.click()
        args, _kw = jobs.pop()
        page = args[1]()
        host._route_display_token = object()
        args[2](page)
        assert len(group.children) == 1
        assert "Contexte modifié" in group.children[0].subtitle
    finally:
        service.storage.close()
