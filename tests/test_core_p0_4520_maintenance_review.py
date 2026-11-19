"""#4520: real journal observations and a revocable read-only GTK line."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core import derived_variant_maintenance_review as core
from cifereca.gui import derived_variant_maintenance_line as gui
from tests.test_core_p0_4520_recovery_operator import _setup
from tests.test_core_ui_1358_maintenance_preview_cancellation import Button, Container


@pytest.mark.parametrize("linked", [True, False])
def test_read_only_pending_classification(tmp_path, linked):
    service, intent, reservation = _setup(tmp_path)
    try:
        if not linked:
            Path(intent.candidate_path).unlink()
        before = service.storage.metadata_get(reservation.key)
        request = core.admit_maintenance_review(service)
        report = core.read_maintenance_review(service, request)
        assert report["counts"]["linked_pending" if linked else "staged_only"] == 1
        assert report["checked"] == 1 and report["complete_pending_page"]
        assert not report["recovery_authorized"] and not report["destructive_cleanup_allowed"]
        assert reservation.key not in repr(report)
        assert service.storage.metadata_get(reservation.key) == before
        assert len(service.storage.placements()) == 1
    finally:
        service.storage.close()


def test_scope_changed_during_review_discards_counts(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    try:
        request = core.admit_maintenance_review(service)
        original = core.review_pending_derived_publication

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            service._source_context_epoch += 1
            return result

        monkeypatch.setattr(core, "review_pending_derived_publication", changed)
        assert core.read_maintenance_review(service, request) is None
    finally:
        service.storage.close()


@pytest.mark.parametrize("stale", [False, True])
def test_line_is_explicit_and_never_offers_recovery(tmp_path, stale):
    service, _intent, reservation = _setup(tmp_path)
    try:
        jobs = []
        host = NS(service=service, _closed=False, _route_display_token=object(),
                  action_button=lambda label, _icon: Button(label),
                  runner=NS(submit=lambda *args, **kw: jobs.append((args, kw))))
        box = Container()
        assert gui.add_derived_variant_maintenance_line(
            host, box, Gtk=NS(), Adw=NS(PreferencesGroup=Container, ActionRow=Container),
        )
        row = box.children[0].children[0]
        button, = row.children
        assert not jobs
        button.click()
        args, _kw = jobs[0]
        value = args[1]()
        initial = row.subtitle
        if stale:
            host._route_display_token = object()
        args[2](value)
        if stale:
            assert row.subtitle == initial
        else:
            assert "1 déjà lié(s)" in row.subtitle
            assert "Aucune reprise" in row.subtitle
        assert len(row.children) == 1 and len(jobs) == 1
        assert service.storage.metadata_get(reservation.key) is not None
    finally:
        service.storage.close()
