"""#6937: capacity counts and physical stock remain distinct, no mutation."""
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core import derived_variant_capacity_review as capacity
from cifereca.core import derived_variant_journal as journal
from cifereca.core.derived_variant_maintenance_review import admit_maintenance_review
from tests.test_core_p0_4520_recovery_operator import _setup
from tests.test_core_p0_4520_derived_journal import _publish_reserved


def _review(service):
    request = admit_maintenance_review(service)
    assert request is not None
    return capacity.review_derived_capacity(service, request)


def test_capacity_distinguishes_logical_and_named_physical_stock(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        _publish_reserved(db, intent, reservation)
        journal.reserve_derived_publication(db, replace(
            intent, candidate_path=str(Path(intent.candidate_path).with_name("next.epub"))))
        before = journal.summarize_derived_journal(db)
        result = _review(service)
        assert result["status"] == "observed"
        assert result["installation"] == result["profile"]
        assert result["profile"]["committed"] == result["profile"]["pending"] == 1
        assert result["profile"]["reserved_output_bytes"] == 18
        assert result["profile_named_stock"]["observed_unique_inode_logical_bytes"] == 9
        assert result["scope_unverified_receipts"]["records"] == 0
        assert not result["quota_refund_authorized"] and not result["retention_authorized"]
        assert not result["physical_installation_usage_known"]
        assert str(tmp_path) not in repr(result) and reservation.key not in repr(result)
        assert journal.summarize_derived_journal(db) == before
    finally:
        db.close()


def test_unbound_historical_receipt_is_still_charged(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        orphan = replace(intent, identity_id="obsolete")
        raw = journal._encode({
            "schema": "derived.intent.v2", "nonce": "a"*32, "intent": asdict(orphan)})
        db.metadata_set(journal._slot(orphan), raw)
        result = _review(service)
        assert result["status"] == "observed"
        assert result["installation"]["records"] == 2
        assert result["profile"]["records"] == result["scope_unverified_receipts"]["records"] == 1
        assert result["installation"]["reserved_output_bytes"] == 18
        assert not result["per_profile_quota"]
        assert "obsolete" not in repr(result)
    finally:
        db.close()


@pytest.mark.parametrize("maximum,expected", [(9, "limit_reached"), (8, "over_capacity")])
def test_saturation_does_not_refund_or_evict(tmp_path, monkeypatch, maximum, expected):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        monkeypatch.setattr(journal, "_MAX_RESERVED_OUTPUT_BYTES", maximum)
        before = db.metadata_get(reservation.key)
        report = _review(service)
        assert report["status"] == expected
        assert report["remaining_operational"]["reserved_output_bytes"] == 0
        assert report["reservation_authorized"] is False
        assert db.metadata_get(reservation.key) == before
    finally:
        db.close()


@pytest.mark.parametrize("change", ["corrupt", "epoch", "journal"])
def test_unknown_or_changed_snapshot_never_returns_remaining_quota(tmp_path, monkeypatch, change):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        if change == "corrupt":
            db.metadata_set(reservation.key, "{}")
        else:
            original = capacity.review_derived_staging_stock
            def changing(*a, **kw):
                result = original(*a, **kw)
                if change == "epoch":
                    service._account_context_epoch += 2
                else:
                    _publish_reserved(db, intent, reservation)
                return result
            monkeypatch.setattr(capacity, "review_derived_staging_stock", changing)
        result = _review(service)
        assert result["status"] in ("unavailable", "evidence_changed")
        assert result["remaining_operational"] is None
        assert result["profile_named_stock"] is None
    finally:
        db.close()


@pytest.mark.parametrize("stale", [False, True])
def test_native_capacity_action_is_explicit_and_revocable(tmp_path, stale):
    from cifereca.gui.derived_variant_capacity_line import add_derived_capacity_line
    from tests.test_core_ui_1358_maintenance_preview_cancellation import Button, Container
    service, intent, reservation = _setup(tmp_path)
    try:
        jobs = []
        host = NS(service=service, _closed=False, _route_display_token=object(),
                  action_button=lambda label, _: Button(label),
                  runner=NS(submit=lambda *args, **kw: jobs.append(args)))
        group = Container()
        assert add_derived_capacity_line(host, group, Adw=NS(ActionRow=Container))
        row, = group.children
        button, = row.children
        assert not jobs
        button.click()
        button.click()
        assert len(jobs) == 1
        value = jobs[0][1]()
        if stale:
            host._route_display_token = object()
        jobs[0][2](value)
        if stale:
            assert "révoquée" in row.subtitle
        else:
            assert "9 octets logiques réservés" in row.subtitle
            assert "Ni réservation, ni remboursement" in row.subtitle
        assert len(row.children) == 1
        assert service.storage.metadata_get(reservation.key) is not None
    finally:
        service.storage.close()
