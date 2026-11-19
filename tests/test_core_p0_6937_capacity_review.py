"""#6937: capacity observation over real private receipts and isolated SQLite/FS."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core import derived_variant_capacity_review as core
from cifereca.core import derived_variant_journal as journal
from cifereca.core.derived_variant_maintenance_review import admit_maintenance_review
from cifereca.gui import derived_variant_capacity_line as gui
from tests.test_core_p0_4520_recovery_operator import _setup
from tests.test_core_ui_1358_maintenance_preview_cancellation import Button, Container


def _read(service):
    return core.read_capacity_review(service, admit_maintenance_review(service))


def test_capacity_and_physical_stock_separate(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        monkeypatch.setattr(journal, "_MAX_RESERVED_OUTPUT_BYTES", intent.output_size)
        before = db.metadata_get(reservation.key)
        result = _read(service)
        assert result["status"] == "observed"
        assert result["counts"] == {
            "pending": 1, "committed": 0, "unreadable": 0, "unbound": 0,
            "active_pending": 1, "active_committed": 0, "other_bound": 0,
        }
        assert result["record_count"] == 1
        assert result["known_output_bytes"] == result["active_output_bytes"] == intent.output_size
        assert result["payload_bytes"] == result["active_payload_bytes"] == len(before.encode("utf-8"))
        assert result["saturation"] == "at_limit"
        stock = result["stock"]
        assert stock["observations"]["same_inode_target"] == 1
        assert stock["observed_unique_inode_logical_bytes"] == intent.output_size
        assert stock["complete_physical_inventory"] is False and stock["sha_verified"] is False
        assert result["quota_refund_authorized"] is False
        assert "Plafond atteint" in gui._subtitle(result)
        assert str(tmp_path) not in repr(result)
        assert reservation.key not in repr(result)
        assert db.metadata_get(reservation.key) == before
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


def test_unbound_and_corrupt_receipts_are_not_free_capacity(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        service.config.identities = lambda: [
            {"id": "id1", "directory_name": "library"},
        ]
        # Synthetic detached receipt, exact format but no configured owner.
        foreign = replace(intent, identity_id="retired", parent_placement_id="other")
        from dataclasses import asdict
        raw = journal._encode({"schema": "derived.intent.v2", "nonce": "a"*32,
                               "intent": asdict(foreign)})
        key = journal._slot(foreign)
        db.metadata_set(key, raw)
        result = _read(service)
        assert result["status"] == "partial" and result["counts"]["unbound"] == 1
        assert result["known_output_bytes"] == 2 * intent.output_size
        assert result["active_output_bytes"] == intent.output_size
        db.metadata_set(key, "not valid")
        result = _read(service)
        assert result["status"] == "partial" and result["counts"]["unreadable"] == 1
        assert result["saturation"] == "unknown" and not result["output_bytes_complete"]
        assert result["known_output_bytes"] == intent.output_size
        assert result["stock"]["status"] == "unavailable"
        assert "indisponible" in gui._subtitle(result)
        assert db.metadata_get(key) == "not valid"
        assert db.metadata_get(reservation.key) is not None
    finally:
        db.close()


def test_scope_or_snapshot_change_discards_result(tmp_path, monkeypatch):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    original = core.review_derived_staging_stock

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        db.metadata_set("synthetic.concurrent-change", "1")
        return result

    monkeypatch.setattr(core, "review_derived_staging_stock", changed)
    try:
        assert _read(service) is None
    finally:
        db.close()


@pytest.mark.parametrize("stale", [False, True])
def test_native_capacity_line_click_and_duplicate_delivery(tmp_path, stale):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        jobs = []
        host = NS(service=service, _closed=False, _route_display_token=object(),
                  action_button=lambda label, _icon: Button(label),
                  runner=NS(submit=lambda *args, **kw: jobs.append(args)))
        box = Container()
        gui.add_derived_variant_capacity_line(host, box, Adw=NS(PreferencesGroup=Container,
                                                                ActionRow=Container))
        row = box.children[0].children[0]
        button, = row.children
        assert not jobs
        button.click()
        button.click()
        assert len(jobs) == 1
        value = jobs[0][1]()
        if stale:
            host._route_display_token = object()
        jobs[0][2](value)
        initial = row.subtitle
        jobs[0][2](None)
        assert row.subtitle == initial
        assert ("Capacité indisponible" if stale else "aucun octet récupérable attesté") in initial
        assert db.metadata_get(reservation.key) is not None
    finally:
        db.close()
